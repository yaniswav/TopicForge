"""Pure QoS-mismatch analyzer: no DDS dependency.

Compares a reader QoS profile against a writer QoS profile and surfaces
the policies that block (or risk degrading) communication. Testable
against synthesized `QosProfile` pairs without any DDS middleware
installed: same convention as the live-adapter pure parsers.

Eight RxO (requested/offered) policies are compared: Reliability, Durability,
Deadline, Liveliness, LatencyBudget, Ownership, DestinationOrder and
DataRepresentation. History is reported separately as *risky*, never as an
RxO incompatibility. Partition is not an RxO policy: `partitions_match` is
evaluated first by the scan (`qos_scan.py`), and a pair it separates never
reaches the RxO rules. A policy is compared only when both sides announced
a value; otherwise it is returned under `PairAnalysis.unchecked`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from topicforge.models import PolicyMismatch, QosProfile

# Durability is totally ordered. Higher index = stronger guarantee.
_DURABILITY_ORDER: tuple[str, ...] = ("VOLATILE", "TRANSIENT_LOCAL", "TRANSIENT", "PERSISTENT")
_LIVELINESS_ORDER: tuple[str, ...] = ("AUTOMATIC", "MANUAL_BY_PARTICIPANT", "MANUAL_BY_TOPIC")
_DESTINATION_ORDER: tuple[str, ...] = ("BY_RECEPTION_TIMESTAMP", "BY_SOURCE_TIMESTAMP")

# `None` on a duration is infinite: modeling it as +infinity lets one
# comparison cover every finite/infinite combination.
_INFINITE = float("inf")

RXO_POLICIES: tuple[str, ...] = (
    "Reliability",
    "Durability",
    "Deadline",
    "Liveliness",
    "LatencyBudget",
    "Ownership",
    "DestinationOrder",
    "DataRepresentation",
)


@dataclass(frozen=True)
class PairAnalysis:
    """Outcome of comparing one reader profile against one writer profile."""

    incompatible: list[PolicyMismatch] = field(default_factory=list)
    risky: list[PolicyMismatch] = field(default_factory=list)
    unchecked: list[str] = field(default_factory=list)

    @property
    def details(self) -> list[PolicyMismatch]:
        """Every finding, incompatible ones first."""
        return self.incompatible + self.risky


def format_duration(ns: int | None) -> str:
    """Human-readable duration: `None` is `infinite`, else `3 s`, `100 ms`, `250 us`, `7 ns`."""
    if ns is None:
        return "infinite"
    for unit, size in (("s", 1_000_000_000), ("ms", 1_000_000), ("us", 1_000)):
        if ns >= size:
            value = ns / size
            return f"{int(value)} {unit}" if value == int(value) else f"{value:g} {unit}"
    return f"{ns} ns"


def _is_wildcard(name: str) -> bool:
    return "*" in name or "?" in name


def _wildcard_matches(pattern: str, name: str) -> bool:
    """`*` (any run, possibly empty) and `?` (one char) against a concrete name."""
    regex = "".join(".*" if c == "*" else "." if c == "?" else re.escape(c) for c in pattern)
    return re.fullmatch(regex, name, flags=re.DOTALL) is not None


def effective_partitions(partitions: list[str] | None) -> list[str]:
    """No policy or an empty list is the default partition `""`."""
    return list(partitions) if partitions else [""]


def partitions_match(reader: list[str] | None, writer: list[str] | None) -> bool:
    """True when at least one reader partition matches one writer partition.

    A wildcard on one side matches a concrete name on the other ; two
    wildcards never match each other (not even identical ones). Only `*` and
    `?` are wildcards: `[12]` is a literal, so `robot[12]` matches only an
    identical `robot[12]`. Checked against cyclonedds 11.0.1 on a live bus
    (2026-10-02, `subscription_matched_status`): `r*`/`r*`, `robot*`/`robot?`
    and `*`/`*` did not match, `robot*`/`robot1` and `""`/`*` did, `robot1`
    did not match `robot[12]` or `robot[1]`. Other vendors may treat `[...]`
    as a character class.
    """
    for rp in effective_partitions(reader):
        for wp in effective_partitions(writer):
            r_wild, w_wild = _is_wildcard(rp), _is_wildcard(wp)
            if r_wild and w_wild:
                continue
            if r_wild and _wildcard_matches(rp, wp):
                return True
            if w_wild and _wildcard_matches(wp, rp):
                return True
            if not r_wild and not w_wild and rp == wp:
                return True
    return False


def _finding(policy: str, requested: str, offered: str, rule: str) -> PolicyMismatch:
    return PolicyMismatch(policy=policy, requested=requested, offered=offered, rule=rule)


def _ns(value: int | None) -> float:
    return _INFINITE if value is None else float(value)


def _liveliness_text(kind: str, lease_ns: int | None) -> str:
    return f"{kind} lease {format_duration(lease_ns)}"


def _check_liveliness(reader: QosProfile, writer: QosProfile) -> PolicyMismatch | None:
    assert reader.liveliness_kind and writer.liveliness_kind
    kind_ok = _LIVELINESS_ORDER.index(writer.liveliness_kind) >= _LIVELINESS_ORDER.index(
        reader.liveliness_kind
    )
    lease_ok = _ns(writer.liveliness_lease_ns) <= _ns(reader.liveliness_lease_ns)
    if kind_ok and lease_ok:
        return None
    return _finding(
        "Liveliness",
        _liveliness_text(reader.liveliness_kind, reader.liveliness_lease_ns),
        _liveliness_text(writer.liveliness_kind, writer.liveliness_lease_ns),
        "offered kind must be >= requested kind (AUTOMATIC < MANUAL_BY_PARTICIPANT < "
        "MANUAL_BY_TOPIC) and offered lease must be <= requested lease",
    )


def _check_representation(reader: QosProfile, writer: QosProfile) -> PolicyMismatch | None:
    accepted = reader.data_representation or []
    offered = writer.data_representation or []
    # The writer's list order is not preserved by the normalization when it
    # offers several, so a multi-representation writer is checked by overlap.
    ok = (offered[0] in accepted) if len(offered) == 1 else bool(set(offered) & set(accepted))
    if ok:
        return None
    return _finding(
        "DataRepresentation",
        "/".join(accepted),
        "/".join(offered),
        "the reader's accepted representations must contain the writer's offered one",
    )


def _is_unknown(policy: str, reader: QosProfile, writer: QosProfile) -> bool:
    """True when either side announced `policy` with a value that could not be read."""
    return policy in (reader.unknown_policies or []) or policy in (writer.unknown_policies or [])


def _core_findings(reader: QosProfile, writer: QosProfile) -> list[PolicyMismatch]:
    found: list[PolicyMismatch] = []
    if reader.reliability == "RELIABLE" and writer.reliability == "BEST_EFFORT":
        found.append(
            _finding(
                "Reliability",
                reader.reliability,
                writer.reliability,
                "a RELIABLE reader needs a RELIABLE writer",
            )
        )
    if _DURABILITY_ORDER.index(reader.durability) > _DURABILITY_ORDER.index(writer.durability):
        found.append(
            _finding(
                "Durability",
                reader.durability,
                writer.durability,
                "offered durability must be >= requested (VOLATILE < TRANSIENT_LOCAL < "
                "TRANSIENT < PERSISTENT)",
            )
        )
    if not _is_unknown("Deadline", reader, writer) and _ns(writer.deadline_ns) > _ns(
        reader.deadline_ns
    ):
        found.append(
            _finding(
                "Deadline",
                format_duration(reader.deadline_ns),
                format_duration(writer.deadline_ns),
                "offered deadline period must be <= requested (no deadline is infinite)",
            )
        )
    return found


def _optional_findings(
    reader: QosProfile, writer: QosProfile
) -> tuple[list[PolicyMismatch], list[str]]:
    """Findings and unchecked names for the policies a side may not have announced."""
    found: list[PolicyMismatch] = []
    unchecked: list[str] = []

    if (
        reader.liveliness_kind is None
        or writer.liveliness_kind is None
        or _is_unknown("Liveliness", reader, writer)
    ):
        unchecked.append("Liveliness")
    elif (f := _check_liveliness(reader, writer)) is not None:
        found.append(f)

    if (
        reader.latency_budget_ns is None
        or writer.latency_budget_ns is None
        or _is_unknown("LatencyBudget", reader, writer)
    ):
        unchecked.append("LatencyBudget")
    elif writer.latency_budget_ns > reader.latency_budget_ns:
        found.append(
            _finding(
                "LatencyBudget",
                format_duration(reader.latency_budget_ns),
                format_duration(writer.latency_budget_ns),
                "offered latency budget must be <= requested",
            )
        )

    if reader.ownership_kind is None or writer.ownership_kind is None:
        unchecked.append("Ownership")
    elif reader.ownership_kind != writer.ownership_kind:
        found.append(
            _finding(
                "Ownership",
                reader.ownership_kind,
                writer.ownership_kind,
                "reader and writer must use the same ownership kind (strength is not compared "
                "here). Among EXCLUSIVE writers the live one with the highest strength "
                "delivers; which writer currently owns an instance is reader-side runtime "
                "state that TopicForge cannot observe",
            )
        )

    if reader.destination_order is None or writer.destination_order is None:
        unchecked.append("DestinationOrder")
    elif _DESTINATION_ORDER.index(writer.destination_order) < _DESTINATION_ORDER.index(
        reader.destination_order
    ):
        found.append(
            _finding(
                "DestinationOrder",
                reader.destination_order,
                writer.destination_order,
                "offered order must be >= requested (BY_RECEPTION_TIMESTAMP < BY_SOURCE_TIMESTAMP)",
            )
        )

    if not reader.data_representation or not writer.data_representation:
        unchecked.append("DataRepresentation")
    elif (f := _check_representation(reader, writer)) is not None:
        found.append(f)
    return found, unchecked


def analyze_pair(reader: QosProfile, writer: QosProfile) -> PairAnalysis:
    """Compare the RxO policies of one pair that already shares a partition and a type.

    Pure function: no I/O, deterministic. Findings follow the order of
    `RXO_POLICIES`; History is appended as a risky finding.
    """
    optional, unchecked = _optional_findings(reader, writer)
    if _is_unknown("Deadline", reader, writer):
        unchecked.insert(0, "Deadline")
    incompatible = _core_findings(reader, writer) + optional
    incompatible.sort(key=lambda f: RXO_POLICIES.index(f.policy))
    risky: list[PolicyMismatch] = []
    if reader.history == "KEEP_ALL" and writer.history == "KEEP_LAST":
        depth = f" depth {writer.history_depth}" if writer.history_depth is not None else ""
        risky.append(
            _finding(
                "History",
                "KEEP_ALL",
                f"KEEP_LAST{depth}",
                "not an RxO incompatibility: the writer may drop samples the reader "
                "expects to keep under load",
            )
        )
    return PairAnalysis(incompatible=incompatible, risky=risky, unchecked=unchecked)


def detect_mismatches(
    reader_qos: QosProfile, writer_qos: QosProfile
) -> tuple[list[str], Literal["incompatible", "risky"]] | None:
    """Policy names and severity for a pair, `None` when fully compatible.

    Thin view over `analyze_pair` for callers that need no values.
    """
    analysis = analyze_pair(reader_qos, writer_qos)
    if not analysis.details:
        return None
    severity: Literal["incompatible", "risky"] = (
        "incompatible" if analysis.incompatible else "risky"
    )
    return [d.policy for d in analysis.details], severity
