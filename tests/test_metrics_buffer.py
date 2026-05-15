"""Unit tests for `topicforge.adapters.common.metrics_buffer.MetricsBuffer`.

Pure logic — no DDS dependency. Same convention as
`tests/test_qos_analyzer.py` and `tests/test_lifecycle_buffer.py`.
"""

from __future__ import annotations

import threading

from topicforge.adapters.common.metrics_buffer import (
    MAX_SAMPLES_PER_TOPIC,
    MetricsBuffer,
    _count_sequence_gaps,
    _percentile,
)

# ---------------------------------------------------------------------------
# Pure helpers — _count_sequence_gaps + _percentile
# ---------------------------------------------------------------------------


def test_count_sequence_gaps_empty() -> None:
    assert _count_sequence_gaps([]) == 0


def test_count_sequence_gaps_single() -> None:
    assert _count_sequence_gaps([42]) == 0


def test_count_sequence_gaps_no_gaps() -> None:
    assert _count_sequence_gaps([0, 1, 2, 3, 4]) == 0


def test_count_sequence_gaps_single_gap() -> None:
    # 0,1,2 then jump to 5 → missing 3,4 = 2 gaps
    assert _count_sequence_gaps([0, 1, 2, 5, 6]) == 2


def test_count_sequence_gaps_multiple_gaps() -> None:
    # 0 then 3 (missing 1,2) then 10 (missing 4..9 = 6) → 2 + 6 = 8
    assert _count_sequence_gaps([0, 3, 10]) == 8


def test_count_sequence_gaps_out_of_order_arrival() -> None:
    # Same sequence as no_gaps, just out of order — gap count unchanged
    assert _count_sequence_gaps([3, 1, 0, 4, 2]) == 0


def test_count_sequence_gaps_dedupes_duplicates() -> None:
    # Duplicate seq# arrival should NOT inflate the gap count
    assert _count_sequence_gaps([0, 0, 1, 2, 2]) == 0


def test_percentile_empty_returns_none() -> None:
    assert _percentile([], 50) is None


def test_percentile_single_value() -> None:
    assert _percentile([42], 50) == 42
    assert _percentile([42], 99) == 42


def test_percentile_synthetic_distribution() -> None:
    values = sorted(range(1, 101))  # 1..100 inclusive
    # Nearest-rank: p50 = ceil(0.50*100)-1 = 49 → values[49] = 50
    assert _percentile(values, 50) == 50
    assert _percentile(values, 95) == 95
    assert _percentile(values, 99) == 99


def test_percentile_clamps_at_bounds() -> None:
    values = [10, 20, 30]
    assert _percentile(values, 0) == 10
    assert _percentile(values, 100) == 30


# ---------------------------------------------------------------------------
# Record + retrieve + window filter
# ---------------------------------------------------------------------------


def _record_burst(
    buf: MetricsBuffer,
    *,
    topic: str,
    count: int,
    start_ns: int,
    interval_ns: int,
    publish_offset_ns: int | None = None,
    domain_id: int = 0,
) -> None:
    """Helper: record `count` samples at `interval_ns` spacing from `start_ns`."""
    for i in range(count):
        receive = start_ns + i * interval_ns
        buf.record(
            topic=topic,
            receive_ns=receive,
            sequence_number=i,
            publish_ns=(receive - publish_offset_ns) if publish_offset_ns is not None else None,
            domain_id=domain_id,
        )


def test_empty_buffer_returns_zero_samples_observed() -> None:
    buf = MetricsBuffer()
    m = buf.compute_metrics(topic="/nope", window_seconds=60, now_ns=1_000_000_000)
    assert m.samples_observed == 0
    assert m.frequency_hz_observed is None
    assert m.window_seconds_actual == 0.0
    assert m.sequence_gaps_count == 0
    assert m.sequence_numbers_available is False
    assert m.latency_available is False


def test_record_then_compute_basic_frequency() -> None:
    buf = MetricsBuffer()
    # 10 Hz: 100 ms interval, 100 samples, no publish offset.
    _record_burst(
        buf,
        topic="/x",
        count=100,
        start_ns=0,
        interval_ns=100_000_000,
    )
    # now_ns = last sample receive (9_900_000_000)
    m = buf.compute_metrics(topic="/x", window_seconds=60, now_ns=9_900_000_000)
    assert m.samples_observed == 100
    # Elapsed = last - first = 9_900_000_000 ns = 9.9 s ; 100/9.9 ≈ 10.10
    assert m.frequency_hz_observed is not None
    assert 9.5 < m.frequency_hz_observed < 10.5


def test_window_filter_drops_old_samples() -> None:
    buf = MetricsBuffer()
    # 60 samples spaced 1 s apart (1 Hz, 60s total)
    _record_burst(
        buf,
        topic="/y",
        count=60,
        start_ns=0,
        interval_ns=1_000_000_000,
    )
    # Window = 10 s, now = 60 s → only last 10 samples survive
    m = buf.compute_metrics(topic="/y", window_seconds=10, now_ns=60_000_000_000)
    # Samples with receive_ns >= now - 10s = 50e9 — that's samples
    # with index >= 50, so 10 samples (50..59).
    assert m.samples_observed == 10


def test_single_sample_returns_none_frequency() -> None:
    buf = MetricsBuffer()
    buf.record(
        topic="/y", receive_ns=1_000_000_000, sequence_number=0, publish_ns=None, domain_id=0
    )
    m = buf.compute_metrics(topic="/y", window_seconds=10, now_ns=2_000_000_000)
    assert m.samples_observed == 1
    assert m.frequency_hz_observed is None  # cannot define freq from 1 sample


