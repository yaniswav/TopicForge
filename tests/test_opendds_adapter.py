"""Tests for `topicforge.adapters.dds_opendds.OpenDdsAdapter` — v0.4.0 stub.

`pyopendds` is not yet maintained on PyPI. The stub adapter exists to
keep the auto-detect framework symmetric across all OSS vendors. These
tests assert the stub shape without requiring any binding installed.
"""

from __future__ import annotations

import pytest

from topicforge.adapters.base import AdapterError
from topicforge.adapters.dds_opendds import OpenDdsAdapter


def test_constructor_validates_domain_id() -> None:
    with pytest.raises(AdapterError, match="domain_id"):
        OpenDdsAdapter(domain_id=-1)
    with pytest.raises(AdapterError, match="domain_id"):
        OpenDdsAdapter(domain_id=233)


def test_constructor_succeeds_for_valid_domain() -> None:
    adapter = OpenDdsAdapter(domain_id=0)
    assert adapter.name == "opendds"
    assert adapter.effective_mode == "live"


def test_is_available_false_without_pyopendds() -> None:
    """Pyopendds is not on PyPI ; the probe must report not-available."""
    adapter = OpenDdsAdapter(domain_id=0)
    assert adapter.is_available() is False


def test_ros2_methods_raise_with_roadmap_pointer() -> None:
    adapter = OpenDdsAdapter(domain_id=0)
    for method, args in [
        ("list_topics", ()),
        ("get_topic_info", ("/x",)),
        ("sample_messages", ("/x", 1)),
        ("analyze_bag", ("/tmp/x.mcap",)),
    ]:
        with pytest.raises(AdapterError, match="OpenDDS adapter is a stub"):
            getattr(adapter, method)(*args)


def test_dds_methods_raise_with_roadmap_pointer() -> None:
    adapter = OpenDdsAdapter(domain_id=0)
    for method, args in [
        ("list_participants", (0,)),
        ("detect_qos_mismatches", (None,)),
        ("peek_dds_samples", ("/x", 1)),
        ("participant_events", (0, 60)),
        ("topic_metrics", ("/x", 60, 0)),
        ("peek_bag_samples", ("/tmp/x.mcap", "/x", 1)),
    ]:
        with pytest.raises(AdapterError, match="OpenDDS adapter is a stub"):
            getattr(adapter, method)(*args)
