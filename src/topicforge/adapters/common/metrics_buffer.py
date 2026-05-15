"""Metrics buffer — per-topic temporal metrics accumulated across sample flows.

A bounded, RLock-protected ring buffer per topic, used by the v0.4.0
Phase 2 `topic_metrics` MCP tool to surface frequency, sequence gaps,
and latency percentiles from samples that flow through the adapter's
existing pull paths (`peek_dds_samples`).

Design rules (mirror `lifecycle.py`):

* **Pure logic at module level.** No DDS dependency. Tests pin
  behavior against synthetic input — same convention as
  `parse_topic_list`, `detect_mismatches`, `LifecycleBuffer`.
* **Bounded per-topic.** Each topic's ring caps at
  `MAX_SAMPLES_PER_TOPIC` (default 1000) ; older samples drop out
  when new ones arrive. Memory footprint is bounded by
  `O(topics x 1000 x sample_record_size)` — at 50 topics roughly
  10 MB worst case.
* **Thread-safe.** Cyclone and Fast adapters today fill the buffer
  on the tool-call thread (synchronous), but a future rclpy adapter
  (roadmapped in `docs/product-plan.md §5`) will fire callbacks
  from a binding worker thread. RLock cost is negligible.
* **Opportunistic fill.** The buffer accumulates samples only as
  the existing `peek_dds_samples` path flows them through. No
  background polling thread — same caveat as `LifecycleBuffer`
  for Cyclone in Phase 1. The `topic_metrics` tool description
  surfaces this to LLM callers explicitly.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from typing import Literal

from topicforge.models import TopicMetrics

MAX_SAMPLES_PER_TOPIC = 1000
"""Hard cap on per-topic ring buffer. Drop-oldest on overflow."""

EffectiveMode = Literal["mock", "live"]


@dataclass(frozen=True, slots=True)
class MetricsSample:
    """Minimal per-sample record captured at the adapter's sample-flow site.

    All fields are produced inside the adapter where the binding
    surfaces the sample. `receive_ns` is `time.time_ns()` at capture
    moment (wall clock, NOT DDS-RTPS receive timestamp — neither
    binding exposes the underlying RTPS timestamp through Python
    reliably). `sequence_number` and `publish_ns` are best-effort —
    `None` when the sample type doesn't expose them.
    """

    topic: str
    receive_ns: int
    sequence_number: int | None
    publish_ns: int | None
    domain_id: int


class MetricsBuffer:
    """Per-topic bounded ring + percentile/frequency computation."""

    def __init__(self, *, max_samples_per_topic: int = MAX_SAMPLES_PER_TOPIC) -> None:
        self._lock = threading.RLock()
        self._cap = max_samples_per_topic
        self._samples: dict[str, deque[MetricsSample]] = {}

    # --------------------------- mutating ------------------------------

    def record(
        self,
        *,
        topic: str,
        receive_ns: int,
        sequence_number: int | None,
        publish_ns: int | None,
        domain_id: int,
    ) -> None:
        """Append one sample to the per-topic ring. Oldest evicted on cap."""
        with self._lock:
            ring = self._samples.get(topic)
            if ring is None:
                ring = deque(maxlen=self._cap)
                self._samples[topic] = ring
            ring.append(
                MetricsSample(
                    topic=topic,
                    receive_ns=receive_ns,
                    sequence_number=sequence_number,
                    publish_ns=publish_ns,
                    domain_id=domain_id,
                )
            )

    # ---------------------- read-only computation ----------------------

    def compute_metrics(
        self,
        *,
        topic: str,
        window_seconds: int,
        now_ns: int | None = None,
        declared_hz: float | None = None,
        mode_effective: EffectiveMode = "live",
        domain_id: int = 0,
    ) -> TopicMetrics:
        """Build a `TopicMetrics` for `topic` over the last `window_seconds`.

        `now_ns` defaults to `time.time_ns()` and is injectable for
        deterministic tests. `declared_hz` comes from the topic's
        QoS Deadline policy (when the adapter has resolved it ;
        callers pass `None` when unknown).

        Returns an `empty` `TopicMetrics` (samples_observed=0, all
        None / 0 metrics) when the topic has no recorded samples
        within the window.
        """
        if now_ns is None:
            import time

            now_ns = time.time_ns()
        cutoff_ns = now_ns - window_seconds * 1_000_000_000

        with self._lock:
            ring = self._samples.get(topic)
            samples = (
                [s for s in ring if s.receive_ns >= cutoff_ns and s.domain_id == domain_id]
                if ring is not None
                else []
            )

        samples_observed = len(samples)
        if samples_observed == 0:
            # Empty path — every metric collapses to its zero-value.
            return TopicMetrics(
                topic=topic,
                window_seconds=window_seconds,
                window_seconds_actual=0.0,
                samples_observed=0,
                frequency_hz_observed=None,
                frequency_hz_declared=declared_hz,
                sequence_gaps_count=0,
                sequence_numbers_available=False,
                latency_ns_p50=None,
                latency_ns_p95=None,
                latency_ns_p99=None,
                latency_available=False,
                mode_effective=mode_effective,
            )

        # window_seconds_actual reflects the actual elapsed range
        # within the window — useful when the buffer is younger than
        # `window_seconds` (e.g., server just started).
        oldest_ns = min(s.receive_ns for s in samples)
        elapsed_ns = max(now_ns - oldest_ns, 1)  # >=1 ns to avoid /0
        window_actual_s = elapsed_ns / 1_000_000_000

        # A single sample doesn't define a frequency.
        freq_observed: float | None = (
            samples_observed / window_actual_s if samples_observed >= 2 else None
        )

        seq_numbers = [s.sequence_number for s in samples if s.sequence_number is not None]
        seq_available = len(seq_numbers) > 0
        gaps_count = _count_sequence_gaps(seq_numbers) if seq_available else 0

        latencies = [
            s.receive_ns - s.publish_ns
            for s in samples
            if s.publish_ns is not None and s.receive_ns >= s.publish_ns
        ]
        latency_available = len(latencies) > 0
        if latency_available:
            latencies_sorted = sorted(latencies)
            p50 = _percentile(latencies_sorted, 50)
            p95 = _percentile(latencies_sorted, 95)
            p99 = _percentile(latencies_sorted, 99)
        else:
            p50 = p95 = p99 = None

        return TopicMetrics(
            topic=topic,
            window_seconds=window_seconds,
            window_seconds_actual=window_actual_s,
            samples_observed=samples_observed,
            frequency_hz_observed=freq_observed,
            frequency_hz_declared=declared_hz,
            sequence_gaps_count=gaps_count,
            sequence_numbers_available=seq_available,
            latency_ns_p50=p50,
            latency_ns_p95=p95,
            latency_ns_p99=p99,
            latency_available=latency_available,
            mode_effective=mode_effective,
        )

    def snapshot_topics(self) -> list[str]:
        """Return the list of topic names currently tracked."""
        with self._lock:
            return list(self._samples.keys())

    def sample_count(self, topic: str) -> int:
        """Diagnostic helper — number of samples currently buffered for `topic`."""
        with self._lock:
            ring = self._samples.get(topic)
            return len(ring) if ring is not None else 0


# ---------------------------------------------------------------------------
# Pure helpers — testable without the buffer
# ---------------------------------------------------------------------------


def _count_sequence_gaps(seq_numbers: list[int]) -> int:
    """Count missing entries in the observed sequence number list.

    Sorts the input and counts the gaps between consecutive values.
    Out-of-order arrivals are tolerated (we sort first). Duplicates
    are deduplicated before counting — they should not contribute to
    a gap claim.

    Example: [0, 1, 2, 5, 6] → 2 gaps (3 and 4 missing).
    """
    if len(seq_numbers) < 2:
        return 0
    from itertools import pairwise

    unique = sorted(set(seq_numbers))
    gaps = 0
    for prev, curr in pairwise(unique):
        diff = curr - prev
        if diff > 1:
            gaps += diff - 1
    return gaps


def _percentile(sorted_values: list[int], q: int) -> int | None:
    """Nearest-rank percentile over a pre-sorted integer list.

    `q` is in `1..100`. Returns the value at the nearest-rank index
    (matches the `numpy.percentile(..., interpolation="lower")`
    convention closely enough for diagnostics). `None` for empty
    input. Pure Python so the OSS core stays NumPy-free.
    """
    if not sorted_values:
        return None
    if q <= 0:
        return sorted_values[0]
    if q >= 100:
        return sorted_values[-1]
    # Nearest-rank: idx = ceil(q/100 * n) - 1, clamped.
    n = len(sorted_values)
    idx = max(0, min(n - 1, (q * n + 99) // 100 - 1))
    return sorted_values[idx]
