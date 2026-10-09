"""Execution guard applied to every MCP tool handler.

mcp 2.x runs synchronous handlers in worker threads, so tool calls can
overlap. The backends cannot take that: the Cyclone binding is not thread-safe
(two concurrent `take()` calls corrupted the heap on Windows) and the `ros2`
CLI adapter spawns processes. `guarded(lane)` therefore does three things.

Lanes. A handler names the backend it uses and runs behind that lane's lock:

- `"ros"`: calls that run the `ros2` CLI (`list_topics`, `get_topic_info`,
  `sample_messages`, `analyze_bag`);
- `"dds"`: calls that use the DDS binding (the seven DDS and observability
  tools);
- `None`: no lock. `health_check` (it must always answer, so it never waits
  for a lane and never runs a CLI) and `peek_bag_samples` (a pure-Python file
  read that shares no state with either backend).

A ROS call and a DDS call can run at the same time; two calls of one lane
cannot. The Cyclone adapter keeps its own `_BINDING_LOCK` as the innermost guard.

Budget. A call has `TOOL_BUDGET_S` seconds of wall time from the moment it
starts, lock wait included (docs/CONTRACT.md section 1.7). The guard waits for
the lane lock at most until `MIN_RUN_S` seconds remain, then raises
`AdapterError("busy: ...")` instead of waiting for ever. The deadline is
published through `topicforge.budget`, so adapters shorten their own timeouts
by the time the call waited. At most `MAX_WAITERS` calls queue per lane; more
are refused at once, which keeps worker threads free for `health_check`.

Errors. `AdapterError` becomes the SDK's `ToolError` with the same text. The
2.x SDK reports any other exception as a bare "Error executing tool X" and
keeps its text on the server; `AdapterError` messages are user-safe and must
reach the client. An unexpected exception is left alone and stays redacted.
"""

from __future__ import annotations

import functools
import threading
import time
from collections.abc import Callable
from typing import Any, Literal, TypeVar

from mcp.server.mcpserver.exceptions import ToolError

from topicforge import budget
from topicforge.adapters.base import AdapterError

F = TypeVar("F", bound=Callable[..., Any])

Lane = Literal["ros", "dds"]

# Wall time of one tool call, lock wait included. Read at call time (tests patch it).
TOOL_BUDGET_S = 45.0
# A call that cannot get its lane with this much budget left is refused as busy.
MIN_RUN_S = 5.0
# Calls allowed to queue behind one lane before new ones are refused.
MAX_WAITERS = 16


class _LaneState:
    """One lane: its lock and the number of callers queued for it."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self._mutex = threading.Lock()
        self._waiting = 0

    def acquire(self, wait_s: float) -> bool:
        """Take the lock within `wait_s` seconds; `False` when busy or the queue is full."""
        if self.lock.acquire(blocking=False):
            return True
        with self._mutex:
            if self._waiting >= MAX_WAITERS:
                return False
            self._waiting += 1
        try:
            return wait_s > 0 and self.lock.acquire(timeout=wait_s)
        finally:
            with self._mutex:
                self._waiting -= 1


# Process-wide: shared by every handler of every app built in this process.
_LANES: dict[str, _LaneState] = {"ros": _LaneState(), "dds": _LaneState()}


def _busy(lane: str) -> AdapterError:
    return AdapterError(f"busy: another {lane} call is running, retry")


def guarded(lane: Lane | None = None) -> Callable[[F], F]:
    """Decorate a handler: lane lock, call budget and `AdapterError` -> `ToolError`.

    `functools.wraps` keeps the signature and annotations the SDK introspects
    to build the input and output schemas.
    """

    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return _run_in_lane(fn, lane, args, kwargs)
            except AdapterError as exc:
                raise ToolError(str(exc)) from exc

        return wrapper  # type: ignore[return-value]

    return decorator


def _run_in_lane(fn: Callable[..., Any], lane: Lane | None, args: Any, kwargs: Any) -> Any:
    if lane is None:
        return fn(*args, **kwargs)
    end = time.monotonic() + TOOL_BUDGET_S
    state = _LANES[lane]
    if not state.acquire(end - MIN_RUN_S - time.monotonic()):
        raise _busy(lane)
    try:
        with budget.deadline_at(end):
            return fn(*args, **kwargs)
    finally:
        state.lock.release()


__all__ = ["Lane", "guarded"]
