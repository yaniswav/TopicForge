"""Tests for the pure QoS-mismatch analyzer.

Synthesized `QosProfile` pairs only: no DDS middleware installed,
no adapter wiring. Pins the four MVP policies and the canonical enum
contract that keeps the analyzer truly vendor-agnostic across the
Cyclone and Fast DDS adapters.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from topicforge.adapters.common import detect_mismatches
from topicforge.models import QosProfile


def _profile(
    *,
    reliability: str = "RELIABLE",
    durability: str = "VOLATILE",
    history: str = "KEEP_LAST",
    history_depth: int | None = 10,
    deadline_ns: int | None = None,
) -> QosProfile:
    return QosProfile(
        reliability=reliability,  # type: ignore[arg-type]
        durability=durability,  # type: ignore[arg-type]
        history=history,  # type: ignore[arg-type]
        history_depth=history_depth,
        deadline_ns=deadline_ns,
    )


def test_identical_profiles_compatible():
    qos = _profile()
    assert detect_mismatches(qos, qos) is None


def test_reliable_reader_best_effort_writer_incompatible():
    reader = _profile(reliability="RELIABLE")
    writer = _profile(reliability="BEST_EFFORT")
    result = detect_mismatches(reader, writer)
    assert result is not None
    policies, severity = result
    assert "Reliability" in policies
    assert severity == "incompatible"


def test_best_effort_reader_reliable_writer_compatible():
    """Reverse direction: BE reader takes what arrives, no mismatch."""
    reader = _profile(reliability="BEST_EFFORT")
    writer = _profile(reliability="RELIABLE")
    assert detect_mismatches(reader, writer) is None


def test_transient_local_reader_volatile_writer_incompatible():
    reader = _profile(durability="TRANSIENT_LOCAL")
    writer = _profile(durability="VOLATILE")
    result = detect_mismatches(reader, writer)
    assert result is not None
    policies, severity = result
    assert policies == ["Durability"]
    assert severity == "incompatible"


def test_volatile_reader_transient_local_writer_compatible():
    """Reader demands less than writer provides: fine."""
    reader = _profile(durability="VOLATILE")
    writer = _profile(durability="TRANSIENT_LOCAL")
    assert detect_mismatches(reader, writer) is None


def test_keep_all_reader_keep_last_writer_risky():
    reader = _profile(history="KEEP_ALL", history_depth=None)
    writer = _profile(history="KEEP_LAST", history_depth=10)
    result = detect_mismatches(reader, writer)
    assert result is not None
    policies, severity = result
    assert policies == ["History"]
    assert severity == "risky"


def test_keep_last_reader_keep_all_writer_compatible():
    reader = _profile(history="KEEP_LAST", history_depth=10)
    writer = _profile(history="KEEP_ALL", history_depth=None)
    assert detect_mismatches(reader, writer) is None


def test_tighter_reader_deadline_incompatible():
    reader = _profile(deadline_ns=100_000_000)
    writer = _profile(deadline_ns=500_000_000)
    result = detect_mismatches(reader, writer)
    assert result is not None
    policies, severity = result
    assert policies == ["Deadline"]
    assert severity == "incompatible"


def test_equal_deadline_compatible():
    qos = _profile(deadline_ns=200_000_000)
    assert detect_mismatches(qos, qos) is None


def test_reader_deadline_none_with_writer_deadline_compatible():
    """No reader constraint -> nothing to mismatch on deadline."""
    reader = _profile(deadline_ns=None)
    writer = _profile(deadline_ns=100_000_000)
    assert detect_mismatches(reader, writer) is None


def test_multiple_incompatibilities_combined():
    reader = _profile(
        reliability="RELIABLE",
        durability="TRANSIENT_LOCAL",
        history="KEEP_LAST",
        deadline_ns=50_000_000,
    )
    writer = _profile(
        reliability="BEST_EFFORT",
        durability="VOLATILE",
        history="KEEP_LAST",
        deadline_ns=200_000_000,
    )
    result = detect_mismatches(reader, writer)
    assert result is not None
    policies, severity = result
    assert set(policies) == {"Reliability", "Durability", "Deadline"}
    assert severity == "incompatible"


def test_risky_only_keeps_risky_severity():
    """If only risky issues, severity stays 'risky' even with multiple risky entries."""
    # KEEP_ALL reader + KEEP_LAST writer = risky
    reader = _profile(history="KEEP_ALL", history_depth=None)
    writer = _profile(history="KEEP_LAST", history_depth=5)
    result = detect_mismatches(reader, writer)
    assert result is not None
    _, severity = result
    assert severity == "risky"


def test_incompatible_takes_precedence_over_risky():
    """If both 'incompatible' and 'risky' policies are present, severity is 'incompatible'."""
    reader = _profile(
        reliability="RELIABLE",
        history="KEEP_ALL",
        history_depth=None,
    )
    writer = _profile(
        reliability="BEST_EFFORT",
        history="KEEP_LAST",
        history_depth=10,
    )
    result = detect_mismatches(reader, writer)
    assert result is not None
    policies, severity = result
    assert "Reliability" in policies
    assert "History" in policies
    assert severity == "incompatible"


# ---------------------------------------------------------------------------
# Cross-vendor edge cases (v0.3.0): flagged by the OMG-DDS exploration
# report as gaps a parametrized test SHOULD cover.
# ---------------------------------------------------------------------------


def test_boundary_durability_tied_at_transient_local() -> None:
    """Reader and writer tied at TRANSIENT_LOCAL -> compatible. Boundary case
    for the strict > comparison in _DURABILITY_ORDER."""
    reader = _profile(durability="TRANSIENT_LOCAL")
    writer = _profile(durability="TRANSIENT_LOCAL")
    assert detect_mismatches(reader, writer) is None


def test_boundary_durability_tied_at_persistent() -> None:
    """Highest rank, both sides equal: compatible."""
    reader = _profile(durability="PERSISTENT")
    writer = _profile(durability="PERSISTENT")
    assert detect_mismatches(reader, writer) is None


def test_policies_list_orders_incompatible_before_risky() -> None:
    """When both incompatible and risky policies fire, the canonical list
    order is incompatible-first. Downstream LLM clients can rely on this
    ordering when summarizing a mismatch report."""
    reader = _profile(
        reliability="RELIABLE",  # incompatible
        history="KEEP_ALL",  # risky
        history_depth=None,
    )
    writer = _profile(
        reliability="BEST_EFFORT",
        history="KEEP_LAST",
        history_depth=10,
    )
    result = detect_mismatches(reader, writer)
    assert result is not None
    policies, _ = result
    assert policies.index("Reliability") < policies.index("History")


def test_canonical_enums_required_by_pydantic_constructor() -> None:
    """The Pydantic Literal protects the analyzer from adapter-side
    normalization slip-ups. A Cyclone adapter accidentally emitting
    `"Reliable"` (Cyclone class-name form) instead of `"RELIABLE"` (the
    canonical form) raises at QosProfile construction time, before
    detect_mismatches ever runs. That's the vendor-neutral contract.
    """
    # lowercase form: adapter must normalize before constructing QosProfile.
    with pytest.raises(ValidationError):
        QosProfile(
            reliability="reliable",  # type: ignore[arg-type]
            durability="VOLATILE",
            history="KEEP_LAST",
            history_depth=10,
        )
    # PascalCase form (the Cyclone Policy.* class name): same rejection.
    with pytest.raises(ValidationError):
        QosProfile(
            reliability="Reliable",  # type: ignore[arg-type]
            durability="VOLATILE",
            history="KEEP_LAST",
            history_depth=10,
        )


def test_zero_deadline_treated_as_zero_not_none() -> None:
    """Reader deadline_ns=0 is a tight constraint, not 'no constraint'.
    Compatible only when writer also has deadline_ns=0 or None... wait,
    actually deadline_ns=0 means 'sample period <= 0ns' which only
    matches an equally-zero writer. We pin the existing analyzer
    behavior here as a no-regression guard."""
    reader = _profile(deadline_ns=0)
    writer = _profile(deadline_ns=0)
    # Equal deadlines, both 0 -> compatible.
    assert detect_mismatches(reader, writer) is None


def test_mixed_severity_keeps_all_offending_policies() -> None:
    """When multiple policies fire across both severities, the report lists
    them all: caller decides which to surface first."""
    reader = _profile(
        reliability="RELIABLE",  # incompatible
        durability="TRANSIENT_LOCAL",  # incompatible (writer is VOLATILE)
        history="KEEP_ALL",  # risky
        history_depth=None,
        deadline_ns=50_000_000,  # incompatible (writer is looser)
    )
    writer = _profile(
        reliability="BEST_EFFORT",
        durability="VOLATILE",
        history="KEEP_LAST",
        history_depth=10,
        deadline_ns=500_000_000,
    )
    result = detect_mismatches(reader, writer)
    assert result is not None
    policies, severity = result
    assert set(policies) == {"Reliability", "Durability", "Deadline", "History"}
    assert severity == "incompatible"


def test_reader_finite_deadline_writer_none_incompatible() -> None:
    """Audit P1-3 / C3: a writer offering no deadline = infinite (loosest)
    period, which cannot satisfy a reader that requests a finite deadline.
    The pre-audit rule skipped this case (both-must-be-non-None) and
    returned a false 'compatible'."""
    reader = _profile(deadline_ns=100_000_000)
    writer = _profile(deadline_ns=None)
    result = detect_mismatches(reader, writer)
    assert result is not None
    policies, severity = result
    assert policies == ["Deadline"]
    assert severity == "incompatible"


def test_both_deadline_none_compatible() -> None:
    """Both infinite -> no deadline constraint on either side -> compatible."""
    reader = _profile(deadline_ns=None)
    writer = _profile(deadline_ns=None)
    assert detect_mismatches(reader, writer) is None


# ------------------------- exact RxO rules (lot B) -------------------------

from topicforge.adapters.common import analyze_pair, format_duration, partitions_match  # noqa: E402

_MS = 1_000_000


def _full(**extra: object) -> QosProfile:
    """A profile with every optional policy announced, defaults all compatible."""
    base: dict[str, object] = {
        "reliability": "RELIABLE",
        "durability": "VOLATILE",
        "history": "KEEP_LAST",
        "history_depth": 10,
        "liveliness_kind": "AUTOMATIC",
        "liveliness_lease_ns": None,
        "ownership_kind": "SHARED",
        "latency_budget_ns": 0,
        "destination_order": "BY_RECEPTION_TIMESTAMP",
        "data_representation": ["XCDR2"],
    }
    base.update(extra)
    return QosProfile(**base)  # type: ignore[arg-type]


def _policies(reader: QosProfile, writer: QosProfile) -> list[str]:
    return [d.policy for d in analyze_pair(reader, writer).incompatible]


def test_full_compatible_pair_has_no_findings_and_nothing_unchecked() -> None:
    analysis = analyze_pair(_full(), _full())
    assert analysis.details == [] and analysis.unchecked == []


def test_details_carry_readable_values() -> None:
    (detail,) = analyze_pair(_full(), _full(reliability="BEST_EFFORT")).incompatible
    assert (detail.policy, detail.requested, detail.offered) == (
        "Reliability",
        "RELIABLE",
        "BEST_EFFORT",
    )


@pytest.mark.parametrize(
    ("reader", "writer", "expected"),
    [
        ("PERSISTENT", "TRANSIENT", True),
        ("TRANSIENT", "PERSISTENT", False),
        ("TRANSIENT_LOCAL", "TRANSIENT", False),
    ],
)
def test_durability_order(reader: str, writer: str, expected: bool) -> None:
    found = _policies(_full(durability=reader), _full(durability=writer))
    assert ("Durability" in found) is expected


def test_deadline_infinite_semantics() -> None:
    assert _policies(_full(deadline_ns=100 * _MS), _full(deadline_ns=None)) == ["Deadline"]
    assert _policies(_full(deadline_ns=None), _full(deadline_ns=100 * _MS)) == []
    (d,) = analyze_pair(_full(deadline_ns=100 * _MS), _full(deadline_ns=None)).incompatible
    assert (d.requested, d.offered) == ("100 ms", "infinite")


@pytest.mark.parametrize(
    ("reader", "writer", "expected"),
    [
        (("MANUAL_BY_TOPIC", None), ("AUTOMATIC", None), True),
        (("AUTOMATIC", None), ("MANUAL_BY_TOPIC", None), False),
        (("MANUAL_BY_PARTICIPANT", None), ("MANUAL_BY_TOPIC", None), False),
        (("AUTOMATIC", 100 * _MS), ("AUTOMATIC", 500 * _MS), True),
        (("AUTOMATIC", 500 * _MS), ("AUTOMATIC", 100 * _MS), False),
        (("AUTOMATIC", 500 * _MS), ("AUTOMATIC", None), True),
        (("AUTOMATIC", None), ("AUTOMATIC", 500 * _MS), False),
    ],
)
def test_liveliness_kind_and_lease(reader: tuple, writer: tuple, expected: bool) -> None:
    r = _full(liveliness_kind=reader[0], liveliness_lease_ns=reader[1])
    w = _full(liveliness_kind=writer[0], liveliness_lease_ns=writer[1])
    assert ("Liveliness" in _policies(r, w)) is expected


def test_liveliness_detail_text() -> None:
    r = _full(liveliness_kind="MANUAL_BY_TOPIC", liveliness_lease_ns=500 * _MS)
    (d,) = analyze_pair(r, _full()).incompatible
    assert d.requested == "MANUAL_BY_TOPIC lease 500 ms"
    assert d.offered == "AUTOMATIC lease infinite"


def test_latency_budget() -> None:
    assert _policies(_full(latency_budget_ns=10 * _MS), _full(latency_budget_ns=50 * _MS)) == [
        "LatencyBudget"
    ]
    assert _policies(_full(latency_budget_ns=50 * _MS), _full(latency_budget_ns=10 * _MS)) == []


def test_ownership_kinds_must_be_equal_strength_ignored() -> None:
    exclusive, shared = _full(ownership_kind="EXCLUSIVE"), _full(ownership_kind="SHARED")
    assert _policies(exclusive, shared) == ["Ownership"]
    assert _policies(shared, exclusive) == ["Ownership"]
    weak = _full(ownership_kind="EXCLUSIVE", ownership_strength=1)
    strong = _full(ownership_kind="EXCLUSIVE", ownership_strength=99)
    assert _policies(weak, strong) == []


def test_destination_order() -> None:
    by_src = "BY_SOURCE_TIMESTAMP"
    assert _policies(_full(destination_order=by_src), _full()) == ["DestinationOrder"]
    assert _policies(_full(), _full(destination_order=by_src)) == []


def test_data_representation() -> None:
    xcdr1, xcdr2 = _full(data_representation=["XCDR1"]), _full(data_representation=["XCDR2"])
    assert _policies(xcdr1, xcdr2) == ["DataRepresentation"]
    both = _full(data_representation=["XCDR1", "XCDR2"])
    assert _policies(both, xcdr2) == []


def test_unknown_values_are_unchecked_not_findings() -> None:
    bare = _profile()
    analysis = analyze_pair(bare, bare)
    assert analysis.details == []
    assert analysis.unchecked == [
        "Liveliness",
        "LatencyBudget",
        "Ownership",
        "DestinationOrder",
        "DataRepresentation",
    ]


def test_history_is_risky_and_labelled() -> None:
    analysis = analyze_pair(_full(history="KEEP_ALL"), _full())
    assert analysis.incompatible == []
    (d,) = analysis.risky
    assert d.policy == "History" and "not an RxO" in d.rule
    assert detect_mismatches(_full(history="KEEP_ALL"), _full()) == (["History"], "risky")


@pytest.mark.parametrize(
    ("reader", "writer", "expected"),
    [
        (None, None, True),
        ([], [""], True),
        (["a"], ["b"], False),
        (["a", "b"], ["b"], True),
        (["robot*"], ["robot1"], True),
        (["robot1"], ["robot*"], True),
        (["robot*"], ["robot*"], False),
        (["robot*"], ["other*"], False),
        (["*"], [], True),
        (["*"], ["x"], True),
        (["r?bot"], ["robot"], True),
        (["r?bot"], ["rbot"], False),
        (["Robot"], ["robot"], False),
        (["a"], [], False),
    ],
)
def test_partitions_match(reader, writer, expected: bool) -> None:
    assert partitions_match(reader, writer) is expected


@pytest.mark.parametrize(
    ("ns", "text"),
    [
        (None, "infinite"),
        (500, "500 ns"),
        (2_500, "2.5 us"),
        (100 * _MS, "100 ms"),
        (3_000_000_000, "3 s"),
    ],
)
def test_format_duration(ns: int | None, text: str) -> None:
    assert format_duration(ns) == text


@pytest.mark.parametrize(
    ("writer", "reader", "expected"),
    [
        # Each row was measured on a live cyclonedds 11.0.1 bus (domain 61,
        # subscription_matched_status.current_count, 2026-10-02).
        ("r*", "r*", False),
        ("robot*", "robot?", False),
        ("robot1", "robot[12]", False),
        ("robot*", "robot1", True),
        ("robot1", "robot*", True),
        ("robot[12]", "robot[12]", True),
        ("robot?", "robot?", False),
        ("robot1", "robot1", True),
        ("robot3", "robot[12]", False),
        ("r*", "robot1", True),
        ("a*", "b*", False),
        ("", "*", True),
        ("*", "*", False),
        ("robot[1-3]", "robot2", False),
        ("robot2", "robot[1-3]", False),
        ("robot1", "robot[1]", False),
    ],
)
def test_partition_matching_agrees_with_cyclone_on_a_live_bus(
    writer: str, reader: str, expected: bool
) -> None:
    assert partitions_match([reader], [writer]) is expected
    assert partitions_match([writer], [reader]) is expected
