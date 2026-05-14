"""Tests for the real CycloneDdsAdapter (v0.3.0).

Skipped without `cyclonedds`. Tests run a local participant against
domain 0 to exercise the real CycloneDDS bindings — they may discover
other participants on the host's loopback domain depending on
environment ; the assertions pin shape, not content, to stay robust
against networking edge cases on CI runners.
"""

from __future__ import annotations

import pytest

cyclonedds = pytest.importorskip("cyclonedds")
pytestmark = pytest.mark.requires_cyclonedds

from topicforge.adapters.base import AdapterError
from topicforge.adapters.dds_cyclone import CycloneDdsAdapter


def test_adapter_imports_and_is_available() -> None:
    adapter = CycloneDdsAdapter(domain_id=0)
    assert adapter.is_available() is True
    assert adapter.name == "cyclone"
    assert adapter.effective_mode == "live"


def test_adapter_rejects_out_of_range_domain() -> None:
    with pytest.raises(AdapterError, match="domain_id must be in"):
        CycloneDdsAdapter(domain_id=-1)
    with pytest.raises(AdapterError, match="domain_id must be in"):
        CycloneDdsAdapter(domain_id=300)


def test_ros2_surface_raises_dds_only_error() -> None:
    adapter = CycloneDdsAdapter(domain_id=0)
    with pytest.raises(AdapterError, match="DDS observability only"):
        adapter.list_topics()
    with pytest.raises(AdapterError, match="DDS observability only"):
        adapter.get_topic_info("/cmd_vel")
    with pytest.raises(AdapterError, match="DDS observability only"):
        adapter.sample_messages("/cmd_vel", 1)
    with pytest.raises(AdapterError, match="DDS observability only"):
        adapter.analyze_bag("/tmp/demo.mcap")


def test_list_participants_returns_list() -> None:
    """list_participants returns a list ; content depends on local discovery.

    The local participant typically announces itself within the discovery
    timeout, so the list is usually non-empty, but we don't assert content
    to stay robust against networking edge cases on CI runners.
    """
    adapter = CycloneDdsAdapter(domain_id=0)
    participants = adapter.list_participants()
    assert isinstance(participants, list)
    for p in participants:
        assert p.vendor in ("cyclone", "fast", "rti", "mock", "unknown")
        assert p.mode_effective == "live"
        assert p.domain_id == 0


def test_detect_qos_mismatches_returns_list() -> None:
    """Empty bus typically yields [] ; we pin the shape, not the content."""
    adapter = CycloneDdsAdapter(domain_id=0)
    result = adapter.detect_qos_mismatches()
    assert isinstance(result, list)


def test_detect_qos_mismatches_topic_filter_accepts_unknown_topic() -> None:
    """Filtering on an unobserved topic must return [] — never raise."""
    adapter = CycloneDdsAdapter(domain_id=0)
    result = adapter.detect_qos_mismatches(topic="/never/seen/this/topic")
    assert result == []


def test_peek_arbitrary_user_topic_raises_xtypes_roadmap() -> None:
    """v0.3.0 limitation: arbitrary user topics raise with a roadmap pointer."""
    adapter = CycloneDdsAdapter(domain_id=0)
    with pytest.raises(AdapterError, match=r"v0\.3\.x roadmap"):
        adapter.peek_dds_samples("/foo/user_topic", count=1)


def test_peek_dds_samples_negative_count_rejected() -> None:
    adapter = CycloneDdsAdapter(domain_id=0)
    with pytest.raises(AdapterError, match="count must be >= 0"):
        adapter.peek_dds_samples("DCPSParticipant", count=-1)


def test_peek_builtin_dcps_participant_returns_sample_result() -> None:
    """Builtin DCPS topics work today — the v0.3.0 MVP scope."""
    adapter = CycloneDdsAdapter(domain_id=0)
    result = adapter.peek_dds_samples("DCPSParticipant", count=5)
    assert result.topic == "DCPSParticipant"
    assert result.mode_effective == "live"
    assert isinstance(result.count, int)
    assert result.count >= 0
    assert result.count == len(result.samples)
    for s in result.samples:
        assert s.topic == "DCPSParticipant"
        assert s.message_type == "dds_builtin/DCPSParticipant"
        assert "vendor" in s.payload
        assert "guid" in s.payload


def test_peek_builtin_dcps_subscription_returns_sample_result() -> None:
    adapter = CycloneDdsAdapter(domain_id=0)
    result = adapter.peek_dds_samples("DCPSSubscription", count=3)
    assert result.topic == "DCPSSubscription"
    assert result.mode_effective == "live"


def test_peek_builtin_dcps_publication_returns_sample_result() -> None:
    adapter = CycloneDdsAdapter(domain_id=0)
    result = adapter.peek_dds_samples("DCPSPublication", count=3)
    assert result.topic == "DCPSPublication"
    assert result.mode_effective == "live"
