"""Tests for the real CycloneDdsAdapter.

Skipped without `cyclonedds`. Each test joins domain 0 and may see other
participants on the host, so assertions pin shape, not content.
"""

from __future__ import annotations

from typing import get_args

import pytest

cyclonedds = pytest.importorskip("cyclonedds")
pytestmark = pytest.mark.requires_cyclonedds

from topicforge.adapters.base import AdapterError
from topicforge.adapters.common import VendorTag
from topicforge.adapters.dds_cyclone import CycloneDdsAdapter


@pytest.fixture(autouse=True)
def _close_adapters(monkeypatch: pytest.MonkeyPatch):
    """Stop every adapter's tracker thread at the end of its test.

    Leaked trackers from earlier tests would keep reading builtin readers
    concurrently while a later test waits for discovery to warm up.
    """
    created: list[CycloneDdsAdapter] = []
    original = CycloneDdsAdapter.__init__

    def tracking_init(self: CycloneDdsAdapter, *args: object, **kwargs: object) -> None:
        original(self, *args, **kwargs)
        created.append(self)

    monkeypatch.setattr(CycloneDdsAdapter, "__init__", tracking_init)
    yield
    for adapter in created:
        adapter.close()


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
        assert p.vendor in get_args(VendorTag)
        assert p.mode_effective == "live"
        assert p.domain_id == 0


def test_detect_qos_mismatches_returns_scan_envelope() -> None:
    """Empty bus typically yields no reports ; we pin the shape, not the content."""
    adapter = CycloneDdsAdapter(domain_id=0)
    result = adapter.detect_qos_mismatches()
    assert isinstance(result.reports, list) and isinstance(result.not_matched, list)
    assert result.mode_effective == "live" and result.policies_checked


def test_detect_qos_mismatches_topic_filter_accepts_unknown_topic() -> None:
    """Filtering on an unobserved topic must return an empty scan: never raise."""
    adapter = CycloneDdsAdapter(domain_id=0)
    result = adapter.detect_qos_mismatches(topic="/never/seen/this/topic")
    assert result.reports == [] and result.not_matched == [] and result.topics_scanned == 0


def test_peek_user_topic_not_on_bus_raises_not_discovered() -> None:
    """A user topic nobody announces raises instead of returning a placeholder."""
    adapter = CycloneDdsAdapter(domain_id=0)
    with pytest.raises(AdapterError, match="not discovered on domain"):
        adapter.peek_dds_samples("/foo/user_topic", count=1)


def test_peek_dds_samples_negative_count_rejected() -> None:
    adapter = CycloneDdsAdapter(domain_id=0)
    with pytest.raises(AdapterError, match="count must be >= 0"):
        adapter.peek_dds_samples("DCPSParticipant", count=-1)


def test_peek_builtin_dcps_participant_returns_sample_result() -> None:
    """Builtin DCPS topics can be peeked."""
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


def test_list_endpoints_shape_and_observer_exclusion() -> None:
    adapter = CycloneDdsAdapter(domain_id=0)
    listing = adapter.list_endpoints()
    assert listing.mode_effective == "live"
    assert listing.domain_id == 0
    assert listing.observer_guid is not None
    assert listing.returned == len(listing.endpoints)
    assert all(not e.is_observer for e in listing.endpoints)
    assert all(e.participant_guid != listing.observer_guid for e in listing.endpoints)
    with_observer = adapter.list_endpoints(include_observer=True)
    assert with_observer.total_discovered == listing.total_discovered


def test_peek_builtin_endpoints_carry_structured_fields() -> None:
    adapter = CycloneDdsAdapter(domain_id=0)
    for topic in ("DCPSPublication", "DCPSSubscription", "DCPSParticipant"):
        for s in adapter.peek_dds_samples(topic, count=5).samples:
            assert {"role", "participant_guid", "announced_ns", "is_observer"} <= set(s.payload)
            assert "_raw_text" not in s.payload


def test_own_participant_is_marked_as_observer() -> None:
    adapter = CycloneDdsAdapter(domain_id=0)
    assert adapter.await_discovery_ready() in (True, False)
    observers = [p for p in adapter.list_participants() if p.is_observer]
    assert len(observers) == 1
    assert observers[0].name == "topicforge"
    assert all(p.vendor_source in ("guid_prefix", "none") for p in adapter.list_participants())


def test_await_discovery_ready_is_bounded_and_then_instant() -> None:
    import time

    adapter = CycloneDdsAdapter(domain_id=0)
    start = time.monotonic()
    assert adapter.await_discovery_ready() is True
    assert time.monotonic() - start <= 3.5
    start = time.monotonic()
    assert adapter.await_discovery_ready() is True
    assert time.monotonic() - start < 0.2


def test_topic_metrics_status_for_user_and_builtin_topics() -> None:
    adapter = CycloneDdsAdapter(domain_id=0)
    assert adapter.topic_metrics("scan", 60).status == "unsupported_user_topic"
    assert adapter.topic_metrics("DCPSParticipant", 60).status == "no_samples_yet"