def test_sequence_numbers_unavailable_when_all_none() -> None:
    buf = MetricsBuffer()
    for i in range(5):
        buf.record(
            topic="/z",
            receive_ns=i * 1_000_000_000,
            sequence_number=None,
            publish_ns=None,
            domain_id=0,
        )
    m = buf.compute_metrics(topic="/z", window_seconds=60, now_ns=5_000_000_000)
    assert m.sequence_numbers_available is False
    assert m.sequence_gaps_count == 0


def test_sequence_gaps_detected_in_window() -> None:
    buf = MetricsBuffer()
    # 0,1,2 then gap 3,4 missing then 5,6 → 2 gaps
    for seq in (0, 1, 2, 5, 6):
        buf.record(
            topic="/seq",
            receive_ns=seq * 100_000_000,
            sequence_number=seq,
            publish_ns=None,
            domain_id=0,
        )
    m = buf.compute_metrics(topic="/seq", window_seconds=60, now_ns=1_000_000_000)
    assert m.sequence_numbers_available is True
    assert m.sequence_gaps_count == 2


def test_latency_percentiles_when_publish_ns_available() -> None:
    buf = MetricsBuffer()
    # 10 samples each with publish_ns = receive_ns - 50_000_000 (50 ms latency)
    _record_burst(
        buf,
        topic="/lat",
        count=10,
        start_ns=0,
        interval_ns=100_000_000,
        publish_offset_ns=50_000_000,
    )
    m = buf.compute_metrics(topic="/lat", window_seconds=60, now_ns=1_000_000_000)
    assert m.latency_available is True
    # All latencies are exactly 50 ms ; every percentile equals 50_000_000
    assert m.latency_ns_p50 == 50_000_000
    assert m.latency_ns_p95 == 50_000_000
    assert m.latency_ns_p99 == 50_000_000


def test_latency_skipped_when_no_publish_ns() -> None:
    buf = MetricsBuffer()
    _record_burst(buf, topic="/no_lat", count=10, start_ns=0, interval_ns=100_000_000)
    m = buf.compute_metrics(topic="/no_lat", window_seconds=60, now_ns=1_000_000_000)
    assert m.latency_available is False
    assert m.latency_ns_p50 is None
    assert m.latency_ns_p95 is None
    assert m.latency_ns_p99 is None


def test_ring_buffer_cap_enforced() -> None:
    buf = MetricsBuffer(max_samples_per_topic=5)
    _record_burst(buf, topic="/cap", count=20, start_ns=0, interval_ns=100_000_000)
    # Cap is 5 ; oldest 15 were evicted
    assert buf.sample_count("/cap") == 5
    m = buf.compute_metrics(topic="/cap", window_seconds=60, now_ns=2_000_000_000)
    assert m.samples_observed == 5


def test_default_cap_constant_pinned() -> None:
    """The tool description quotes this constant — pin it."""
    assert MAX_SAMPLES_PER_TOPIC == 1000


def test_multi_topic_isolation() -> None:
    buf = MetricsBuffer()
    _record_burst(buf, topic="/a", count=10, start_ns=0, interval_ns=100_000_000)
    _record_burst(buf, topic="/b", count=20, start_ns=0, interval_ns=100_000_000)
    assert buf.sample_count("/a") == 10
    assert buf.sample_count("/b") == 20
    m_a = buf.compute_metrics(topic="/a", window_seconds=60, now_ns=1_000_000_000)
    m_b = buf.compute_metrics(topic="/b", window_seconds=60, now_ns=2_000_000_000)
    assert m_a.samples_observed == 10
    assert m_b.samples_observed == 20


def test_domain_filtering() -> None:
    buf = MetricsBuffer()
    # Same topic on two domains
    for i in range(5):
        buf.record(
            topic="/d",
            receive_ns=i * 100_000_000,
            sequence_number=i,
            publish_ns=None,
            domain_id=0,
        )
        buf.record(
            topic="/d",
            receive_ns=i * 100_000_000,
            sequence_number=i,
            publish_ns=None,
            domain_id=42,
        )
    m0 = buf.compute_metrics(topic="/d", window_seconds=60, now_ns=1_000_000_000, domain_id=0)
    m42 = buf.compute_metrics(topic="/d", window_seconds=60, now_ns=1_000_000_000, domain_id=42)
    assert m0.samples_observed == 5
    assert m42.samples_observed == 5
    # The two metrics should be independent — confirm one was filtered.
    assert buf.sample_count("/d") == 10  # both domains stored under same ring


def test_snapshot_topics_lists_recorded_topics() -> None:
    buf = MetricsBuffer()
    buf.record(topic="/a", receive_ns=0, sequence_number=0, publish_ns=None, domain_id=0)
    buf.record(topic="/b", receive_ns=0, sequence_number=0, publish_ns=None, domain_id=0)
    assert set(buf.snapshot_topics()) == {"/a", "/b"}


def test_declared_hz_is_echoed_through() -> None:
    buf = MetricsBuffer()
    buf.record(topic="/d", receive_ns=0, sequence_number=0, publish_ns=None, domain_id=0)
    m = buf.compute_metrics(topic="/d", window_seconds=60, now_ns=1_000_000_000, declared_hz=10.0)
    assert m.frequency_hz_declared == 10.0


def test_thread_safety_smoke() -> None:
    """Two threads recording concurrently must not corrupt state."""
    buf = MetricsBuffer(max_samples_per_topic=500)

    def feed(topic: str) -> None:
        for i in range(100):
            buf.record(
                topic=topic,
                receive_ns=i * 100_000,
                sequence_number=i,
                publish_ns=None,
                domain_id=0,
            )

    threads = [threading.Thread(target=feed, args=(t,)) for t in ("/t1", "/t2")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert buf.sample_count("/t1") == 100
    assert buf.sample_count("/t2") == 100
