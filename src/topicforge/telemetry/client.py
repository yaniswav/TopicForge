"""Telemetry client and tool instrumentation.

- Disabled means a no-op: `emit()` and `instrument()` assemble no payload,
  call no transport and add no timing. Tests pin that nothing touches the
  network when it is off.
- Only `tool_name`, `latency_ms`, `mode`, `version`, `session_id` and
  `success` are sent. The inspector and adapters never import this module,
  so topic names, message bodies and bag paths cannot reach it.
- The default transport logs a line; another one can be injected through
  `build_telemetry_client`.
- Transport exceptions are swallowed so telemetry cannot break a tool call.
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
    """The event sent over the transport; these six fields are all telemetry carries.

    Adding a field is a privacy decision: update the README Telemetry
    section in the same change.
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
    """Default transport: writes the payload to the `topicforge.telemetry` logger."""
    log.info("telemetry event: %s", payload)


class TelemetryClient:
    """Opt-in telemetry emitter, built once at startup and shared by the handlers."""

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
        # New per process, never persisted or tied to a user.
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
    """Wrap a tool handler with timing and an `emit` call.

    When telemetry is disabled the handler is returned unchanged, so no
    network call is possible (`test_off_mode_no_network`). `functools.wraps`
    keeps the signature that FastMCP introspects.
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
    """Construct the client used by `build_app`; tests inject a transport here."""
    return TelemetryClient(
        enabled=enabled,
        mode=mode,
        version=version,
        transport=transport,
    )
