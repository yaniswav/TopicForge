"""Telemetry client and tool instrumentation.

Design notes:

- **Strict opt-in.** `enabled=False` makes `emit()` and `instrument()` true
  no-ops: no payload is assembled, no transport is invoked, no timing
  overhead is added to tool handlers. The OFF-means-no-network guarantee
  is the load-bearing property of this module and is tested explicitly.
- **No user payload.** Only `tool_name`, `latency_ms`, `mode`, `version`,
  `session_id`, and `success` are sent. The `Inspector` and adapter
  layers never touch this module — by construction they cannot leak
  topic names, message bodies, or bag paths into telemetry.
- **Pluggable transport.** Default transport writes a structured log
  line; a future HTTP transport (Fly.io, S3-backed endpoint) will plug
  in via `build_telemetry_client` without changing call sites.
- **Fire-and-forget.** Transport exceptions are swallowed so a telemetry
  hiccup can never break a tool call.
"""

from __future__ import annotations

import functools
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

log = logging.getLogger(__name__)

Transport = Callable[[dict[str, Any]], None]

F = TypeVar("F", bound=Callable[..., Any])


@dataclass(frozen=True, slots=True)
class TelemetryEvent:
    """The complete event shape sent over the transport.

    These five fields are the *only* data points telemetry ever carries.
    Adding a field here is a privacy decision — document it in the README
    Telemetry section in the same change.
    """

    tool_name: str
    latency_ms: float
    mode: str
    version: str
    session_id: str
    success: bool

    def to_payload(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "latency_ms": round(self.latency_ms, 2),
            "mode": self.mode,
            "version": self.version,
            "session_id": self.session_id,
            "success": self.success,
        }


def _log_transport(payload: dict[str, Any]) -> None:
    """Default transport — writes the payload to the `topicforge.telemetry` logger."""
    log.info("telemetry event: %s", payload)


class TelemetryClient:
    """Opt-in telemetry emitter.

    A single instance is built at server startup and shared by every tool
    handler. When disabled, every method is a no-op and the instance
    holds no resources.
    """

    def __init__(
        self,
        *,
        enabled: bool,
        mode: str,
        version: str,
        transport: Transport | None = None,
        session_id: str | None = None,
    ) -> None:
        self._enabled = enabled
        self._mode = mode
        self._version = version
        self._transport = transport or _log_transport
        # A new session id per process; never persisted, never tied to user
        # identity. Lets the server-side deduplicate within a session
        # without identifying anyone.
        self._session_id = session_id or uuid.uuid4().hex

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def session_id(self) -> str:
        return self._session_id

    def emit(self, *, tool_name: str, latency_ms: float, success: bool) -> None:
        """Send a single tool-call event. No-op when telemetry is disabled."""
        if not self._enabled:
            return
        event = TelemetryEvent(
            tool_name=tool_name,
            latency_ms=latency_ms,
            mode=self._mode,
            version=self._version,
            session_id=self._session_id,
            success=success,
        )
        try:
            self._transport(event.to_payload())
        except Exception as exc:
            log.debug("telemetry transport failed: %s", exc)


def instrument(client: TelemetryClient, tool_name: str) -> Callable[[F], F]:
    """Wrap a tool handler with timing + emit.

    When `client.enabled` is False, the decorator returns the handler
    unchanged — zero overhead and, more importantly, zero possibility of
    a network call. This is the property `test_off_mode_no_network`
    pins.

    `functools.wraps` preserves the signature so FastMCP's introspection
    of Pydantic-annotated parameters and return type still works.
    """
    if not client.enabled:
        return lambda fn: fn

    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            success = True
            try:
                return fn(*args, **kwargs)
            except Exception:
                success = False
                raise
            finally:
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                client.emit(tool_name=tool_name, latency_ms=elapsed_ms, success=success)

        return wrapper  # type: ignore[return-value]

    return decorator


def build_telemetry_client(
    *,
    enabled: bool,
    mode: str,
    version: str,
    transport: Transport | None = None,
) -> TelemetryClient:
    """Construct the telemetry client used by `build_app`.

    Kept as a small factory so wiring stays out of `server/app.py` and
    tests can inject a deterministic transport.
    """
    return TelemetryClient(
        enabled=enabled,
        mode=mode,
        version=version,
        transport=transport,
    )
