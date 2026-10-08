"""Execution guard applied to every MCP tool handler.

mcp 2.x runs synchronous handlers in a worker thread, so two tool calls can
overlap. The backends cannot take that: the Cyclone binding is not thread-safe
(two concurrent `take()` calls corrupted the heap on Windows) and the `ros2`
CLI adapter spawns processes whose output it reads without any coordination.
`guarded` therefore does two things:

- serializes handler bodies behind one process-wide lock, so the server
  handles one tool call at a time, exactly as it did on the 1.x event loop;
- translates `AdapterError` into the SDK's `ToolError`. The 2.x SDK reports any
  other exception as a bare "Error executing tool X" and keeps its text on the
  server; `AdapterError` messages are written to be user-safe and must reach
  the client, so they are re-raised as `ToolError` with the same text. An
  unexpected exception is left alone and stays redacted by the SDK.

Fine-grained locks per backend are a later refinement (0.7.0 M5).
"""

from __future__ import annotations

import functools
import threading
from collections.abc import Callable
from typing import Any, TypeVar

from mcp.server.mcpserver.exceptions import ToolError

from topicforge.adapters.base import AdapterError

F = TypeVar("F", bound=Callable[..., Any])

# Process-wide: shared by every handler of every app built in this process.
_CALL_LOCK = threading.RLock()


def guarded(fn: F) -> F:
    """Serialize `fn` behind the process-wide lock and translate `AdapterError`.

    `functools.wraps` keeps the signature and annotations the SDK introspects
    to build the input and output schemas.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            with _CALL_LOCK:
                return fn(*args, **kwargs)
        except AdapterError as exc:
            raise ToolError(str(exc)) from exc

    return wrapper  # type: ignore[return-value]


__all__ = ["guarded"]
