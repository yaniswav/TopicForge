"""Observations in a bag, from record times and decoded stamps (binding-free, pure).

The strings say what was seen, never why: a silence may be a lost message, a paused recorder
or a node that stopped, and only sequence numbers could tell. Every threshold is relative to
the series' own rhythm, with a minimum sample count and an absolute floor, because a false
alarm on a healthy bag costs more than a missed marginal gap:

* clock: `/clock` going backward, or a forward step far above its usual step;
* silence: a steady series (low spread of intervals) with a gap far above its median period,
  inside the recording or running to its end. `/tf` as a whole is such a series. Two or more
  series silent over the same span are one line. Time before a series' first message is not
  a gap;
* TF edge: an edge present for most of `/tf` that goes silent while `/tf` goes on;
* stamps: a header stamp going backward within one `frame_id` of a steady topic.

Limit: a series whose intervals are irregular (the 90th percentile above twice the median) is
not judged, so sustained irregular loss goes unseen. `find_anomalies` is given only the
series the caller expects to be steady (sensor and state types).
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from itertools import pairwise

_NS = 1_000_000_000

MIN_STEADY_INTERVALS = 30  # a topic needs this many intervals to count as steady
STEADY_P90_FACTOR = 2.0  # steady: the 90th percentile interval is at most 2 x the median
GAP_FACTOR = 5.0  # a silence exceeds 5 x the median period ...
GAP_FLOOR_S = 0.2  # ... and this many seconds
TF_MIN_INTERVALS = 19  # an edge needs 20 messages to be judged
TF_GAP_FACTOR = 10.0
TF_GAP_FLOOR_S = 0.5
TF_MIN_COVERAGE = 0.8  # an edge must span this share of the `/tf` span to be judged
CLOCK_BACK_TOLERANCE_S = 0.001
CLOCK_JUMP_FACTOR = 20.0  # a forward jump exceeds 20 x the usual step ...
CLOCK_JUMP_FLOOR_S = 1.0  # ... and this many seconds
CLOCK_MIN_STEPS = 10  # steps needed to know the usual clock step
STAMP_BACK_FLOOR_S = 0.5
STAMP_BACK_FACTOR = 5.0
STAMP_MIN_SAMPLES = 5
GROUP_MIN_SERIES = 2  # this many series silent over the same span make one line
MAX_GAPS_PER_SERIES = 20  # the longest gaps kept per series, to bound the grouping
MAX_PER_SERIES = 3
MAX_LISTED = 12


@dataclass(frozen=True)
class _Gap:
    """A silence of one series: between two of its messages, or from its last one to the end."""

    series: str
    start_ns: int
    end_ns: int
    period_ns: float
    to_end: bool = False

    @property
    def length_ns(self) -> int:
        return self.end_ns - self.start_ns


@dataclass
class _Cluster:
    start_ns: int
    end_ns: int
    gaps: list[_Gap] = field(default_factory=list)


def find_anomalies(
    *,
    origin_ns: int,
    end_ns: int,
    clock: Sequence[tuple[int, int]],
    tf_edges: Mapping[tuple[str, str], Sequence[int]],
    topic_times: Mapping[str, Sequence[int]],
    stamps: Mapping[str, Sequence[tuple[int, int, str]]],
    deadline: float | None = None,
) -> tuple[list[str], bool]:
    """`(anomaly strings, cut)`; at most `MAX_LISTED` strings, `cut` when `deadline` ran out.

    `origin_ns` is the record time of the first message, the zero of every offset; `end_ns`
    the last record time covered. `clock` holds `(record_ns, clock_ns)` of `/clock`;
    `tf_edges` the record times of each dynamic `(parent, child)` edge; `topic_times` the
    record times of the series to judge for silence (`/tf` among them); `stamps` the sampled
    `(record_ns, header_stamp_ns, frame_id)` of topics to judge for backward stamps.
    """
    cut = False

    def out_of_time() -> bool:
        return deadline is not None and time.monotonic() >= deadline

    lines = _clock_lines(clock, origin_ns)
    steady: dict[str, float] = {}
    gaps: list[_Gap] = []
    for name, times in topic_times.items():
        if out_of_time():
            cut = True
            break
        ordered = _sorted(times)
        if (period := _steady_period(ordered)) is not None:
            steady[name] = period
            gaps += _gaps(name, ordered, period, end_ns)
    clusters = _cluster(gaps)
    lines += [_cluster_line(c, origin_ns, len(steady)) for c in clusters if _grouped(c)]
    singles = [c.gaps[0] for c in clusters if not _grouped(c)]
    lines += _gap_lines(singles, origin_ns)
    if not cut:
        edge_gaps = _tf_gaps(tf_edges, topic_times.get("/tf"), clusters)
        lines += _gap_lines(edge_gaps, origin_ns, prefix="/tf ")
    for name, pairs in stamps.items():
        if out_of_time():
            cut = True
            break
        if name in steady:
            lines += _stamp_lines(name, pairs, steady[name], origin_ns)
    if len(lines) > MAX_LISTED:
        extra = len(lines) - MAX_LISTED
        lines = [*lines[:MAX_LISTED], f"... and {extra} more observations not listed."]
    return lines, cut


# ---- rhythm ---------------------------------------------------------------------


def _sorted(times: Sequence[int]) -> Sequence[int]:
    """`times` in order; most series arrive sorted, so the sort is skipped for them."""
    if all(a <= b for a, b in pairwise(times)):
        return times
    return sorted(times)


def _steady_period(
    ordered: Sequence[int], min_intervals: int = MIN_STEADY_INTERVALS
) -> float | None:
    """Median interval in ns of a steady series, `None` when it is short, bursty or batched."""
    if len(ordered) <= min_intervals:
        return None
    intervals = sorted(b - a for a, b in pairwise(ordered))
    median = intervals[len(intervals) // 2]
    if median <= 0:
        return None
    if intervals[int(0.9 * (len(intervals) - 1))] > STEADY_P90_FACTOR * median:
        return None
    return float(median)


def _gaps(
    series: str,
    ordered: Sequence[int],
    period_ns: float,
    end_ns: int,
    *,
    factor: float = GAP_FACTOR,
    floor_s: float = GAP_FLOOR_S,
    with_tail: bool = True,
) -> list[_Gap]:
    """The longest `MAX_GAPS_PER_SERIES` silences above `factor` x the period and `floor_s`."""
    limit = max(factor * period_ns, floor_s * _NS)
    found = [_Gap(series, a, b, period_ns) for a, b in pairwise(ordered) if b - a > limit]
    if with_tail and ordered and end_ns - ordered[-1] > limit:
        found.append(_Gap(series, ordered[-1], end_ns, period_ns, to_end=True))
    found.sort(key=lambda g: -g.length_ns)
    return found[:MAX_GAPS_PER_SERIES]


def _tf_gaps(
    edges: Mapping[tuple[str, str], Sequence[int]],
    tf_times: Sequence[int] | None,
    clusters: Sequence[_Cluster],
) -> list[_Gap]:
    """Silent edges of a `/tf` that goes on, for edges present for most of the `/tf` span."""
    if not tf_times:
        return []
    span = max(tf_times) - min(tf_times)
    out: list[_Gap] = []
    for (parent, child), times in edges.items():
        ordered = _sorted(times)
        if span <= 0 or ordered[-1] - ordered[0] < TF_MIN_COVERAGE * span:
            continue
        if (period := _steady_period(ordered, TF_MIN_INTERVALS)) is None:
            continue
        found = _gaps(
            f"{parent}->{child}",
            ordered,
            period,
            ordered[-1],
            factor=TF_GAP_FACTOR,
            floor_s=TF_GAP_FLOOR_S,
            with_tail=False,
        )
        out += [g for g in found if not _inside_a_cluster(g, clusters)]
    return out


# ---- grouping ---------------------------------------------------------------------


def _cluster(gaps: Sequence[_Gap]) -> list[_Cluster]:
    """Sweep the gaps by start; those that still overlap their cluster's common span join it."""
    clusters: list[_Cluster] = []
    for gap in sorted(gaps, key=lambda g: g.start_ns):
        current = clusters[-1] if clusters else None
        if current is not None and _joins(current, gap):
            current.start_ns = max(current.start_ns, gap.start_ns)
            current.end_ns = min(current.end_ns, gap.end_ns)
            current.gaps.append(gap)
        else:
            clusters.append(_Cluster(gap.start_ns, gap.end_ns, [gap]))
    return clusters


