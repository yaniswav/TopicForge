"""Clock classification of a message stream: wall, simulated, mixed or absent stamps.

Pure arithmetic over integer nanoseconds, tested without ROS 2. A stamp at or
above `WALL_CLOCK_NS` (2001-09-09 in epoch seconds) is read as a date, so the
publisher runs on the wall clock; a smaller non-zero stamp is time since a
simulation or robot start. Contract: docs/CONTRACT.md section 9.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

ClockBasis = Literal["wall", "sim", "none", "mixed"]

_NS_PER_S = 1_000_000_000
WALL_CLOCK_NS = 1_000_000_000 * _NS_PER_S
_UNSTAMPED_SOURCES = frozenset({"none", "recorded"})
_P95_NEAREST_RANK_MIN = 20


@dataclass(frozen=True)
class ClockStats:
    """Clock facts of one stream, as copied into `TopicRate`."""

    basis: ClockBasis = "none"
    age_median_s: float | None = None
    age_p95_s: float | None = None
    regression_count: int = 0


def classify_stamp(stamp_ns: int, source: str) -> Literal["wall", "sim", "zero"]:
    """Class of one message stamp."""
    if stamp_ns == 0 or source in _UNSTAMPED_SOURCES:
        return "zero"
    return "wall" if stamp_ns >= WALL_CLOCK_NS else "sim"


def compute_clock(stamped: Sequence[tuple[int, int, str]]) -> ClockStats:
    """Clock stats of `(basis_ns, stamp_ns, stamp_source)` tuples, one per message.

    `basis_ns` is the arrival time (live) or the record time (bag). The age
    `basis - stamp` is signed (negative means the publisher clock is ahead) and
    given only when every message is wall-stamped.
    """
    if not stamped:
        return ClockStats()
    ordered = sorted(stamped, key=lambda item: item[0])
    classes = {classify_stamp(stamp, source) for _, stamp, source in ordered}
    basis = _basis(classes)
    median = p95 = None
    if basis == "wall":
        ages = sorted((b - s) / _NS_PER_S for b, s, _ in ordered)
        median, p95 = _median(ages), _p95(ages)
    return ClockStats(
        basis=basis,
        age_median_s=median,
        age_p95_s=p95,
        regression_count=_regressions(ordered),
    )


def _basis(classes: set[str]) -> ClockBasis:
    if len(classes) > 1:
        return "mixed"
    only = next(iter(classes))
    if only == "wall":
        return "wall"
    return "sim" if only == "sim" else "none"


def _regressions(ordered: Sequence[tuple[int, int, str]]) -> int:
    """Consecutive non-zero stamps, in basis order, where the stamp strictly decreases."""
    count = 0
    previous: int | None = None
    for _, stamp, source in ordered:
        if classify_stamp(stamp, source) == "zero":
            continue
        if previous is not None and stamp < previous:
            count += 1
        previous = stamp
    return count


def _median(sorted_values: Sequence[float]) -> float:
    mid = len(sorted_values) // 2
    if len(sorted_values) % 2:
        return _round(sorted_values[mid])
    return _round((sorted_values[mid - 1] + sorted_values[mid]) / 2)


def _p95(sorted_values: Sequence[float]) -> float:
    """Nearest-rank 95th percentile; the maximum under 20 values."""
    n = len(sorted_values)
    if n < _P95_NEAREST_RANK_MIN:
        return _round(sorted_values[-1])
    return _round(sorted_values[math.ceil(0.95 * n) - 1])


def _round(value: float) -> float:
    return round(value, 6)
