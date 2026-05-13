"""Integration test: build the real MCP app and verify tool registration.

We deliberately use FastMCP's public `list_tools()` async API rather than
poking at internals, so this test stays stable across MCP SDK versions.
Handler logic is exercised end-to-end through the service-layer tests; this
suite's job is to ensure the wiring works.
"""

from __future__ import annotations

import asyncio

from topicforge.config import Settings
from topicforge.server import build_app

MVP_TOOLS = {
    "health_check",
    "list_topics",
    "get_topic_info",
    "sample_messages",
    "analyze_bag",
}


def _mock_app():
    return build_app(
        Settings(mode="mock", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False)
    )


def test_build_app_succeeds() -> None:
    app = _mock_app()
    assert app is not None


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


# Map each tool to the title FastMCP derives from its Pydantic return type.
# Pinning these prevents a silent regression to `dict[str, Any]` handlers,
# which would degrade outputSchema back to `additionalProperties: True`.
_EXPECTED_OUTPUT_TITLES = {
    "health_check": "HealthReport",
    "get_topic_info": "TopicInfo",
    "sample_messages": "SampleResult",
    "analyze_bag": "BagAnalysis",
}


def test_tool_outputs_are_typed_pydantic_schemas() -> None:
    app = _mock_app()
    tools = {t.name: t for t in asyncio.run(app.list_tools())}

    for name, expected_title in _EXPECTED_OUTPUT_TITLES.items():
        schema = tools[name].outputSchema
        assert schema is not None, f"{name}: outputSchema must be populated"
        assert schema.get("title") == expected_title, (
            f"{name}: expected outputSchema.title={expected_title!r}, got {schema.get('title')!r}"
        )
        # `additionalProperties: True` is FastMCP's signal for a generic dict
        # return type. Our handlers return frozen Pydantic models, so the flag
        # must be either absent or explicitly `False`.
        assert schema.get("additionalProperties") is not True, (
            f"{name}: outputSchema must not be a generic dict envelope"
        )

    # `list_topics` returns `list[TopicInfo]`; FastMCP wraps that in a `result`
    # property and emits TopicInfo under `$defs`.
    list_schema = tools["list_topics"].outputSchema
    assert list_schema is not None
    assert "TopicInfo" in (list_schema.get("$defs") or {}), (
        "list_topics outputSchema should reference TopicInfo via $defs"
    )
