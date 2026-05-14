"""Cross-vendor parametrized tests for the DDS adapters.

The same test runs once with CycloneDdsAdapter and once with FastDdsAdapter
via `@pytest.fixture(params=["cyclone", "fast"])`. Each backend auto-skips
when its binding is not installed — the fixture calls `importorskip`
inside the parameter branch, so the parametrize matrix surfaces an
explicit "skipped" entry per backend rather than failing at collection.

The point of cross-vendor testing is to confirm that:
  * Both adapters satisfy the same `MiddlewareAdapter` protocol shape
  * Both return Pydantic models with canonical vendor-neutral enum values
    (this would catch a Cyclone adapter accidentally emitting `"Reliable"`
    instead of `"RELIABLE"`, etc.)
  * Both raise `DDS_ONLY_ERROR_MSG` on the 4 ROS2 methods uniformly
  * Both raise the v0.3.x XTypes/IDL roadmap message uniformly
  * `detect_qos_mismatches` returns the same `MismatchReport` shape
    regardless of which discovery path produced the QoS pair

Note: when both bindings are installed and tests run in parallel, the
Cyclone and Fast participants will discover each other on domain 0 via
the OMG-RTPS protocol guarantee. That is the multi-vendor positioning
in action ; the tests deliberately do not pin participant counts.
"""

from __future__ import annotations

from typing import Any

import pytest

from topicforge.adapters.base import AdapterError, MiddlewareAdapter


@pytest.fixture(params=["cyclone", "fast"])
def dds_adapter(request: pytest.FixtureRequest) -> Any:
    backend = request.param
    if backend == "cyclone":
        pytest.importorskip("cyclonedds")
        from topicforge.adapters.dds_cyclone import CycloneDdsAdapter

        adapter = CycloneDdsAdapter(domain_id=0)
        yield adapter
    else:
        pytest.importorskip("fastdds")
        from topicforge.adapters.dds_fast import FastDdsAdapter

        adapter = FastDdsAdapter(domain_id=0)
        try:
            yield adapter
        finally:
            adapter.close()


def test_adapter_satisfies_middleware_protocol(dds_adapter: Any) -> None:
    """Both adapters implement the MiddlewareAdapter protocol structurally."""
    assert isinstance(dds_adapter, MiddlewareAdapter)


def test_adapter_name_is_canonical_tag(dds_adapter: Any) -> None:
    assert dds_adapter.name in ("cyclone", "fast")


def test_adapter_effective_mode_is_live(dds_adapter: Any) -> None:
    assert dds_adapter.effective_mode == "live"


def test_adapter_is_available(dds_adapter: Any) -> None:
    assert dds_adapter.is_available() is True


def test_list_participants_returns_pydantic_participantinfo(dds_adapter: Any) -> None:
    """Output shape: list of frozen Pydantic ParticipantInfo with canonical
    vendor enum. Vendor-agnostic — works regardless of which backend
    happened to do the discovery."""
    participants = dds_adapter.list_participants()
    assert isinstance(participants, list)
    for p in participants:
        # Canonical vendor Literal must hold on every adapter's output.
        assert p.vendor in ("cyclone", "fast", "rti", "mock", "unknown")
        assert p.mode_effective == "live"
        assert isinstance(p.guid, str)
        assert isinstance(p.domain_id, int)


def test_detect_qos_mismatches_returns_list_with_canonical_severity(
    dds_adapter: Any,
) -> None:
    """Empty bus typically yields [] ; either way the type and severity
    Literal hold."""
    result = dds_adapter.detect_qos_mismatches()
    assert isinstance(result, list)
    for r in result:
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


def test_peek_user_topic_uniform_roadmap_message(dds_adapter: Any) -> None:
    """Both adapters raise the v0.3.x roadmap message on arbitrary user topics."""
    with pytest.raises(AdapterError, match=r"v0\.3\.x roadmap"):
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
