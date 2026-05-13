"""Tests for `topicforge.telemetry`.

The load-bearing property pinned here is: **OFF means no network**. If
this suite ever passes while the OFF path quietly invokes the transport,
the privacy promise documented in the README is broken.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from topicforge import __version__
from topicforge.config import Settings, load_settings
from topicforge.server import build_app
from topicforge.telemetry import (
    TelemetryClient,
    TelemetryEvent,
    build_telemetry_client,
    instrument,
)


class _SpyTransport:
    """Records every payload it receives; never touches the network."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, payload: dict[str, Any]) -> None:
        self.calls.append(payload)


def _settings(telemetry: bool) -> Settings:
    return Settings(
        mode="mock",
        log_level="INFO",
        ros2_executable="ros2",
        telemetry_enabled=telemetry,
    )


# ---- Settings parsing --------------------------------------------------


def test_telemetry_defaults_off() -> None:
    s = load_settings(env={})
    assert s.telemetry_enabled is False


@pytest.mark.parametrize("value", ["on", "ON", "1", "true", "TRUE", "yes", "enabled"])
def test_telemetry_on_values(value: str) -> None:
    s = load_settings(env={"TOPICFORGE_TELEMETRY": value})
    assert s.telemetry_enabled is True


@pytest.mark.parametrize("value", ["off", "0", "false", "no", "disabled", ""])
def test_telemetry_off_values(value: str) -> None:
    s = load_settings(env={"TOPICFORGE_TELEMETRY": value})
    assert s.telemetry_enabled is False


def test_telemetry_invalid_value_rejected() -> None:
    with pytest.raises(ValueError, match="TOPICFORGE_TELEMETRY"):
        load_settings(env={"TOPICFORGE_TELEMETRY": "maybe"})


# ---- Client.emit() -----------------------------------------------------


def test_emit_is_noop_when_disabled() -> None:
    transport = _SpyTransport()
    client = build_telemetry_client(
        enabled=False, mode="mock", version="0.0.0", transport=transport
    )
    client.emit(tool_name="list_topics", latency_ms=12.3, success=True)
    assert transport.calls == []


def test_emit_sends_expected_payload_when_enabled() -> None:
    transport = _SpyTransport()
    client = TelemetryClient(
        enabled=True,
        mode="mock",
        version="9.9.9",
        transport=transport,
        session_id="fixed-session-id",
    )
    client.emit(tool_name="list_topics", latency_ms=42.567, success=True)

    assert len(transport.calls) == 1
    payload = transport.calls[0]
    assert payload == {
        "tool_name": "list_topics",
        "latency_ms": 42.57,
        "mode": "mock",
        "version": "9.9.9",
        "session_id": "fixed-session-id",
        "success": True,
    }


def test_session_id_is_stable_within_a_client() -> None:
    transport = _SpyTransport()
    client = TelemetryClient(enabled=True, mode="mock", version="0.0.0", transport=transport)
    client.emit(tool_name="health_check", latency_ms=1.0, success=True)
    client.emit(tool_name="list_topics", latency_ms=2.0, success=True)
    assert transport.calls[0]["session_id"] == transport.calls[1]["session_id"]


def test_session_ids_differ_between_clients() -> None:
    a = TelemetryClient(enabled=True, mode="mock", version="0.0.0")
    b = TelemetryClient(enabled=True, mode="mock", version="0.0.0")
    assert a.session_id != b.session_id


def test_transport_exception_does_not_propagate() -> None:
    def boom(_payload: dict[str, Any]) -> None:
        raise RuntimeError("network down")

    client = TelemetryClient(enabled=True, mode="mock", version="0.0.0", transport=boom)
    # Must not raise — telemetry can never break a tool call.
    client.emit(tool_name="health_check", latency_ms=1.0, success=True)


# ---- instrument() decorator -------------------------------------------


def test_instrument_is_identity_when_disabled() -> None:
    transport = _SpyTransport()
    client = TelemetryClient(enabled=False, mode="mock", version="0.0.0", transport=transport)

    @instrument(client, "list_topics")
    def handler() -> str:
        return "ok"

    assert handler() == "ok"
    assert transport.calls == []


def test_instrument_records_success_when_enabled() -> None:
    transport = _SpyTransport()
    client = TelemetryClient(
        enabled=True,
        mode="mock",
        version="0.0.0",
        transport=transport,
        session_id="sid",
    )

    @instrument(client, "list_topics")
    def handler() -> str:
        return "ok"

    assert handler() == "ok"
    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["tool_name"] == "list_topics"
    assert call["success"] is True
    assert isinstance(call["latency_ms"], float)
    assert call["latency_ms"] >= 0.0


