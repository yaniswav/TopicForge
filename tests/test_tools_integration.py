"""Integration test: build the real MCP app and verify tool registration.

We deliberately use the SDK's public `list_tools()` async API rather than
poking at internals, so this test stays stable across MCP SDK versions.
Handler logic is exercised end-to-end through the service-layer tests; this
suite's job is to ensure the wiring works.
"""

from __future__ import annotations

import asyncio

import pytest

from topicforge.config import Settings
from topicforge.server import build_app

MVP_TOOLS = {
    # ROS2 graph tools (v0.1.x)
    "health_check",
    "list_topics",
    "get_topic_info",
    "sample_messages",
    "analyze_bag",
    # DDS module tools
    "list_participants",
    "detect_qos_mismatches",
    "peek_dds_samples",
    # DDS lifecycle
    "participant_events",
    # DDS temporal diagnostics
    "topic_metrics",
    # Bag post-mortem analysis
    "peek_bag_samples",
    # Endpoint discovery
    "list_endpoints",
}


def _mock_app():
    return build_app(
        Settings(mode="mock", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False)
    )


def test_build_app_succeeds() -> None:
    app = _mock_app()
    assert app is not None


def test_tool_surface_is_locked_at_twelve() -> None:
    names = {t.name for t in asyncio.run(_mock_app().list_tools())}
    assert names == MVP_TOOLS
    assert len(names) == 12


def test_all_mvp_tools_registered() -> None:
    app = _mock_app()
    tools = asyncio.run(app.list_tools())
    names = {t.name for t in tools}
    missing = MVP_TOOLS - names
    assert not missing, f"missing tools: {missing}"


def test_registered_tools_have_descriptions() -> None:
    app = _mock_app()
    tools = asyncio.run(app.list_tools())
    for t in tools:
        if t.name in MVP_TOOLS:
            assert t.description, f"{t.name} is missing a description"


def test_adapter_error_propagates_as_tool_error() -> None:
    """Handlers are thin:
    `AdapterError` bubbles up (re-raised as ToolError by the guard), which the SDK surfaces it as an MCP-native
    error (isError=true) rather than masking it as a successful result. At the
    `call_tool` layer this manifests as a `ToolError` carrying the
    adapter's message. If a handler ever wrapped errors in a custom success
    envelope, this would silently pass a normal result instead of raising."""
    from mcp.server.mcpserver.exceptions import ToolError

    app = _mock_app()
    with pytest.raises(ToolError, match="Unknown topic"):
        asyncio.run(app.call_tool("get_topic_info", {"topic": "/does_not_exist"}))


def test_valid_tool_call_returns_result_not_error() -> None:
    """Contrast case: a well-formed call returns a result without raising."""
    app = _mock_app()
    result = asyncio.run(app.call_tool("health_check", {}))
    assert result is not None


# Map each tool to the title the SDK derives from its Pydantic return type.
# Pinning these prevents a silent regression to `dict[str, Any]` handlers,
# which would degrade outputSchema back to `additionalProperties: True`.
_EXPECTED_OUTPUT_TITLES = {
    "health_check": "HealthReport",
    "get_topic_info": "TopicInfo",
    "sample_messages": "SampleResult",
    "analyze_bag": "BagAnalysis",
    # DDS module tools
    "peek_dds_samples": "SampleResult",
}


def test_tool_outputs_are_typed_pydantic_schemas() -> None:
    app = _mock_app()
    tools = {t.name: t for t in asyncio.run(app.list_tools())}

    for name, expected_title in _EXPECTED_OUTPUT_TITLES.items():
        schema = tools[name].output_schema
        assert schema is not None, f"{name}: outputSchema must be populated"
        assert schema.get("title") == expected_title, (
            f"{name}: expected outputSchema.title={expected_title!r}, got {schema.get('title')!r}"
        )
        # `additionalProperties: True` is the SDK's signal for a generic dict
        # return type. Our handlers return frozen Pydantic models, so the flag
        # must be either absent or explicitly `False`.
        assert schema.get("additionalProperties") is not True, (
            f"{name}: outputSchema must not be a generic dict envelope"
        )

    # `list_topics` returns `list[TopicInfo]`; the SDK wraps that in a `result`
    # property and emits TopicInfo under `$defs`.
    list_schema = tools["list_topics"].output_schema
    assert list_schema is not None
    assert "TopicInfo" in (list_schema.get("$defs") or {}), (
        "list_topics outputSchema should reference TopicInfo via $defs"
    )


# Pin the `mode_effective` contract: every response carrier
# (TopicInfo, SampleResult, BagAnalysis) must surface it as a required field
# so a downstream LLM can distinguish a live response from a mock one without
# re-reading `health_check`.


def _resolve_response_schema(
    tool_schema: dict[str, object], expected_title: str
) -> dict[str, object]:
    if tool_schema.get("title") == expected_title:
        return tool_schema
    defs = tool_schema.get("$defs") or {}
    if isinstance(defs, dict) and expected_title in defs:
        nested = defs[expected_title]
        assert isinstance(nested, dict)
        return nested
    raise AssertionError(f"could not locate schema for {expected_title!r} in {tool_schema!r}")


def test_tool_responses_expose_mode_effective_field() -> None:
    app = _mock_app()
    tools = {t.name: t for t in asyncio.run(app.list_tools())}

    # (tool_name, schema_title) pairs that should carry `mode_effective`.
    checks = [
        ("get_topic_info", "TopicInfo"),
        ("sample_messages", "SampleResult"),
        ("analyze_bag", "BagAnalysis"),
        ("list_topics", "TopicInfo"),  # nested via $defs in the list envelope
    ]
    for tool_name, schema_title in checks:
        tool_schema = tools[tool_name].output_schema
        assert tool_schema is not None, f"{tool_name}: outputSchema must be populated"
        resolved = _resolve_response_schema(tool_schema, schema_title)
        properties = resolved.get("properties") or {}
        required = resolved.get("required") or []
        assert isinstance(properties, dict)
        assert "mode_effective" in properties, (
            f"{tool_name}/{schema_title}: outputSchema must declare mode_effective"
        )
        assert "mode_effective" in required, (
            f"{tool_name}/{schema_title}: mode_effective must be required, not optional"
        )
