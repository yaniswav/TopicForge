"""Per-topic metrics buffer behind `topic_metrics`.

A bounded ring per topic, guarded by an RLock, yields frequency, sequence
gaps and latency percentiles from the samples that pass through
`peek_dds_samples`. There is no polling thread: the buffer only fills when
a peek happens, which the `topic_metrics` tool description tells callers.

Each ring holds at most `MAX_SAMPLES_PER_TOPIC` samples and at most
`MAX_TOPICS` topics are tracked (oldest inserted is evicted), so a churny
bus cannot grow memory without bound. No DDS import, so tests use
synthetic samples.
"""

from __future__ import annotations

import threading
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Literal

from topicforge.models import TopicMetrics

MAX_SAMPLES_PER_TOPIC = 1000
"""Hard cap on per-topic ring buffer. Drop-oldest on overflow."""

MAX_TOPICS = 4096
"""Hard cap on the number of distinct topics tracked. Oldest-inserted topic
evicted on overflow so a churny bus cannot grow the map without bound."""

EffectiveMode = Literal["mock", "live"]


@dataclass(frozen=True, slots=True)
class MetricsSample:
    """One captured sample.

    `receive_ns` is the wall clock at capture, not an RTPS receive
    timestamp (neither binding exposes that reliably in Python).
    `sequence_number`, `publish_ns` and `writer_guid` are `None` when the
    binding does not expose them. `writer_guid` lets `compute_metrics` count
    sequence gaps per writer.
    """

    topic: str
    receive_ns: int
    sequence_number: int | None
    publish_ns: int | None
    domain_id: int
    writer_guid: str | None = None


class MetricsBuffer:
    """Per-topic bounded ring + percentile/frequency computation."""

    def __init__(
        self,
        *,
        max_samples_per_topic: int = MAX_SAMPLES_PER_TOPIC,
        max_topics: int = MAX_TOPICS,
    ) -> None:
        self._lock = threading.RLock()
        self._cap = max_samples_per_topic
        self._max_topics = max_topics
        self._samples: dict[str, deque[MetricsSample]] = {}

    def record(
        self,
        *,
        topic: str,
        receive_ns: int,
        sequence_number: int | None,
        publish_ns: int | None,
        domain_id: int,
        writer_guid: str | None = None,
    ) -> None:
        """Append one sample to the per-topic ring. Oldest evicted on cap."""
        with self._lock:
            ring = self._samples.get(topic)
            if ring is None:
                if len(self._samples) >= self._max_topics:
                    # Evict the oldest-inserted topic to keep the map bounded.
                    oldest = next(iter(self._samples), None)
                    if oldest is not None:
                        del self._samples[oldest]
                ring = deque(maxlen=self._cap)
                self._samples[topic] = ring
            ring.append(
                MetricsSample(
                    topic=topic,
                    receive_ns=receive_ns,
                    sequence_number=sequence_number,
                    publish_ns=publish_ns,
                    domain_id=domain_id,
                    writer_guid=writer_guid,
                )
            )

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

        `now_ns` defaults to `time.time_ns()` and exists for deterministic
        tests. `declared_hz` is derived from the QoS Deadline; pass `None`
        when unknown. A topic with no samples in the window yields an empty
        `TopicMetrics` (`samples_observed=0`, no metrics).
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

        # The actual window can be shorter than requested (server just started).
        receive_times = [s.receive_ns for s in samples]
        oldest_ns = min(receive_times)
        newest_ns = max(receive_times)
        elapsed_ns = max(now_ns - oldest_ns, 1)  # >=1 ns to avoid /0
        window_actual_s = elapsed_ns / 1_000_000_000

        # Frequency is N-1 intervals over (newest - oldest), not (now - oldest),
        # which would include idle time since the last peek. Samples from one
        # peek share a receive_ns (span 0) and give no frequency rather than
        # an invented one.
        sample_span_ns = newest_ns - oldest_ns
        freq_observed: float | None = (
            (samples_observed - 1) / (sample_span_ns / 1_000_000_000)
            if samples_observed >= 2 and sample_span_ns > 0
            else None
        )

        # Per writer: merging independent writers would read their counter
        # offset as one huge gap.
        seq_by_writer: dict[str | None, list[int]] = defaultdict(list)
        for s in samples:
            if s.sequence_number is not None:
                seq_by_writer[s.writer_guid].append(s.sequence_number)
        seq_available = len(seq_by_writer) > 0
        gaps_count = sum(_count_sequence_gaps(seqs) for seqs in seq_by_writer.values())

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
        """Diagnostic helper: number of samples currently buffered for `topic`."""
        with self._lock:
            ring = self._samples.get(topic)
            return len(ring) if ring is not None else 0


# A hole wider than this between consecutive sequence numbers is read as a
# publisher restart or counter wrap, not as that many lost samples.
_MAX_PLAUSIBLE_GAP = 10_000


def _count_sequence_gaps(seq_numbers: list[int]) -> int:
    """Count missing entries in one writer's sequence numbers.

    Input is sorted and deduplicated, so out-of-order arrival is fine. A
    hole wider than `_MAX_PLAUSIBLE_GAP` is skipped as a reset or wrap.
    Example: [0, 1, 2, 5, 6] -> 2 (3 and 4 missing).
    """
    if len(seq_numbers) < 2:
        return 0
    from itertools import pairwise

    unique = sorted(set(seq_numbers))
    gaps = 0
    for prev, curr in pairwise(unique):
        diff = curr - prev
        if 1 < diff <= _MAX_PLAUSIBLE_GAP:
            gaps += diff - 1
    return gaps


def _percentile(sorted_values: list[int], q: int) -> int | None:
    """Nearest-rank percentile (`q` in 1..100) of a sorted list; `None` if empty."""
    if not sorted_values:
        return None
    if q <= 0:
        return sorted_values[0]
    if q >= 100:
        return sorted_values[-1]
    n = len(sorted_values)
    idx = max(0, min(n - 1, (q * n + 99) // 100 - 1))
    return sorted_values[idx]
