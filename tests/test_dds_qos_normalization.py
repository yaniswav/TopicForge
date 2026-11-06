"""Tests for the vendor QoS -> canonical QosProfile normalizers.

This was extracted from the Cyclone adapter so it can be tested WITHOUT the
`cyclonedds` binding installed. Before the extraction the entire QoS
normalization path (the feeder of `detect_qos_mismatches`, the flagship
DDS diagnostic) was unreachable by the suite, so a renamed policy key
would silently make every QoS profile resolve to `None` (-> no mismatch
ever reported) with the suite still green. The `*_returns_none` cases
below pin exactly that failure mode.

Synthetic duck-typed objects only: no DDS middleware required.
"""

from __future__ import annotations

from topicforge.adapters.common import (
    cyclone_qos_to_profile,
    detect_mismatches,
)
from topicforge.adapters.common.qos_analyzer import analyze_pair

# ---------------------------------------------------------------------------
# Cyclone: policies are objects whose class NAME is read (e.g. "Reliable").
# The synthetic classes below reproduce that shape.
# ---------------------------------------------------------------------------


class Reliable:
    pass


class BestEffort:
    pass


class Volatile:
    pass


class TransientLocal:
    pass


class KeepAll:
    pass


class KeepLast:
    def __init__(self, depth: int = 10) -> None:
        self.depth = depth


class _Duration:
    def __init__(self, ns: int) -> None:
        self._ns = ns

    def to_nanoseconds(self) -> int:
        return self._ns


class Deadline:
    def __init__(self, ns: int) -> None:
        self.duration = _Duration(ns)


class _CycloneSample:
    def __init__(self, qos: object) -> None:
        self.qos = qos


def test_cyclone_full_profile():
    sample = _CycloneSample([Reliable(), TransientLocal(), KeepLast(depth=5), Deadline(1_000_000)])
    profile = cyclone_qos_to_profile(sample)
    assert profile is not None
    assert profile.reliability == "RELIABLE"
    assert profile.durability == "TRANSIENT_LOCAL"
    assert profile.history == "KEEP_LAST"
    assert profile.history_depth == 5
    assert profile.deadline_ns == 1_000_000


def test_cyclone_no_deadline_still_builds_profile():
    profile = cyclone_qos_to_profile(_CycloneSample([BestEffort(), Volatile(), KeepAll()]))
    assert profile is not None
    assert profile.reliability == "BEST_EFFORT"
    assert profile.durability == "VOLATILE"
    assert profile.history == "KEEP_ALL"
    assert profile.deadline_ns is None


def test_cyclone_missing_reliability_returns_none():
    assert cyclone_qos_to_profile(_CycloneSample([Volatile(), KeepLast()])) is None


def test_cyclone_no_qos_attr_returns_none():
    assert cyclone_qos_to_profile(object()) is None


def test_cyclone_non_iterable_qos_returns_none():
    # `for policy in qos` raises TypeError -> defensively swallowed -> None.
    assert cyclone_qos_to_profile(_CycloneSample(qos=42)) is None


def test_cyclone_renamed_policy_class_returns_none():
    # Regression guard: a binding that renames "Reliable" -> "Reliability"
    # must make the profile resolve to None (no false mismatch), NOT
    # silently pass.
    class Reliability:  # wrong name: not the spec-canonical "Reliable"
        pass

    assert cyclone_qos_to_profile(_CycloneSample([Reliability(), Volatile(), KeepLast()])) is None


# ---------------------------------------------------------------------------
# End-to-end: normalized profiles feed the flagship analyzer correctly.
# ---------------------------------------------------------------------------


def test_normalized_profiles_feed_detect_mismatches():
    reader = cyclone_qos_to_profile(_CycloneSample([Reliable(), Volatile(), KeepLast()]))
    writer = cyclone_qos_to_profile(_CycloneSample([BestEffort(), Volatile(), KeepLast()]))
    assert reader is not None and writer is not None
    result = detect_mismatches(reader, writer)
    assert result is not None
    policies, severity = result
    assert "Reliability" in policies
    assert severity == "incompatible"


def test_identical_reliable_pair_is_compatible():
    profile = cyclone_qos_to_profile(_CycloneSample([Reliable(), Volatile(), KeepLast()]))
    assert profile is not None
    # An identical profile pair is always compatible.
    assert detect_mismatches(profile, profile) is None


# ------------------ deadline / defensive branch coverage --------------------
# The Deadline policy shape varies across binding
# versions; these pin the getattr-fallback branches.


