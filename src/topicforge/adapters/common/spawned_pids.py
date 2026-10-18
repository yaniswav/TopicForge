"""Ring of the processes TopicForge started recently.

A `ros2` CLI call (`ros2 node info`, ...) joins the DDS domain as a participant
and lingers for 0.3-2 s after it exits. `run_process` records each pid here so
the composite adapter can flag those ghosts in `list_participants`. Pure and
thread-safe; no binding.
"""

from __future__ import annotations

import threading
import time
from collections import deque

MAX_SPAWNS = 256
_lock = threading.Lock()
_ring: deque[tuple[int, int]] = deque(maxlen=MAX_SPAWNS)


def record_spawn(pid: int, now_ns: int | None = None) -> None:
    """Remember that TopicForge started process `pid` (wall-clock ns)."""
    with _lock:
        _ring.append((pid, time.time_ns() if now_ns is None else now_ns))


def recent_spawns() -> list[tuple[int, int]]:
    """Copy of the ring, oldest first: `(pid, wall_ns)`."""
    with _lock:
        return list(_ring)


def clear_spawns() -> None:
    """Empty the ring (tests)."""
    with _lock:
        _ring.clear()
