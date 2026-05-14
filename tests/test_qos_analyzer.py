"""Tests for the pure QoS-mismatch analyzer.

Synthesized `QosProfile` pairs only — no DDS middleware installed,
no adapter wiring. Pins the four MVP policies.
"""

from __future__ import annotations

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
    """Reverse direction — BE reader takes what arrives, no mismatch."""
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
    """Reader demands less than writer provides — fine."""
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
