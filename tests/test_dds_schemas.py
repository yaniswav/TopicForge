"""Schema shape pinning for the DDS module models.

`extra="forbid"` and `frozen=True` are inherited from the shared
`_CONFIG`; this suite asserts that the contract holds for the three
new models and that mandatory fields stay mandatory.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from topicforge.adapters.ros2_mock import MockAdapter
from topicforge.models import (
    EndpointInfo,
    MismatchReport,
    ParticipantInfo,
    QosProfile,
    TopicSummary,
)
from topicforge.services import Inspector
from topicforge.services.dds_views import endpointless_note

# ----- QosProfile -----


def test_qos_profile_minimum_valid_payload():
    qos = QosProfile(
        reliability="RELIABLE",
        durability="VOLATILE",
        history="KEEP_LAST",
        history_depth=10,
    )
    assert qos.reliability == "RELIABLE"
    assert qos.history_depth == 10
    assert qos.deadline_ns is None


def test_qos_profile_rejects_unknown_field():
    with pytest.raises(ValidationError):
        QosProfile(
            reliability="RELIABLE",
            durability="VOLATILE",
            history="KEEP_LAST",
            history_depth=10,
            unexpected_field="oops",  # type: ignore[call-arg]
        )


def test_qos_profile_rejects_invalid_reliability():
    with pytest.raises(ValidationError):
        QosProfile(
            reliability="MAYBE",  # type: ignore[arg-type]
            durability="VOLATILE",
            history="KEEP_LAST",
            history_depth=10,
        )


def test_qos_profile_is_frozen():
    qos = QosProfile(
        reliability="RELIABLE",
        durability="VOLATILE",
        history="KEEP_LAST",
        history_depth=10,
    )
    with pytest.raises(ValidationError):
        qos.reliability = "BEST_EFFORT"  # type: ignore[misc]


def test_qos_profile_negative_history_depth_rejected():
    with pytest.raises(ValidationError):
        QosProfile(
            reliability="RELIABLE",
            durability="VOLATILE",
            history="KEEP_LAST",
            history_depth=-1,
        )


# ----- ParticipantInfo -----


def test_participant_info_valid_payload():
    p = ParticipantInfo(
        guid="abc-123",
        vendor="cyclone",
        hostname="robot-01",
        domain_id=0,
    )
    assert p.guid == "abc-123"
    assert p.vendor == "cyclone"


def test_participant_info_does_not_repeat_the_envelope() -> None:
    """`mode_effective` lives in `ParticipantListing`, not on each participant."""
    with pytest.raises(ValidationError):
        ParticipantInfo(  # type: ignore[call-arg]
            guid="abc",
            vendor="cyclone",
            domain_id=0,
            mode_effective="live",
        )


def test_participant_info_reserves_the_node_link() -> None:
    p = ParticipantInfo(guid="abc", vendor="cyclone", domain_id=0)
    assert p.node_names == [] and p.node_names_source == "none"


def test_participant_info_rejects_domain_out_of_range():
    with pytest.raises(ValidationError):
        ParticipantInfo(
            guid="abc",
            vendor="cyclone",
            domain_id=300,
        )


def test_participant_info_rejects_unknown_vendor():
    with pytest.raises(ValidationError):
        ParticipantInfo(
            guid="abc",
            vendor="vortex",  # type: ignore[arg-type]
            domain_id=0,
        )


# ----- MismatchReport -----


def test_mismatch_report_valid_payload():
    report = MismatchReport(
        topic="/foo",
        reader_guid="r-1",
        writer_guid="w-1",
        incompatible_policies=["Reliability"],
        severity="incompatible",
    )
    assert report.severity == "incompatible"
    assert report.incompatible_policies == ["Reliability"]


def test_mismatch_report_rejects_invalid_severity():
    with pytest.raises(ValidationError):
        MismatchReport(
            topic="/foo",
            reader_guid=None,
            writer_guid=None,
            incompatible_policies=["Reliability"],
            severity="critical",  # type: ignore[arg-type]
        )


def test_mismatch_report_allows_empty_policy_list():
    """Empty list is technically allowed by the schema ; semantic emptiness
    is the adapter's job to avoid emitting."""
    report = MismatchReport(
        topic="/foo",
        reader_guid=None,
        writer_guid=None,
        incompatible_policies=[],
        severity="risky",
    )
    assert report.incompatible_policies == []


def test_mismatch_report_is_frozen():
    report = MismatchReport(
        topic="/foo",
        reader_guid=None,
        writer_guid=None,
        incompatible_policies=["Reliability"],
        severity="incompatible",
    )
    with pytest.raises(ValidationError):
        report.severity = "risky"  # type: ignore[misc]


# ----- 0.8.0 visibility fields -----

_NOW_NS = 1_000_000_000_000
_LONG_AGO_NS = _NOW_NS - 60 * 1_000_000_000


def _participant(**kw: object) -> ParticipantInfo:
    base: dict[str, object] = {
        "guid": "01.02.03.04",
        "vendor": "cyclone",
        "domain_id": 0,
        "status": "active",
        "first_seen_ns": _LONG_AGO_NS,
        "endpoint_count": 0,
    }
    base.update(kw)
    return ParticipantInfo(**base)  # type: ignore[arg-type]


def test_the_visibility_fields_are_optional_and_empty_by_default():
    assert _participant(endpoint_count=None).endpoint_count is None
    endpoint = EndpointInfo(
        guid="g", role="writer", participant_guid="p", dds_topic="t", is_observer=False
    )
    assert endpoint.node_name is None
    summary = TopicSummary(
        dds_topic="t", type_names=[], writer_count=0, reader_count=0, partitions=[]
    )
    assert summary.writer_nodes == [] and summary.reader_nodes == []


def test_endpoint_count_cannot_be_negative():
    with pytest.raises(ValidationError):
        _participant(endpoint_count=-1)


def test_a_long_known_endpointless_participant_is_noted_with_both_causes():
    note = endpointless_note([_participant(), _participant(guid="b")], _NOW_NS)
    assert note is not None
    assert note.startswith("2 participant(s) announce no endpoint TopicForge can see")
    assert "endpoint-less participant" in note and "DDS Security" in note
    assert "secured" not in note.lower()  # two causes named, no verdict


@pytest.mark.parametrize(
    "override",
    [
        {"endpoint_count": None},
        {"endpoint_count": 5},
        {"is_observer": True},
        {"is_topicforge_cli": True},
        {"status": "left"},
        {"first_seen_ns": None},
        {"first_seen_ns": _NOW_NS - 10_000_000_000},  # exactly 10 s: not yet more than 10 s
    ],
)
def test_the_endpointless_note_skips_what_it_cannot_say(override: dict[str, object]):
    assert endpointless_note([_participant(**override)], _NOW_NS) is None


def test_the_participant_listing_carries_the_note():
    class _Adapter(MockAdapter):
        def list_participants(self, domain_id: int = 0) -> list[ParticipantInfo]:
            return [*super().list_participants(domain_id), _participant(guid="silent")]

    listing = Inspector(_Adapter()).list_participants()
    assert listing.note is not None and "1 participant(s) announce no endpoint" in listing.note
    assert Inspector(MockAdapter()).list_participants().note is None
