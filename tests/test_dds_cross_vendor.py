"""Protocol-shape tests for the DDS adapter.

Runs against Cyclone only (the fastdds-based adapter was removed in 0.9.0:
Cyclone observes Fast DDS and every conformant vendor through standard RTPS discovery).
The fixture stays parametrized so a future backend slots in. It skips without
`cyclonedds`. The tests confirm that the adapter:
  * satisfies the `MiddlewareAdapter` protocol shape
  * returns Pydantic models with canonical vendor-neutral enum values
  * raises `DDS_ONLY_ERROR_MSG` on the ROS 2 methods
  * returns the expected `MismatchScan` shape from `detect_qos_mismatches`
"""

from __future__ import annotations

from typing import Any, get_args

import pytest

from topicforge.adapters.base import AdapterError, MiddlewareAdapter
from topicforge.adapters.common import VendorTag


@pytest.fixture(params=["cyclone"])
def dds_adapter(request: pytest.FixtureRequest) -> Any:
    pytest.importorskip("cyclonedds")
    from topicforge.adapters.dds_cyclone import CycloneDdsAdapter

    return CycloneDdsAdapter(domain_id=0)


def test_adapter_satisfies_middleware_protocol(dds_adapter: Any) -> None:
    """Both adapters implement the MiddlewareAdapter protocol structurally."""
    assert isinstance(dds_adapter, MiddlewareAdapter)


def test_adapter_name_is_canonical_tag(dds_adapter: Any) -> None:
    assert dds_adapter.name == "cyclone"


def test_adapter_effective_mode_is_live(dds_adapter: Any) -> None:
    assert dds_adapter.effective_mode == "live"


def test_adapter_is_available(dds_adapter: Any) -> None:
    assert dds_adapter.is_available() is True


def test_list_participants_returns_pydantic_participantinfo(dds_adapter: Any) -> None:
    """Output shape: list of frozen Pydantic ParticipantInfo with canonical
    vendor enum. Vendor-agnostic: works regardless of which backend
    happened to do the discovery."""
    participants = dds_adapter.list_participants()
    assert isinstance(participants, list)
    for p in participants:
        # Canonical vendor Literal must hold on every adapter's output.
        assert p.vendor in get_args(VendorTag)
        assert p.mode_effective == "live"
        assert isinstance(p.guid, str)
        assert isinstance(p.domain_id, int)


def test_detect_qos_mismatches_returns_list_with_canonical_severity(
    dds_adapter: Any,
) -> None:
    """Empty bus typically yields no reports ; either way the envelope and
    severity Literal hold."""
    result = dds_adapter.detect_qos_mismatches()
    assert isinstance(result.reports, list)
    for r in result.reports:
        assert isinstance(r.incompatible_policies, list)
        assert r.severity in ("incompatible", "risky")
        assert r.mode_effective == "live"


def test_ros2_surface_raises_dds_only_error_uniformly(dds_adapter: Any) -> None:
    """Both adapters raise the same DDS_ONLY_ERROR_MSG on every ROS2 method."""
    methods_and_args: list[tuple[str, tuple[Any, ...]]] = [
        ("list_topics", ()),
        ("get_topic_info", ("/cmd_vel",)),
        ("sample_messages", ("/cmd_vel", 1)),
        ("analyze_bag", ("/tmp/demo.mcap",)),
    ]
    for method, args in methods_and_args:
        with pytest.raises(AdapterError, match="DDS observability only"):
            getattr(dds_adapter, method)(*args)


def test_peek_user_topic_not_on_bus_raises(dds_adapter: Any) -> None:
    """A user topic nobody announces raises a clear "not discovered" error."""
    with pytest.raises(AdapterError, match="not discovered"):
        dds_adapter.peek_dds_samples("/foo/user_topic", count=1)


def test_peek_negative_count_uniformly_rejected(dds_adapter: Any) -> None:
    with pytest.raises(AdapterError, match="count must be >= 0"):
        dds_adapter.peek_dds_samples("DCPSParticipant", count=-1)


def test_peek_builtin_dcps_participant_returns_canonical_sample_result(
    dds_adapter: Any,
) -> None:
    """Both adapters produce the same SampleResult shape for builtin DCPS
    snapshots, including the metadata payload keys."""
    result = dds_adapter.peek_dds_samples("DCPSParticipant", count=3)
    assert result.topic == "DCPSParticipant"
    assert result.mode_effective == "live"
    assert result.count == len(result.samples)
    for s in result.samples:
        assert s.topic == "DCPSParticipant"
        assert s.message_type == "dds_builtin/DCPSParticipant"
        # Payload metadata keys are vendor-neutral.
        assert "vendor" in s.payload
        assert "guid" in s.payload