def _joins(cluster: _Cluster, gap: _Gap) -> bool:
    common = min(cluster.end_ns, gap.end_ns) - max(cluster.start_ns, gap.start_ns)
    return common > 0 and common >= 0.5 * min(cluster.end_ns - cluster.start_ns, gap.length_ns)


def _grouped(cluster: _Cluster) -> bool:
    return len({g.series for g in cluster.gaps}) >= GROUP_MIN_SERIES


def _inside_a_cluster(gap: _Gap, clusters: Sequence[_Cluster]) -> bool:
    return any(
        min(gap.end_ns, c.end_ns) - max(gap.start_ns, c.start_ns) >= 0.5 * gap.length_ns
        for c in clusters
    )


# ---- formatting -----------------------------------------------------------------


def _t(ns: int, origin_ns: int) -> str:
    return f"t={(ns - origin_ns) / _NS:.2f}s"


def _span(ns: float) -> str:
    return f"{ns / 1e6:.0f} ms" if ns < _NS else f"{ns / _NS:.2f} s"


def _gap_lines(gaps: Sequence[_Gap], origin_ns: int, *, prefix: str = "") -> list[str]:
    """The longest `MAX_PER_SERIES` gaps of each series, one line each."""
    lines: list[str] = []
    for series in dict.fromkeys(g.series for g in gaps):
        own = sorted((g for g in gaps if g.series == series), key=lambda g: -g.length_ns)
        lines += [_gap_line(g, origin_ns, prefix) for g in own[:MAX_PER_SERIES]]
        if len(own) > MAX_PER_SERIES:
            lines.append(f"{prefix}{series}: {len(own) - MAX_PER_SERIES} more silences")
    return lines


