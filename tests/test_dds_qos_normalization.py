"""Tests for the vendor QoS -> canonical QosProfile normalizers.

These were extracted from the Cyclone and Fast adapters (Lot 0, audit
2026-07-08) precisely so they can be tested WITHOUT the `cyclonedds` /
`fastdds` bindings installed. Before the extraction the entire QoS
normalization path (the feeder of `detect_qos_mismatches`, the flagship
DDS diagnostic) was unreachable by the suite, so a renamed policy key
would silently make every QoS profile resolve to `None` (-> no mismatch
ever reported) with the suite still green. The `*_returns_none` cases
below pin exactly that failure mode.

Synthetic duck-typed objects only: no DDS middleware required.
"""

from __future__ import annotations

import pytest

from topicforge.adapters.common import (
    cyclone_qos_to_profile,
    detect_mismatches,
    fast_qos_to_profile,
)

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
    # silently pass. This is the exact failure mode the audit flagged as
    # previously untestable.
    class Reliability:  # wrong name: not the spec-canonical "Reliable"
        pass

    assert cyclone_qos_to_profile(_CycloneSample([Reliability(), Volatile(), KeepLast()])) is None


# ---------------------------------------------------------------------------
# Fast: QoS is a struct with .reliability/.durability/.history/.deadline,
# each exposing an integer `.kind` mapped via binding-derived int->str maps.
# ---------------------------------------------------------------------------

_REL = {1: "RELIABLE", 0: "BEST_EFFORT"}
_DUR = {0: "VOLATILE", 1: "TRANSIENT_LOCAL", 2: "TRANSIENT", 3: "PERSISTENT"}
_HIST = {0: "KEEP_LAST", 1: "KEEP_ALL"}


class _Kind:
    def __init__(self, kind: int, depth: int | None = None) -> None:
        self.kind = kind
        if depth is not None:
            self.depth = depth


class _Period:
    def __init__(self, seconds: int = 0, nanosec: int = 0) -> None:
        self.seconds = seconds
        self.nanosec = nanosec


class _FastDeadline:
    def __init__(self, seconds: int = 0, nanosec: int = 0) -> None:
        self.period = _Period(seconds, nanosec)


class _FastQos:
    def __init__(
        self,
        *,
        rel: int,
        dur: int,
        hist: int,
        depth: int = 1,
        deadline: _FastDeadline | None = None,
    ) -> None:
        self.reliability = _Kind(rel)
        self.durability = _Kind(dur)
        self.history = _Kind(hist, depth)
        self.deadline = deadline


class _FastSample:
    def __init__(self, qos: object) -> None:
        self.qos = qos


def _fast(sample: object):
    return fast_qos_to_profile(sample, reliability_map=_REL, durability_map=_DUR, history_map=_HIST)


def test_fast_full_profile():
    qos = _FastQos(rel=1, dur=1, hist=0, depth=7, deadline=_FastDeadline(seconds=1, nanosec=500))
    profile = _fast(_FastSample(qos))
    assert profile is not None
    assert profile.reliability == "RELIABLE"
    assert profile.durability == "TRANSIENT_LOCAL"
    assert profile.history == "KEEP_LAST"
    assert profile.history_depth == 7
    assert profile.deadline_ns == 1_000_000_500


def test_fast_no_deadline_still_builds_profile():
    profile = _fast(_FastSample(_FastQos(rel=0, dur=0, hist=1)))
    assert profile is not None
    assert profile.reliability == "BEST_EFFORT"
    assert profile.history == "KEEP_ALL"
    assert profile.deadline_ns is None


def test_fast_no_qos_returns_none():
    assert _fast(_FastSample(None)) is None


def test_fast_unknown_reliability_kind_returns_none():
    # Regression guard: an enum int not in the map (renamed / shifted across
    # a binding major version) must resolve the whole profile to None.
    assert _fast(_FastSample(_FastQos(rel=99, dur=1, hist=0))) is None


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


@pytest.mark.parametrize("normalizer", ["cyclone", "fast"])
def test_both_vendors_agree_on_identical_reliable_pair(normalizer: str):
    if normalizer == "cyclone":
        profile = cyclone_qos_to_profile(_CycloneSample([Reliable(), Volatile(), KeepLast()]))
    else:
        profile = _fast(_FastSample(_FastQos(rel=1, dur=0, hist=0)))
    assert profile is not None
    # Identical profile pair is always compatible regardless of vendor path.
    assert detect_mismatches(profile, profile) is None


# ------------------ deadline / defensive branch coverage --------------------
# The Deadline policy shape and the Fast period fields vary across binding
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


def test_fast_qos_missing_reliability_object_returns_none():
    class _Qos:  # no reliability / m_reliability attribute at all
        durability = _Kind(0)
        history = _Kind(0, 1)
        deadline = None

    assert _fast(_FastSample(_Qos())) is None


def test_fast_deadline_alt_period_field_names():
    # Some bindings expose `.sec` / `.nanoseconds` instead of
    # `.seconds` / `.nanosec`: the normalizer falls back to both.
    class _Period:
        sec = 2
        nanoseconds = 250

    class _Deadline:
        period = _Period()

    qos = _FastQos(rel=1, dur=0, hist=0, deadline=_Deadline())  # type: ignore[arg-type]
    profile = _fast(_FastSample(qos))
    assert profile is not None
    assert profile.deadline_ns == 2_000_000_250


def test_fast_history_depth_populated():
    profile = _fast(_FastSample(_FastQos(rel=1, dur=0, hist=0, depth=42)))
    assert profile is not None
    assert profile.history_depth == 42