def _deadline_policy(*, duration: object = None, deadline: object = None) -> object:
    # An instance whose `type(obj).__name__ == "Deadline"` (what the Cyclone
    # normalizer keys on), with configurable duration / deadline attributes.
    policy = type("Deadline", (), {})()
    if duration is not None:
        policy.duration = duration
    if deadline is not None:
        policy.deadline = deadline
    return policy


def test_cyclone_deadline_duration_as_int():
    sample = _CycloneSample([Reliable(), Volatile(), KeepLast(), _deadline_policy(duration=1234)])
    profile = cyclone_qos_to_profile(sample)
    assert profile is not None
    assert profile.deadline_ns == 1234


def test_cyclone_deadline_via_deadline_attr_with_to_nanoseconds():
    policy = _deadline_policy(deadline=_Duration(5678))  # `.duration` absent
    sample = _CycloneSample([Reliable(), Volatile(), KeepLast(), policy])
    profile = cyclone_qos_to_profile(sample)
    assert profile is not None
    assert profile.deadline_ns == 5678


# --- Regression: cyclonedds 11.0.1 scopes its policy class names -------------
#
# On a live bus `type(policy).__name__` is "Reliability.BestEffort", not
# "BestEffort". Matching only the bare name built no profile at all, so
# detect_qos_mismatches never reported anything. `type()` accepts a dotted
# name, which reproduces it.

_ScopedBestEffort = type("Reliability.BestEffort", (), {})
_ScopedReliable = type("Reliability.Reliable", (), {})
_ScopedVolatile = type("Durability.Volatile", (), {})


def _scoped_keep_last(depth: int) -> object:
    cls = type("History.KeepLast", (), {})
    obj = cls()
    obj.depth = depth  # type: ignore[attr-defined]
    return obj


def test_cyclone_scoped_policy_names_build_a_profile():
    sample = _CycloneSample([_ScopedBestEffort(), _ScopedVolatile(), _scoped_keep_last(1)])
    profile = cyclone_qos_to_profile(sample)
    assert profile is not None
    assert profile.reliability == "BEST_EFFORT"
    assert profile.durability == "VOLATILE"
    assert profile.history == "KEEP_LAST"
    assert profile.history_depth == 1


def test_cyclone_scoped_names_distinguish_reliable_from_best_effort():
    reader = cyclone_qos_to_profile(
        _CycloneSample([_ScopedReliable(), _ScopedVolatile(), _scoped_keep_last(1)])
    )
    writer = cyclone_qos_to_profile(
        _CycloneSample([_ScopedBestEffort(), _ScopedVolatile(), _scoped_keep_last(1)])
    )
    assert reader is not None and writer is not None
    assert (reader.reliability, writer.reliability) == ("RELIABLE", "BEST_EFFORT")


class _Unreadable:
    """A duration the binding exposes in a shape we cannot read."""


Liveliness_Automatic = type("Automatic", (), {"lease_duration": _Unreadable()})


def test_unreadable_deadline_is_unknown_not_infinite() -> None:
    bad = Deadline(0)
    bad.duration = _Unreadable()
    writer = cyclone_qos_to_profile(_CycloneSample([Reliable(), TransientLocal(), KeepLast(), bad]))
    reader = cyclone_qos_to_profile(
        _CycloneSample([Reliable(), TransientLocal(), KeepLast(), Deadline(100_000_000)])
    )
    assert writer is not None and reader is not None
    assert writer.deadline_ns is None and writer.unknown_policies == ["Deadline"]
    assert reader.unknown_policies is None
    analysis = analyze_pair(reader, writer)
    assert analysis.incompatible == []
    assert "Deadline" in analysis.unchecked


def test_infinite_deadline_stays_infinite_and_compared() -> None:
    inf = Deadline(9_223_372_036_854_775_807)
    writer = cyclone_qos_to_profile(_CycloneSample([Reliable(), TransientLocal(), KeepLast(), inf]))
    reader = cyclone_qos_to_profile(
        _CycloneSample([Reliable(), TransientLocal(), KeepLast(), Deadline(100_000_000)])
    )
    assert writer is not None and reader is not None
    assert writer.unknown_policies is None
    assert [m.policy for m in analyze_pair(reader, writer).incompatible] == ["Deadline"]


def test_unreadable_liveliness_lease_goes_to_unchecked() -> None:
    writer = cyclone_qos_to_profile(
        _CycloneSample([Reliable(), TransientLocal(), KeepLast(), Liveliness_Automatic()])
    )
    assert writer is not None
    assert writer.liveliness_kind == "AUTOMATIC"
    assert writer.unknown_policies == ["Liveliness"]
    assert "Liveliness" in analyze_pair(writer, writer).unchecked
