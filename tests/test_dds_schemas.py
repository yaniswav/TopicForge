"""Schema shape pinning for the v0.2.0 DDS module models.

`extra="forbid"` and `frozen=True` are inherited from the shared
`_CONFIG`; this suite asserts that the contract holds for the three
new models and that mandatory fields stay mandatory.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from topicforge.models import MismatchReport, ParticipantInfo, QosProfile

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
        mode_effective="live",
    )
    assert p.guid == "abc-123"
    assert p.vendor == "cyclone"


def test_participant_info_requires_mode_effective():
    with pytest.raises(ValidationError):
        ParticipantInfo(  # type: ignore[call-arg]
            guid="abc",
            vendor="cyclone",
            domain_id=0,
        )


def test_participant_info_rejects_domain_out_of_range():
    with pytest.raises(ValidationError):
        ParticipantInfo(
            guid="abc",
            vendor="cyclone",
            domain_id=300,
            mode_effective="live",
        )


def test_participant_info_rejects_unknown_vendor():
    with pytest.raises(ValidationError):
        ParticipantInfo(
            guid="abc",
            vendor="opensplice",  # type: ignore[arg-type]
            domain_id=0,
            mode_effective="live",
        )


# ----- MismatchReport -----


def test_mismatch_report_valid_payload():
    report = MismatchReport(
        topic="/foo",
        reader_guid="r-1",
        writer_guid="w-1",
        incompatible_policies=["Reliability"],
        severity="incompatible",
        mode_effective="mock",
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
            mode_effective="mock",
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
        mode_effective="mock",
    )
    assert report.incompatible_policies == []


def test_mismatch_report_is_frozen():
    report = MismatchReport(
        topic="/foo",
        reader_guid=None,
        writer_guid=None,
        incompatible_policies=["Reliability"],
        severity="incompatible",
        mode_effective="mock",
    )
    with pytest.raises(ValidationError):
        report.severity = "risky"  # type: ignore[misc]
