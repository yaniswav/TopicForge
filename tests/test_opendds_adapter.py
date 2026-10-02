"""Tests for `topicforge.adapters.dds_opendds.OpenDdsAdapter`.

`pyopendds` is not on PyPI, so the adapter is a stub. These tests pin
its shape and need no binding.
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


def test_is_available_always_false_for_stub() -> None:
    """A stub never reports available, even if a `pyopendds` module is importable.

    The factory selects on is_available() and every method here raises.
    """
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
        with pytest.raises(AdapterError, match="OpenDDS backend is not implemented"):
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
        with pytest.raises(AdapterError, match="OpenDDS backend is not implemented"):
            getattr(adapter, method)(*args)
