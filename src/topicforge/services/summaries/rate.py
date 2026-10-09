"""Observed rate of a topic and its verdict, from arrival times already collected.

Pure arithmetic over integer nanoseconds. The thresholds and the evaluation
order are those of docs/CONTRACT.md section 4.
"""

from __future__ import annotations

import itertools
import math
import statistics
from collections.abc import Sequence
from typing import Literal

from topicforge.models.summaries import RateVerdict, TopicRate

_NS_PER_S = 1_000_000_000

MIN_SAMPLES_FOR_VERDICT = 5
GAP_FACTOR = 3.0
STABLE_CV = 0.2
JITTERY_CV = 0.5


def compute_rate(
    arrivals_ns: Sequence[int],
    *,
    basis: Literal["received_ns", "recorded_ns"],
    stamps_ns: Sequence[int] | None = None,
    window_start_ns: int | None = None,
    window_end_ns: int | None = None,
    stopped_on_deadline: bool = False,
) -> TopicRate:
    """Rate block for messages that arrived (or were recorded) at `arrivals_ns`.

    `window_start_ns` and `window_end_ns` bound a live observation; without
    them the window is the span of the arrivals. The trailing interval (last
    message to `window_end_ns`) is measured, and counted for `intermittent`,
    only when `stopped_on_deadline` is true. `stamps_ns` are the messages' own
    stamps, given only when every message has one, to get `sim_frequency_hz`.
    """
    times = sorted(arrivals_ns)
    n = len(times)
    start = times[0] if window_start_ns is None and times else window_start_ns
    end = times[-1] if window_end_ns is None and times else window_end_ns
    window_s = 0.0 if start is None or end is None else max(0.0, (end - start) / _NS_PER_S)
    trailing_ns = _trailing_ns(times, window_end_ns, stopped_on_deadline)

    intervals = [b - a for a, b in itertools.pairwise(times)]
    mean_ns = statistics.fmean(intervals) if intervals else None
    median_ns = statistics.median(intervals) if intervals else None
    cv = _cv(intervals, mean_ns)
    span_ns = times[-1] - times[0] if n >= 2 else 0
    verdict, note = _verdict(n, window_s, intervals, median_ns, trailing_ns, cv, mean_ns, span_ns)
    return TopicRate(
        basis=basis,
        message_count=n,
        window_s=_round(window_s),
        mean_interval_s=_seconds(mean_ns),
        interval_median_s=_seconds(median_ns),
        max_gap_s=_seconds(max(intervals) if intervals else None),
        interval_cv=None if cv is None else _round(cv),
        observed_frequency_hz=_frequency(n, span_ns),
        sim_frequency_hz=_sim_frequency(stamps_ns),
        trailing_gap_s=_seconds(trailing_ns),
        verdict=verdict,
        verdict_note=note,
    )


def _trailing_ns(times: list[int], end_ns: int | None, stopped_on_deadline: bool) -> int | None:
    if not stopped_on_deadline or end_ns is None or not times:
        return None
    return max(0, end_ns - times[-1])


def _cv(intervals: list[int], mean_ns: float | None) -> float | None:
    """Population standard deviation over the mean; `None` without a positive mean."""
    if not intervals or mean_ns is None or mean_ns <= 0:
        return None
    return statistics.pstdev(intervals) / mean_ns


def _verdict(
    n: int,
    window_s: float,
    intervals: list[int],
    median_ns: float | None,
    trailing_ns: int | None,
    cv: float | None,
    mean_ns: float | None,
    span_ns: int,
) -> tuple[RateVerdict, str]:
    if n == 0:
        return "silent", f"No message arrived in the {window_s:.1f} s window."
    if n < MIN_SAMPLES_FOR_VERDICT:
        return "insufficient", (
            f"Only {n} message(s) in {window_s:.1f} s; {MIN_SAMPLES_FOR_VERDICT} are needed "
            "for a verdict (ask for count >= 10)."
        )
    gap = max([*intervals, *([trailing_ns] if trailing_ns is not None else [])])
    if median_ns is not None and gap > GAP_FACTOR * median_ns:
        where = "after the last message" if trailing_ns == gap else "between messages"
        return "intermittent", (
            f"A gap of {gap / _NS_PER_S:.2f} s {where} is more than {GAP_FACTOR:g} times the "
            f"median interval of {median_ns / _NS_PER_S:.3f} s."
        )
    if cv is None or mean_ns is None:
        return "erratic", "The messages arrived in one burst, so no steady rate can be measured."
    hz = _frequency(n, span_ns)
    if cv < STABLE_CV:
        return "stable", f"Steady delivery at {hz:.1f} Hz (interval variation {cv:.2f})."
    if cv < JITTERY_CV:
        return "jittery", f"Delivery at about {hz:.1f} Hz with uneven spacing (variation {cv:.2f})."
    return "erratic", (
        f"Very irregular spacing (variation {cv:.2f}); a topic with several publishers, "
        "such as /tf, reads this way without being broken."
    )


def _frequency(n: int, span_ns: int) -> float | None:
    if n < 2 or span_ns <= 0:
        return None
    return _round((n - 1) / (span_ns / _NS_PER_S))


def _sim_frequency(stamps_ns: Sequence[int] | None) -> float | None:
    if not stamps_ns or len(stamps_ns) < 2:
        return None
    return _frequency(len(stamps_ns), stamps_ns[-1] - stamps_ns[0])


def _seconds(value_ns: float | None) -> float | None:
    return None if value_ns is None else _round(value_ns / _NS_PER_S)


def _round(value: float) -> float:
    return value if not math.isfinite(value) else round(value, 6)
