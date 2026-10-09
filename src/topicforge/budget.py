"""Per-call time budget shared between the tool guard and the adapters.

The guard sets an absolute monotonic deadline for the tool call that is
running in the current thread (request start plus the documented worst case,
lock wait included). Adapters ask `remaining()` and shrink their own timeouts
to fit, so a call that waited for a lock has less time to run, and the total
stays under the documented bound.
"""

from __future__ import annotations

import contextvars
import time
from collections.abc import Iterator
from contextlib import contextmanager

_DEADLINE: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "topicforge_deadline", default=None
)


@contextmanager
def deadline_at(monotonic_deadline: float) -> Iterator[None]:
    """Run the enclosed block with `monotonic_deadline` (a `time.monotonic()` value) active."""
    token = _DEADLINE.set(monotonic_deadline)
    try:
        yield
    finally:
        _DEADLINE.reset(token)


def remaining() -> float | None:
    """Seconds left in the active budget, `None` when no budget is active (may be <= 0)."""
    deadline = _DEADLINE.get()
    return None if deadline is None else deadline - time.monotonic()


def clamp(timeout_s: float, reserve_s: float = 0.0) -> float:
    """`timeout_s` limited to what the active budget leaves after `reserve_s`.

    Returns `timeout_s` unchanged when no budget is active. The result can be
    zero or negative when the budget is spent: the caller treats that as a timeout.
    """
    left = remaining()
    if left is None:
        return timeout_s
    return min(timeout_s, left - reserve_s)


__all__ = ["clamp", "deadline_at", "remaining"]