def test_instrument_records_failure_and_reraises() -> None:
    transport = _SpyTransport()
    client = TelemetryClient(enabled=True, mode="mock", version="0.0.0", transport=transport)

    @instrument(client, "analyze_bag")
    def handler() -> None:
        raise ValueError("bad path")

    with pytest.raises(ValueError, match="bad path"):
        handler()

    assert len(transport.calls) == 1
    assert transport.calls[0]["success"] is False
    assert transport.calls[0]["tool_name"] == "analyze_bag"


def test_instrument_preserves_function_signature() -> None:
    """FastMCP introspects handler signatures to derive the input schema.

    If `instrument` ever drops the signature, MCP clients lose all
    parameter descriptions. This pins the contract.
    """
    import inspect

    client = TelemetryClient(enabled=True, mode="mock", version="0.0.0")

    @instrument(client, "get_topic_info")
    def handler(topic: str, count: int = 5) -> str:
        return f"{topic}-{count}"

    sig = inspect.signature(handler)
    assert list(sig.parameters) == ["topic", "count"]
    assert sig.parameters["count"].default == 5


# ---- Payload privacy ---------------------------------------------------


def test_payload_contains_only_whitelisted_keys() -> None:
    """Hard fence around what we send. If you add a key here, document it
    in the README Telemetry section in the same change.
    """
    allowed = {"tool_name", "latency_ms", "mode", "version", "session_id", "success"}
    event = TelemetryEvent(
        tool_name="x",
        latency_ms=1.0,
        mode="mock",
        version="0.0.0",
        session_id="sid",
        success=True,
    )
    assert set(event.to_payload().keys()) == allowed


def test_payload_never_contains_user_supplied_input() -> None:
    """Even if a tool is called with sensitive arguments, telemetry only
    carries the tool name — never the argument values.
    """
    transport = _SpyTransport()
    client = TelemetryClient(enabled=True, mode="mock", version="0.0.0", transport=transport)

    @instrument(client, "get_topic_info")
    def handler(topic: str) -> str:
        return topic

    handler("/super/secret/internal/topic")
    assert transport.calls, "telemetry should have fired"
    payload = transport.calls[0]
    # The topic name must not appear anywhere in the payload.
    serialized = repr(payload)
    assert "secret" not in serialized
    assert "/super" not in serialized


# ---- End-to-end through the built MCP app -----------------------------


def test_build_app_off_makes_no_transport_calls() -> None:
    transport = _SpyTransport()
    app = build_app(_settings(telemetry=False), telemetry_transport=transport)
    tools = {t.name: t for t in asyncio.run(app.list_tools())}
    # Touch every MVP tool through FastMCP's call_tool path.
    for name in ("health_check", "list_topics"):
        asyncio.run(app.call_tool(name, {}))
    asyncio.run(app.call_tool("get_topic_info", {"topic": "/cmd_vel"}))
    asyncio.run(app.call_tool("sample_messages", {"topic": "/cmd_vel", "count": 1}))
    asyncio.run(app.call_tool("analyze_bag", {"path": "/tmp/demo.mcap"}))

    assert tools, "sanity: app must register tools"
    assert transport.calls == [], (
        "OFF mode must never invoke the telemetry transport (privacy invariant)"
    )


def test_build_app_on_emits_one_event_per_tool_call() -> None:
    transport = _SpyTransport()
    app = build_app(_settings(telemetry=True), telemetry_transport=transport)

    asyncio.run(app.call_tool("health_check", {}))
    asyncio.run(app.call_tool("list_topics", {}))

    assert len(transport.calls) == 2
    names = [c["tool_name"] for c in transport.calls]
    assert names == ["health_check", "list_topics"]
    for c in transport.calls:
        assert c["mode"] == "mock"
        assert c["version"] == __version__
        assert c["success"] is True


def test_default_log_transport_writes_one_record_per_event(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No transport injected — the default log transport must still fire.

    Pins the MVP behaviour: opt-in users can verify what was sent by
    reading the `topicforge.telemetry` logger.
    """
    client = build_telemetry_client(enabled=True, mode="mock", version="0.0.0")
    with caplog.at_level(logging.INFO, logger="topicforge.telemetry.client"):
        client.emit(tool_name="health_check", latency_ms=1.0, success=True)
    assert any("telemetry event" in r.message for r in caplog.records)
