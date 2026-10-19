"""Time range of `peek_bag_samples`: bag-relative seconds to record-time nanoseconds.

`start_s` and `end_s` count from the bag's first recorded message (rosbag2
`starting_time`) and select on the record time, as the half-open interval
`[start, end)`. Pure functions, shared by the `rosbags` reader and the mock.
"""

from __future__ import annotations

import math

from topicforge.adapters.base import AdapterError

_NS_PER_S = 1_000_000_000


def validate_range(start_s: float | None, end_s: float | None) -> None:
    """Raise `AdapterError` for a negative, non-finite or empty range."""
    for label, value in (("start_s", start_s), ("end_s", end_s)):
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise AdapterError(f"{label} must be a number of seconds, got {type(value).__name__}")
        if not math.isfinite(value) or value < 0:
            raise AdapterError(f"{label} must be a finite number >= 0, got {value}")
    if start_s is not None and end_s is not None and end_s <= start_s:
        raise AdapterError(f"end_s ({end_s:g}) must be greater than start_s ({start_s:g})")


def window_ns(
    base_ns: int, start_s: float | None, end_s: float | None
) -> tuple[int | None, int | None]:
    """`(start, stop)` record times in ns for `[start_s, end_s)`; `None` leaves a side open."""
    start = None if start_s is None else base_ns + round(start_s * _NS_PER_S)
    stop = None if end_s is None else base_ns + round(end_s * _NS_PER_S)
    return start, stop


def range_note(
    start_s: float | None,
    end_s: float | None,
    *,
    duration_s: float | None,
    got: int,
) -> str | None:
    """Note for a ranged read: past the end of the bag, or an empty window; else `None`."""
    if start_s is None and end_s is None:
        return None
    label = (
        f"[{0 if start_s is None else start_s:g} s, {'end' if end_s is None else f'{end_s:g} s'})"
    )
    if duration_s is not None and start_s is not None and start_s > duration_s:
        return (
            f"start_s {start_s:g} is past the end of the bag ({duration_s:.3f} s long): "
            "no message in range."
        )
    if got == 0:
        return f"No message of this topic in the bag-relative range {label}."
    return None
