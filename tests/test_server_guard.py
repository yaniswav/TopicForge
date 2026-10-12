"""The execution guard, tested through the real MCP layer (in-memory client and server).

mcp 2.x reports any exception but `ToolError` as a bare "Error executing tool X", and
runs sync handlers in worker threads. `topicforge.tools.guard.guarded` restores the
`AdapterError` text and serializes handler bodies; these tests pin both.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import anyio
from mcp import Client
from mcp.server.mcpserver import MCPServer

from topicforge.adapters.base import AdapterError
from topicforge.adapters.ros2_mock import MockAdapter
from topicforge.config import Settings
from topicforge.models import TopicInfo
from topicforge.services import HealthService, Inspector
from topicforge.telemetry import build_telemetry_client
from topicforge.tools import register_tools

_SETTINGS = Settings(mode="mock", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False)


class _ScriptedAdapter(MockAdapter):
    """Mock adapter whose `list_topics` / `get_topic_info` can be scripted per test."""

    def __init__(self) -> None:
        super().__init__()
        self.active = 0
        self.max_active = 0
        self.guard = threading.Lock()
        self.dwell_s = 0.0

    def list_topics(self) -> list[TopicInfo]:
        return self._dwell(super().list_topics)

    def get_topic_info(self, topic: str) -> TopicInfo:
        if topic == "/adapter_error":
            raise AdapterError("Topic '/adapter_error' is not reachable: user-safe message")
        if topic == "/crash":
            raise RuntimeError("secret internal detail C:\\Users\\someone\\private.py")
        return self._dwell(lambda: super(_ScriptedAdapter, self).get_topic_info(topic))

    def _dwell(self, call: Any) -> Any:
        with self.guard:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(self.dwell_s)
            return call()
        finally:
            with self.guard:
                self.active -= 1


def _server(adapter: MockAdapter) -> MCPServer:
    mcp = MCPServer("topicforge")
    telemetry = build_telemetry_client(enabled=False, mode="mock", version="test")
    register_tools(mcp, Inspector(adapter), HealthService(_SETTINGS, adapter), telemetry)
    return mcp


def _call(mcp: MCPServer, name: str, args: dict[str, Any]) -> Any:
    async def run() -> Any:
        async with Client(mcp) as client:
            return await client.call_tool(name, args)

    return asyncio.run(run())


def test_adapter_error_text_reaches_the_client() -> None:
    result = _call(_server(_ScriptedAdapter()), "get_topic_info", {"topic": "/adapter_error"})
    assert result.is_error is True
    text = result.content[0].text
    assert "Topic '/adapter_error' is not reachable: user-safe message" in text


def test_input_validation_message_reaches_the_client() -> None:
    result = _call(_server(_ScriptedAdapter()), "get_topic_info", {"topic": "no_slash"})
    assert result.is_error is True
    assert "topic must start with '/'" in result.content[0].text


def test_unexpected_exception_is_an_error_without_leaking_details() -> None:
    result = _call(_server(_ScriptedAdapter()), "get_topic_info", {"topic": "/crash"})
    assert result.is_error is True
    text = result.content[0].text
    assert "secret internal detail" not in text
    assert "private.py" not in text
    assert "Traceback" not in text


def test_a_successful_call_is_not_an_error() -> None:
    result = _call(_server(_ScriptedAdapter()), "get_topic_info", {"topic": "/cmd_vel"})
    assert result.is_error is False
    assert result.structured_content is not None


def test_concurrent_tool_calls_are_serialized_without_deadlock() -> None:
    adapter = _ScriptedAdapter()
    adapter.dwell_s = 0.15
    mcp = _server(adapter)

    async def run() -> list[Any]:
        results: list[Any] = []
        async with Client(mcp) as client:

            async def one(name: str, args: dict[str, Any]) -> None:
                results.append(await client.call_tool(name, args))

            with anyio.fail_after(20):
                async with anyio.create_task_group() as tg:
                    tg.start_soon(one, "list_topics", {})
                    tg.start_soon(one, "get_topic_info", {"topic": "/cmd_vel"})
                    tg.start_soon(one, "list_topics", {})
        return results

    results = asyncio.run(run())
    assert len(results) == 3
    assert all(not r.is_error for r in results)
    # Handlers ran in worker threads; the guard must have kept them from overlapping.
    assert adapter.max_active == 1


def test_a_slow_handler_does_not_block_the_event_loop() -> None:
    """Fixes the 1.x freeze: sync handlers now run in a worker thread."""
    adapter = _ScriptedAdapter()
    adapter.dwell_s = 0.6
    mcp = _server(adapter)

    async def run() -> int:
        ticks = 0
        done = anyio.Event()

        async def call() -> None:
            async with Client(mcp) as client:
                await client.call_tool("list_topics", {})
            done.set()

        async def ticker() -> None:
            nonlocal ticks
            while not done.is_set():
                await anyio.sleep(0.05)
                ticks += 1

        with anyio.fail_after(20):
            async with anyio.create_task_group() as tg:
                tg.start_soon(call)
                tg.start_soon(ticker)
        return ticks

    assert asyncio.run(run()) >= 5
