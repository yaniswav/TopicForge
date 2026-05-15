"""Tests for `topicforge.adapters.dds_dust.DustDdsAdapter` — v0.4.0 stub.

Dust DDS is Rust-native, no Python binding maintained. `is_available()`
is always False ; every protocol method raises AdapterError. The stub
exists so the auto-detect chain has a fourth OSS slot.
"""

from __future__ import annotations

import pytest

from topicforge.adapters.base import AdapterError
from topicforge.adapters.dds_dust import DustDdsAdapter


def test_constructor_validates_domain_id() -> None:
    with pytest.raises(AdapterError, match="domain_id"):
        DustDdsAdapter(domain_id=-1)
    with pytest.raises(AdapterError, match="domain_id"):
        DustDdsAdapter(domain_id=233)


def test_constructor_succeeds_for_valid_domain() -> None:
    adapter = DustDdsAdapter(domain_id=0)
    assert adapter.name == "dust"
    assert adapter.effective_mode == "live"


def test_is_available_is_always_false() -> None:
    """No Python binding maintained — the stub never becomes available."""
    assert DustDdsAdapter(domain_id=0).is_available() is False


def test_all_protocol_methods_raise_roadmap_pointer() -> None:
    adapter = DustDdsAdapter(domain_id=0)
    for method, args in [
        ("list_topics", ()),
        ("get_topic_info", ("/x",)),
        ("sample_messages", ("/x", 1)),
        ("analyze_bag", ("/tmp/x.mcap",)),
        ("list_participants", (0,)),
        ("detect_qos_mismatches", (None,)),
        ("peek_dds_samples", ("/x", 1)),
        ("participant_events", (0, 60)),
        ("topic_metrics", ("/x", 60, 0)),
        ("peek_bag_samples", ("/tmp/x.mcap", "/x", 1)),
    ]:
        with pytest.raises(AdapterError, match="Dust DDS adapter is a stub"):
            getattr(adapter, method)(*args)
