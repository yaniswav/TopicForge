"""Lane locks and the call budget, tested through the real MCP layer.

A fake adapter blocks inside a ROS or DDS call on a `threading.Event`, so the
tests use events rather than sleeps to order things. They pin the four rules of
`topicforge.tools.guard`: `health_check` never waits, ROS and DDS calls do not
block each other, two ROS calls serialise and the loser fails fast with a
`busy` error, and a mixed burst finishes without deadlock.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import anyio
import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer

from topicforge import budget
from topicforge.adapters.ros2_mock import MockAdapter
from topicforge.config import Settings
from topicforge.models import ParticipantInfo, SampleResult, TopicInfo
from topicforge.services import HealthService, Inspector
from topicforge.telemetry import build_telemetry_client
from topicforge.tools import guard, register_tools

_SETTINGS = Settings(mode="mock", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False)


class _BlockingAdapter(MockAdapter):
    """Mock adapter whose ROS-lane calls can block until the test lets them go."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block_ros = False
        self.active = 0
        self.max_active = 0
        self.seen_remaining: list[float | None] = []
        self._count = threading.Lock()

    def _ros_call(self) -> None:
        with self._count:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            self.seen_remaining.append(budget.remaining())
            if self.block_ros:
                self.block_ros = False  # only the first call blocks
                self.entered.set()
                assert self.release.wait(30), "test never released the blocked call"
        finally:
            with self._count:
                self.active -= 1

    def sample_messages(self, topic: str, count: int, **kwargs: Any) -> SampleResult:
        self._ros_call()
        return super().sample_messages(topic, count, **kwargs)

    def get_topic_info(self, topic: str) -> TopicInfo:
        self._ros_call()
        return super().get_topic_info(topic)

    def list_topics(self) -> list[TopicInfo]:
        self._ros_call()
        return super().list_topics()

    def list_participants(self, domain_id: int = 0) -> list[ParticipantInfo]:
        return super().list_participants(domain_id)


def _server(adapter: MockAdapter) -> MCPServer:
    mcp = MCPServer("topicforge")
    telemetry = build_telemetry_client(enabled=False, mode="mock", version="test")
    register_tools(mcp, Inspector(adapter), HealthService(_SETTINGS, adapter), telemetry)
    return mcp


async def _wait_entered(adapter: _BlockingAdapter) -> None:
    assert await anyio.to_thread.run_sync(adapter.entered.wait, 10), "blocked call never started"


def test_health_check_answers_while_a_ros_call_is_blocked() -> None:
    adapter = _BlockingAdapter()
    adapter.block_ros = True
    mcp = _server(adapter)

    async def run() -> float:
        async with Client(mcp) as client:
            results: list[Any] = []

            async def blocked() -> None:
                results.append(await client.call_tool("sample_messages", {"topic": "/scan"}))

            with anyio.fail_after(30):
                async with anyio.create_task_group() as tg:
                    tg.start_soon(blocked)
                    await _wait_entered(adapter)
                    started = time.monotonic()
                    health = await client.call_tool("health_check", {})
                    elapsed = time.monotonic() - started
                    assert not health.is_error
                    assert not results, "the ROS call must still be blocked"
                    adapter.release.set()
            assert not results[0].is_error
            return elapsed

    assert asyncio.run(run()) < 1.0


def test_a_dds_call_proceeds_while_a_ros_call_is_blocked() -> None:
    adapter = _BlockingAdapter()
    adapter.block_ros = True
    mcp = _server(adapter)

    async def run() -> None:
        async with Client(mcp) as client:
            results: list[Any] = []

            async def blocked() -> None:
                results.append(await client.call_tool("get_topic_info", {"topic": "/scan"}))

            with anyio.fail_after(30):
                async with anyio.create_task_group() as tg:
                    tg.start_soon(blocked)
                    await _wait_entered(adapter)
                    dds = await client.call_tool("list_participants", {})
                    assert not dds.is_error
                    assert not results, "the ROS call must still be blocked"
                    adapter.release.set()
            assert not results[0].is_error

    asyncio.run(run())


