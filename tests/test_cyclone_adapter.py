"""Tests for the Cyclone DDS adapter — skipped without the SDK.

Marked `requires_cyclonedds` so they auto-skip on hosts where the
`cyclonedds` Python bindings are not importable. CI runs them on
ubuntu-latest where wheels are reliably available.
"""

from __future__ import annotations

import pytest

cyclonedds = pytest.importorskip("cyclonedds")
pytestmark = pytest.mark.requires_cyclonedds

from topicforge.adapters.base import AdapterError
from topicforge.adapters.dds_cyclone import CycloneDdsAdapter


def test_adapter_imports_and_is_available():
    adapter = CycloneDdsAdapter(domain_id=0)
    assert adapter.is_available() is True
    assert adapter.name == "cyclone"
    assert adapter.effective_mode == "live"


def test_adapter_rejects_out_of_range_domain():
    with pytest.raises(AdapterError, match="domain_id must be in"):
        CycloneDdsAdapter(domain_id=-1)
    with pytest.raises(AdapterError, match="domain_id must be in"):
        CycloneDdsAdapter(domain_id=300)


def test_ros2_surface_raises_dds_only_error():
    adapter = CycloneDdsAdapter(domain_id=0)
    with pytest.raises(AdapterError, match="does not serve ROS2"):
        adapter.list_topics()
    with pytest.raises(AdapterError, match="does not serve ROS2"):
        adapter.get_topic_info("/cmd_vel")
    with pytest.raises(AdapterError, match="does not serve ROS2"):
        adapter.sample_messages("/cmd_vel", 1)
    with pytest.raises(AdapterError, match="does not serve ROS2"):
        adapter.analyze_bag("/tmp/demo.mcap")


def test_dds_surface_raises_stub_error_in_v020():
    """v0.2.0 stub — DDS methods raise a clear roadmap message.

    Update this test when the real implementation lands in v0.2.x.
    """
    adapter = CycloneDdsAdapter(domain_id=0)
    with pytest.raises(AdapterError, match=r"v0\.2\.0 stub"):
        adapter.list_participants(domain_id=0)
    with pytest.raises(AdapterError, match=r"v0\.2\.0 stub"):
        adapter.detect_qos_mismatches(topic=None)
    with pytest.raises(AdapterError, match=r"v0\.2\.0 stub"):
        adapter.peek_dds_samples(topic="/foo", count=1)