def _gap_line(g: _Gap, origin_ns: int, prefix: str) -> str:
    usual = f"usual period {_span(g.period_ns)}"
    if g.to_end:
        return (
            f"{prefix}{g.series}: no message since {_t(g.start_ns, origin_ns)}, "
            f"{_span(g.length_ns)} before the end of the data at {_t(g.end_ns, origin_ns)} "
            f"({usual})"
        )
    return (
        f"{prefix}{g.series}: no message for {_span(g.length_ns)} from "
        f"{_t(g.start_ns, origin_ns)} to {_t(g.end_ns, origin_ns)} ({usual})"
    )


def _cluster_line(cluster: _Cluster, origin_ns: int, judged: int) -> str:
    names = sorted({g.series for g in cluster.gaps})
    shown = ", ".join(names[:4]) + (", ..." if len(names) > 4 else "")
    return (
        f"{len(names)} of {judged} topics silent together for "
        f"{_span(cluster.end_ns - cluster.start_ns)} at {_t(cluster.start_ns, origin_ns)} "
        f"({shown}), possibly a recording pause"
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
                f"/clock stepped backward {_span(-delta)} at {_t(rec, origin_ns)} "
                f"(clock {c0 / _NS:.3f} s -> {c1 / _NS:.3f} s)"
            )
        elif (
            usual
            and delta > max(CLOCK_JUMP_FACTOR * usual, CLOCK_JUMP_FLOOR_S * _NS)
            # A recording pause also widens the step: only a step far above the usual
            # sim-to-record ratio is a jump.
            and (drec <= 0 or rtf is None or delta / drec > 5 * rtf)
        ):
            lines.append(
                f"/clock stepped forward {_span(delta)} at {_t(rec, origin_ns)} "
                f"(clock {c0 / _NS:.3f} s -> {c1 / _NS:.3f} s, usual step {_span(usual)})"
            )
    return lines[:MAX_PER_SERIES]


def _stamp_lines(
    topic: str, pairs: Sequence[tuple[int, int, str]], period_ns: float, origin_ns: int
) -> list[str]:
    """Backward header stamps of one steady topic, compared within each `frame_id`."""
    tolerance = max(STAMP_BACK_FLOOR_S * _NS, STAMP_BACK_FACTOR * period_ns)
    lines: list[str] = []
    for frame in dict.fromkeys(f for _r, _s, f in pairs):
        usable = [(r, s) for r, s, f in pairs if f == frame and s > 0]
        if len(usable) < STAMP_MIN_SAMPLES:
            continue
        previous = usable[0][1]
        for rec, stamp in usable[1:]:
            if stamp < previous - tolerance:
                lines.append(
                    f"{topic}: header stamp stepped backward {_span(previous - stamp)} at "
                    f"{_t(rec, origin_ns)} (stamp {previous / _NS:.3f} s -> {stamp / _NS:.3f} s, "
                    f"frame {frame!r}, {len(usable)} sampled messages)"
                )
            previous = stamp
    return lines[:MAX_PER_SERIES]
