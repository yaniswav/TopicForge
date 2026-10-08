"""Fail-closed proof that every tool declares itself read-only in the MCP protocol.

Tools are listed through the MCP layer, the way a client sees them. A new tool
registered without annotations (or with a weaker base) fails here.
"""

from __future__ import annotations

import asyncio

from mcp.types import Tool

from topicforge.config import Settings
from topicforge.server import build_app

# Tools that only read the local environment or local files, never a live graph or bus.
CLOSED_WORLD_TOOLS = {"health_check", "analyze_bag", "peek_bag_samples"}


def _tools() -> list[Tool]:
    app = build_app(
        Settings(mode="mock", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False)
    )
    return asyncio.run(app.list_tools())


def test_tool_count_is_twelve() -> None:
    assert len(_tools()) == 12


def test_every_tool_is_declared_read_only() -> None:
    for tool in _tools():
        a = tool.annotations
        assert a is not None, f"{tool.name} has no annotations"
        assert a.read_only_hint is True, tool.name
        assert a.destructive_hint is False, tool.name
        assert a.idempotent_hint is True, tool.name
        assert isinstance(a.open_world_hint, bool), tool.name
        assert a.title and a.title.strip(), tool.name


def test_open_world_hint_split_is_exact() -> None:
    closed = {t.name for t in _tools() if t.annotations and t.annotations.open_world_hint is False}
    assert closed == CLOSED_WORLD_TOOLS
