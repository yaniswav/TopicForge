"""Tool-level tests for `topic_metrics` (10th MCP tool, v0.4.0 Phase 2).

Exercises the Inspector + MockAdapter path against the deterministic
mock fixture (`/dds/heartbeat_10hz`, 100 samples @ 10 Hz, 50 ms
synthetic latency, sequence 0..99 with no gaps).
"""

from __future__ import annotations

import pytest

from topicforge.adapters.base import AdapterError
from topicforge.models import TopicMetrics
from topicforge.services import Inspector

# ---------------------------------------------------------------------------
# Shape + happy path
# ---------------------------------------------------------------------------


def test_topic_metrics_returns_topic_metrics_shape(inspector: Inspector) -> None:
    result = inspector.topic_metrics("/dds/heartbeat_10hz")
    assert isinstance(result, TopicMetrics)
    assert result.topic == "/dds/heartbeat_10hz"
    assert result.window_seconds == 60
    assert result.mode_effective == "mock"


def test_topic_metrics_heartbeat_fixture_frequency_in_range(inspector: Inspector) -> None:
    """Mock fixture is 100 samples spaced 100 ms apart → ~10 Hz."""
    result = inspector.topic_metrics("/dds/heartbeat_10hz", window_seconds=60)
    assert result.samples_observed == 100
    # window_seconds_actual = 10 s (from first to fixture_now); 100/10 = 10
    assert result.frequency_hz_observed is not None
    assert 9.5 <= result.frequency_hz_observed <= 10.5


def test_topic_metrics_heartbeat_fixture_latency(inspector: Inspector) -> None:
    """Mock fixture has deterministic 50 ms latency on every sample."""
    result = inspector.topic_metrics("/dds/heartbeat_10hz", window_seconds=60)
    assert result.latency_available is True
    assert result.latency_ns_p50 == 50_000_000
    assert result.latency_ns_p95 == 50_000_000
    assert result.latency_ns_p99 == 50_000_000


def test_topic_metrics_heartbeat_fixture_no_sequence_gaps(inspector: Inspector) -> None:
    result = inspector.topic_metrics("/dds/heartbeat_10hz")
    assert result.sequence_numbers_available is True
    assert result.sequence_gaps_count == 0


def test_topic_metrics_unknown_topic_returns_zero_samples(inspector: Inspector) -> None:
    """A topic with no recorded samples returns an empty TopicMetrics."""
    result = inspector.topic_metrics("/dds/never_seen")
    assert result.samples_observed == 0
    assert result.frequency_hz_observed is None
    assert result.latency_available is False
    assert result.sequence_numbers_available is False


def test_topic_metrics_singleton_returns_none_frequency(inspector: Inspector) -> None:
    """A topic with exactly 1 sample cannot define a frequency."""
    result = inspector.topic_metrics("/dds/singleton")
    assert result.samples_observed == 1
    assert result.frequency_hz_observed is None


def test_topic_metrics_domain_filter(inspector: Inspector) -> None:
    """`/dds/cross_domain` exists on domain 42 only ; domain 0 → empty."""
    on_d0 = inspector.topic_metrics("/dds/cross_domain", domain_id=0)
    on_d42 = inspector.topic_metrics("/dds/cross_domain", domain_id=42)
    assert on_d0.samples_observed == 0
    assert on_d42.samples_observed == 1


# ---------------------------------------------------------------------------
# Validation paths
# ---------------------------------------------------------------------------


def test_topic_metrics_rejects_invalid_window(inspector: Inspector) -> None:
    with pytest.raises(AdapterError, match="window_seconds"):
        inspector.topic_metrics("/dds/heartbeat_10hz", window_seconds=0)
    with pytest.raises(AdapterError, match="window_seconds"):
        inspector.topic_metrics("/dds/heartbeat_10hz", window_seconds=3601)


def test_topic_metrics_rejects_non_int_window(inspector: Inspector) -> None:
    with pytest.raises(AdapterError, match="window_seconds"):
        inspector.topic_metrics("/dds/heartbeat_10hz", window_seconds="60")  # type: ignore[arg-type]


def test_topic_metrics_rejects_invalid_domain(inspector: Inspector) -> None:
    with pytest.raises(AdapterError, match="domain_id"):
        inspector.topic_metrics("/dds/heartbeat_10hz", domain_id=-1)
    with pytest.raises(AdapterError, match="domain_id"):
        inspector.topic_metrics("/dds/heartbeat_10hz", domain_id=233)


def test_topic_metrics_rejects_malformed_topic_name(inspector: Inspector) -> None:
    with pytest.raises(AdapterError, match="DDS topic name"):
        inspector.topic_metrics("invalid topic name")
    with pytest.raises(AdapterError, match="non-empty"):
        inspector.topic_metrics("")