def test_a_second_ros_call_fails_fast_with_busy_when_its_budget_runs_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(guard, "TOOL_BUDGET_S", 1.0)
    monkeypatch.setattr(guard, "MIN_RUN_S", 0.2)
    adapter = _BlockingAdapter()
    adapter.block_ros = True
    mcp = _server(adapter)

    async def run() -> tuple[Any, float]:
        async with Client(mcp) as client:
            results: list[Any] = []

            async def blocked() -> None:
                results.append(await client.call_tool("list_topics", {}))

            with anyio.fail_after(30):
                async with anyio.create_task_group() as tg:
                    tg.start_soon(blocked)
                    await _wait_entered(adapter)
                    started = time.monotonic()
                    second = await client.call_tool("get_topic_info", {"topic": "/scan"})
                    waited = time.monotonic() - started
                    adapter.release.set()
            assert not results[0].is_error, "the first call is unaffected"
            return second, waited

    second, waited = asyncio.run(run())
    assert second.is_error is True
    assert "busy: another ros call is running, retry" in second.content[0].text
    assert waited < 3.0, "it must give up when its budget runs out, not wait for the holder"
    assert adapter.max_active == 1


def test_ros_calls_never_overlap_and_the_waiter_has_less_time_left() -> None:
    adapter = _BlockingAdapter()
    adapter.block_ros = True
    mcp = _server(adapter)

    async def run() -> None:
        async with Client(mcp) as client:
            results: list[Any] = []

            async def first() -> None:
                results.append(await client.call_tool("list_topics", {}))

            async def second() -> None:
                results.append(await client.call_tool("get_topic_info", {"topic": "/scan"}))

            with anyio.fail_after(30):
                async with anyio.create_task_group() as tg:
                    tg.start_soon(first)
                    await _wait_entered(adapter)
                    tg.start_soon(second)
                    await anyio.sleep(0.5)  # the second call is queued behind the first
                    adapter.release.set()
            assert len(results) == 2 and not any(r.is_error for r in results)

    asyncio.run(run())
    assert adapter.max_active == 1
    first_left, second_left = adapter.seen_remaining
    assert first_left is not None and second_left is not None
    assert second_left < first_left - 0.3, "waiting for the lock must shorten the deadline"
    # Windows' coarse monotonic clock can return the same tick on both reads, so
    # deadline - now rounds to a hair above the budget.
    assert first_left <= guard.TOOL_BUDGET_S + 1e-6


def test_a_mixed_burst_finishes_without_deadlock() -> None:
    adapter = _BlockingAdapter()
    mcp = _server(adapter)
    calls: list[tuple[str, dict[str, Any]]] = [
        ("list_topics", {}),
        ("get_topic_info", {"topic": "/scan"}),
        ("list_participants", {}),
        ("health_check", {}),
        ("sample_messages", {"topic": "/scan", "count": 1}),
    ] * 4

    async def run() -> list[Any]:
        results: list[Any] = []
        async with Client(mcp) as client:

            async def one(name: str, args: dict[str, Any]) -> None:
                results.append(await client.call_tool(name, args))

            with anyio.fail_after(60):
                async with anyio.create_task_group() as tg:
                    for name, args in calls:
                        tg.start_soon(one, name, args)
        return results

    results = asyncio.run(run())
    assert len(results) == 20
    assert not [r for r in results if r.is_error]
    assert adapter.max_active == 1, "ROS calls overlapped"


def test_a_full_queue_refuses_new_callers_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(guard, "MAX_WAITERS", 1)
    lane = guard._LaneState()
    assert lane.acquire(1.0)  # the holder
    waiter_in = threading.Event()
    outcome: list[bool] = []

    def waiter() -> None:
        waiter_in.set()
        outcome.append(lane.acquire(5.0))

    thread = threading.Thread(target=waiter)
    thread.start()
    waiter_in.wait(5)
    deadline = time.monotonic() + 5
    while lane._waiting == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    started = time.monotonic()
    assert lane.acquire(5.0) is False
    assert time.monotonic() - started < 1.0
    lane.lock.release()
    thread.join(10)
    assert outcome == [True]
    lane.lock.release()
