"""Tests for the FastDdsAdapter (v0.3.0).

Skipped without `fastdds`. Listener-driven discovery may need a few
hundred ms to populate against a local bus — the constructor uses
`discovery_wait_ms=1500` by default, which gives the listener time to
collect at least the local participant before the fixture yields.

The `_DiscoveryListener` thread-safety test exercises the RLock without
requiring a real Fast DDS participant — it works on the listener class
directly with synthetic discovery infos.
"""

from __future__ import annotations

import pytest

fastdds = pytest.importorskip("fastdds")
pytestmark = pytest.mark.requires_fastdds

from topicforge.adapters.base import AdapterError
from topicforge.adapters.dds_fast import FastDdsAdapter


@pytest.fixture
def adapter():
    a = FastDdsAdapter(domain_id=0)
    yield a
    a.close()


def test_adapter_imports_and_is_available(adapter: FastDdsAdapter) -> None:
    assert adapter.is_available() is True
    assert adapter.name == "fast"
    assert adapter.effective_mode == "live"


def test_adapter_rejects_out_of_range_domain() -> None:
    with pytest.raises(AdapterError, match="domain_id must be in"):
        FastDdsAdapter(domain_id=-1)
    with pytest.raises(AdapterError, match="domain_id must be in"):
        FastDdsAdapter(domain_id=300)


def test_ros2_surface_raises_dds_only_error(adapter: FastDdsAdapter) -> None:
    with pytest.raises(AdapterError, match="DDS observability only"):
        adapter.list_topics()
    with pytest.raises(AdapterError, match="DDS observability only"):
        adapter.get_topic_info("/cmd_vel")
    with pytest.raises(AdapterError, match="DDS observability only"):
        adapter.sample_messages("/cmd_vel", 1)
    with pytest.raises(AdapterError, match="DDS observability only"):
        adapter.analyze_bag("/tmp/demo.mcap")


def test_list_participants_returns_list(adapter: FastDdsAdapter) -> None:
    """The local Fast DDS participant typically announces itself within
    discovery_wait_ms ; we pin shape over content for CI portability."""
    participants = adapter.list_participants()
    assert isinstance(participants, list)
    for p in participants:
        assert p.vendor in ("cyclone", "fast", "rti", "mock", "unknown")
        assert p.mode_effective == "live"
        assert p.domain_id == 0


def test_detect_qos_mismatches_returns_list(adapter: FastDdsAdapter) -> None:
    result = adapter.detect_qos_mismatches()
    assert isinstance(result, list)


def test_detect_qos_mismatches_topic_filter_accepts_unknown_topic(
    adapter: FastDdsAdapter,
) -> None:
    """Filtering on an unobserved topic must return [] — never raise."""
    result = adapter.detect_qos_mismatches(topic="/never/seen/this/topic")
    assert result == []


def test_peek_arbitrary_user_topic_raises_xtypes_roadmap(
    adapter: FastDdsAdapter,
) -> None:
    """v0.3.0 limitation: arbitrary user topics raise with a roadmap pointer."""
    with pytest.raises(AdapterError, match=r"v0\.3\.x roadmap"):
        adapter.peek_dds_samples("/foo/user_topic", count=1)


def test_peek_dds_samples_negative_count_rejected(adapter: FastDdsAdapter) -> None:
    with pytest.raises(AdapterError, match="count must be >= 0"):
        adapter.peek_dds_samples("DCPSParticipant", count=-1)


def test_peek_builtin_dcps_participant_returns_sample_result(
    adapter: FastDdsAdapter,
) -> None:
    result = adapter.peek_dds_samples("DCPSParticipant", count=5)
    assert result.topic == "DCPSParticipant"
    assert result.mode_effective == "live"
    assert isinstance(result.count, int)
    assert result.count >= 0
    assert result.count == len(result.samples)


def test_close_is_idempotent(adapter: FastDdsAdapter) -> None:
    """close() called twice doesn't raise."""
    adapter.close()
    adapter.close()
    assert adapter.is_available() is False


def test_listener_thread_safe_under_concurrent_callbacks() -> None:
    """Stress: 4 threads fire 50 callbacks each at the listener — final
    snapshot must be a coherent list, no exceptions raised. Tests the
    listener class directly without going through the Fast DDS
    participant, so this test does not require the fastdds binding —
    but it's gated by requires_fastdds at the module level for
    organizational clarity (the listener IS part of the Fast adapter)."""
    import threading

    from topicforge.adapters.dds_fast.adapter import _DiscoveryListener

    listener = _DiscoveryListener()

    class _FakeData:
        def __init__(self, guid: bytes) -> None:
            self.guid = guid

    class _FakeInfo:
        def __init__(self, guid: bytes, status: str = "DISCOVERED") -> None:
            self.status = status
            self.info = _FakeData(guid)

    def hammer(thread_id: int) -> None:
        for i in range(50):
            info = _FakeInfo(guid=bytes([(thread_id * 50 + i) % 256]) * 16)
            listener.on_participant_discovery(None, info)

    threads = [threading.Thread(target=hammer, args=(t,)) for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    snapshot = listener.snapshot_participants()
    assert isinstance(snapshot, list)
    # Each thread inserts 50 entries with mostly unique GUIDs ; collisions
    # at the 256-byte ceiling reduce the count but never crash.
    assert len(snapshot) > 0
