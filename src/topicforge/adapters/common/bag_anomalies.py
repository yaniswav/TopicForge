"""Anomalies in a bag, found from record times and decoded stamps (binding-free, pure).

Every threshold is relative to the series' own rhythm, with a minimum sample count and an
absolute floor, because a false alarm on a healthy bag costs more than a missed marginal gap:

* clock: `/clock` going backward, or a forward step far above its usual step;
* TF: on `/tf`, a dynamic edge silent for far longer than its own usual period;
* losses: a steady topic (low spread of intervals) with a recording-time gap far above its
  median period. A gap that many steady topics share is one stall, not many losses;
* stamps: a header stamp going backward on a steady topic.

`find_anomalies` returns plain strings that name the series, the offset in seconds from the
start of the bag and the sizes. See `bag_scan.py` for what is collected and when it is cut.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise

_NS = 1_000_000_000

MIN_STEADY_INTERVALS = 30  # a topic needs this many intervals to count as steady
STEADY_P90_FACTOR = 2.0  # steady: the 90th percentile interval is at most 2 x the median
LOSS_FACTOR = 5.0  # a loss gap exceeds 5 x the median period ...
LOSS_FLOOR_S = 0.2  # ... and this many seconds
TF_MIN_INTERVALS = 19  # an edge needs 20 messages to be judged
TF_GAP_FACTOR = 10.0
TF_GAP_FLOOR_S = 0.5
CLOCK_BACK_TOLERANCE_S = 0.001
CLOCK_JUMP_FACTOR = 20.0  # a forward jump exceeds 20 x the usual step ...
CLOCK_JUMP_FLOOR_S = 1.0  # ... and this many seconds
CLOCK_MIN_STEPS = 10  # steps needed to know the usual clock step
STAMP_BACK_FLOOR_S = 0.5
STAMP_BACK_FACTOR = 5.0
STAMP_MIN_SAMPLES = 5
STALL_MIN_TOPICS = 3  # a stall: this many steady topics silent together ...
STALL_MIN_SHARE = 0.5  # ... and at least this share of all steady topics
MAX_PER_SERIES = 3
MAX_LISTED = 12


@dataclass(frozen=True)
class _Gap:
    """A silence between two consecutive record times of one series."""

    series: str
    start_ns: int
    end_ns: int
    period_ns: float
    count: int

    @property
    def length_ns(self) -> int:
        return self.end_ns - self.start_ns


@dataclass(frozen=True)
class _Stall:
    """A silence that several steady topics share."""

    start_ns: int
    end_ns: int
    topics: tuple[str, ...]


def find_anomalies(
    *,
    origin_ns: int,
    clock: Sequence[tuple[int, int]],
    tf_edges: Mapping[tuple[str, str], Sequence[int]],
    topic_times: Mapping[str, Sequence[int]],
    stamps: Mapping[str, Sequence[tuple[int, int]]],
) -> list[str]:
    """Anomaly strings for one bag, at most `MAX_LISTED` of them.

    `origin_ns` is the record time of the first message, the zero of every offset.
    `clock` holds `(record_ns, clock_ns)` of `/clock`; `tf_edges` the record times of each
    dynamic `(parent, child)` edge; `topic_times` the record times of the topics eligible for
    loss and stamp checks (the caller leaves out latched, internal and multiplexed topics);
    `stamps` the sampled `(record_ns, header_stamp_ns)` per topic.
    """
    steady = {name: p for name, ts in topic_times.items() if (p := _steady_period(ts)) is not None}
    loss_gaps = [g for name, p in steady.items() for g in _gaps(name, topic_times[name], p)]
    stalls, individual = _split_stalls(loss_gaps, len(steady))
    tf_gaps = [g for g in _tf_gaps(tf_edges) if not _inside_a_stall(g, stalls)]
    lines: list[str] = []
    lines += _clock_lines(clock, origin_ns)
    lines += [_stall_line(s, origin_ns) for s in stalls]
    lines += _gap_lines(tf_gaps, origin_ns, "no TF update", prefix="/tf ")
    lines += _gap_lines(individual, origin_ns, "no messages")
    for name, pairs in stamps.items():
        if name in steady:
            lines += _stamp_lines(name, pairs, steady[name], origin_ns)
    if len(lines) > MAX_LISTED:
        extra = len(lines) - MAX_LISTED
        lines = [*lines[:MAX_LISTED], f"... and {extra} more anomalies not listed."]
    return lines


# ---- rhythm ---------------------------------------------------------------------


def _sorted(times: Sequence[int]) -> Sequence[int]:
    """`times` in order; most series arrive sorted, so the sort is skipped for them."""
    if all(a <= b for a, b in pairwise(times)):
        return times
    return sorted(times)


def _steady_period(times: Sequence[int], min_intervals: int = MIN_STEADY_INTERVALS) -> float | None:
    """Median interval in ns of a steady series, `None` when it is short, bursty or batched."""
    if len(times) <= min_intervals:
        return None
    ordered = _sorted(times)
    intervals = sorted(b - a for a, b in pairwise(ordered))
    median = intervals[len(intervals) // 2]
    if median <= 0:
        return None
    if intervals[int(0.9 * (len(intervals) - 1))] > STEADY_P90_FACTOR * median:
        return None
    return float(median)


def _gaps(
    series: str,
    times: Sequence[int],
    period_ns: float,
    *,
    factor: float = LOSS_FACTOR,
    floor_s: float = LOSS_FLOOR_S,
) -> list[_Gap]:
    """Silences longer than `factor` x the period and `floor_s` seconds."""
    limit = max(factor * period_ns, floor_s * _NS)
    ordered = _sorted(times)
    return [
        _Gap(series, a, b, period_ns, len(ordered)) for a, b in pairwise(ordered) if b - a > limit
    ]


def _tf_gaps(edges: Mapping[tuple[str, str], Sequence[int]]) -> list[_Gap]:
    out: list[_Gap] = []
    for (parent, child), times in edges.items():
        period = _steady_period(times, TF_MIN_INTERVALS)
        if period is not None:
            series = f"{parent}->{child}"
            out += _gaps(series, times, period, factor=TF_GAP_FACTOR, floor_s=TF_GAP_FLOOR_S)
    return out


# ---- stalls ---------------------------------------------------------------------


def _overlap_ns(a: _Gap, b: _Gap) -> int:
    return min(a.end_ns, b.end_ns) - max(a.start_ns, b.start_ns)


def _split_stalls(gaps: Sequence[_Gap], steady_topics: int) -> tuple[list[_Stall], list[_Gap]]:
    """Gaps that many steady topics share become one stall; the rest stay individual."""
    ordered = sorted(gaps, key=lambda g: g.start_ns)
    used: set[int] = set()
    stalls: list[_Stall] = []
    for i, gap in enumerate(ordered):
        if i in used:
            continue
        members = [
            j
            for j, other in enumerate(ordered)
            if _overlap_ns(gap, other) >= 0.5 * min(gap.length_ns, other.length_ns)
        ]
        topics = sorted({ordered[j].series for j in members})
        if len(topics) < STALL_MIN_TOPICS or len(topics) < STALL_MIN_SHARE * steady_topics:
            continue
        start = max(ordered[j].start_ns for j in members)
        end = min(ordered[j].end_ns for j in members)
        if end > start:
            stalls.append(_Stall(start, end, tuple(topics)))
            used.update(members)
    return stalls, [g for i, g in enumerate(ordered) if i not in used]


def _inside_a_stall(gap: _Gap, stalls: Sequence[_Stall]) -> bool:
    return any(
        min(gap.end_ns, s.end_ns) - max(gap.start_ns, s.start_ns) >= 0.5 * gap.length_ns
        for s in stalls
    )


# ---- formatting -----------------------------------------------------------------


def _t(ns: int, origin_ns: int) -> str:
    return f"t={(ns - origin_ns) / _NS:.2f}s"


def _span(ns: float) -> str:
    return f"{ns / 1e6:.0f} ms" if ns < _NS else f"{ns / _NS:.2f} s"


def _gap_lines(gaps: Sequence[_Gap], origin_ns: int, what: str, *, prefix: str = "") -> list[str]:
    """The longest `MAX_PER_SERIES` gaps of each series, one line each."""
    lines: list[str] = []
    for series in dict.fromkeys(g.series for g in gaps):
        own = sorted((g for g in gaps if g.series == series), key=lambda g: -g.length_ns)
        for g in own[:MAX_PER_SERIES]:
            missing = max(round(g.length_ns / g.period_ns) - 1, 1)
            lines.append(
                f"{prefix}{series}: {what} for {_span(g.length_ns)} from "
                f"{_t(g.start_ns, origin_ns)} to {_t(g.end_ns, origin_ns)} (usual period "
                f"{_span(g.period_ns)}, about {missing} missing, {g.count} messages)"
            )
        if len(own) > MAX_PER_SERIES:
            lines.append(f"{prefix}{series}: {len(own) - MAX_PER_SERIES} more gaps of that kind")
    return lines


def _stall_line(stall: _Stall, origin_ns: int) -> str:
    names = ", ".join(stall.topics[:4]) + (", ..." if len(stall.topics) > 4 else "")
    return (
        f"{len(stall.topics)} steady topics silent together for "
        f"{_span(stall.end_ns - stall.start_ns)} from {_t(stall.start_ns, origin_ns)} to "
        f"{_t(stall.end_ns, origin_ns)} ({names}): a recorder stall or a paused simulation, "
        "not a loss on one topic"
    )


# ---- clock and stamps -----------------------------------------------------------


def _clock_lines(clock: Sequence[tuple[int, int]], origin_ns: int) -> list[str]:
    """Backward steps and large forward steps of `/clock`, in record order."""
    if len(clock) < 2:
        return []
    steps = [(r1, c0, c1, r1 - r0) for (r0, c0), (r1, c1) in pairwise(clock)]
    forward = sorted(c1 - c0 for _r, c0, c1, _d in steps if c1 > c0)
    usual = forward[len(forward) // 2] if len(forward) >= CLOCK_MIN_STEPS else None
    ratios = sorted((c1 - c0) / d for _r, c0, c1, d in steps if d > 0 and c1 > c0)
    rtf = ratios[len(ratios) // 2] if ratios else None
    lines: list[str] = []
    for rec, c0, c1, drec in steps:
        delta = c1 - c0
        if delta < -CLOCK_BACK_TOLERANCE_S * _NS:
            lines.append(
                f"/clock jumped backward {_span(-delta)} at {_t(rec, origin_ns)} "
                f"(clock {c0 / _NS:.3f} s -> {c1 / _NS:.3f} s): a simulation reset or a second "
                "time source"
            )
        elif (
            usual
            and delta > max(CLOCK_JUMP_FACTOR * usual, CLOCK_JUMP_FLOOR_S * _NS)
            # A recorder stall also widens the step: only a step far above the usual
            # sim-to-record ratio is a jump.
            and (drec <= 0 or rtf is None or delta / drec > 5 * rtf)
        ):
            lines.append(
                f"/clock jumped forward {_span(delta)} at {_t(rec, origin_ns)} "
                f"(clock {c0 / _NS:.3f} s -> {c1 / _NS:.3f} s, usual step {_span(usual)})"
            )
    return lines[:MAX_PER_SERIES]


def _stamp_lines(
    topic: str, pairs: Sequence[tuple[int, int]], period_ns: float, origin_ns: int
) -> list[str]:
    """Backward header stamps of one steady topic, over the sampled messages."""
    usable = [(r, s) for r, s in pairs if s > 0]
    if len(usable) < STAMP_MIN_SAMPLES:
        return []
    tolerance = max(STAMP_BACK_FLOOR_S * _NS, STAMP_BACK_FACTOR * period_ns)
    lines: list[str] = []
    previous = usable[0][1]
    for rec, stamp in usable[1:]:
        if stamp < previous - tolerance:
            lines.append(
                f"{topic}: header stamp went backward {_span(previous - stamp)} at "
                f"{_t(rec, origin_ns)} (stamp {previous / _NS:.3f} s -> {stamp / _NS:.3f} s, "
                f"{len(usable)} sampled messages)"
            )
        previous = stamp
    return lines[:MAX_PER_SERIES]
