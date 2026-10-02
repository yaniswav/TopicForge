"""Whole-bus QoS scan over discovered endpoints: binding-free, testable with fakes.

Takes the `EndpointInfo` records every adapter can build, pairs readers with
writers per topic and applies, in order: type name, Partition, then the RxO
rules of `qos_analyzer`. Pairs DDS will never match (different partitions or
type names) land in `MismatchScan.not_matched` and are not run through the
RxO rules, so a partition split is never mislabeled as a Reliability problem.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from topicforge.adapters.common.qos_analyzer import (
    RXO_POLICIES,
    analyze_pair,
    effective_partitions,
    partitions_match,
)
from topicforge.models import (
    EndpointInfo,
    MismatchReport,
    MismatchScan,
    NotMatchedPair,
)

POLICIES_CHECKED: list[str] = [*RXO_POLICIES, "Partition", "type name", "History (risky only)"]

POLICIES_UNCHECKED: list[str] = [
    "Presentation: not announced reliably by the discovery data TopicForge reads",
    "Liveliness (runtime state): only the declared kind and lease are compared, "
    "not whether a writer is actually alive",
    "TypeConsistency (XTypes assignability): only type names are compared; "
    "differing type ids are reported as a hint, not a verdict",
    "Resource limits, TimeBasedFilter, Lifespan, Durability service, transport and "
    "security settings: not compared",
    "Anything not discoverable: DDS Security, vendor-specific QoS and runtime behavior "
    "(data flow) are invisible to discovery, so an empty `reports` is not proof the bus "
    "is healthy",
]

_MAX_HINTS = 20
_MAX_NEAR_DISTANCE = 2


def levenshtein(a: str, b: str) -> int:
    """Edit distance between two strings (insert, delete, substitute)."""
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _not_matched(
    reader: EndpointInfo,
    writer: EndpointInfo,
    reason: Literal["partition", "type_name"],
    detail: str,
) -> NotMatchedPair:
    return NotMatchedPair(
        topic=reader.topic,
        reader_guid=reader.guid,
        reader_participant_guid=reader.participant_guid,
        reader_participant_name=reader.participant_name,
        writer_guid=writer.guid,
        writer_participant_guid=writer.participant_guid,
        writer_participant_name=writer.participant_name,
        reason=reason,
        detail=detail,
    )


def _separation(reader: EndpointInfo, writer: EndpointInfo) -> NotMatchedPair | None:
    """Why DDS will not match this pair, `None` when it will (QoS aside)."""
    r_parts = effective_partitions(reader.qos.partitions if reader.qos else None)
    w_parts = effective_partitions(writer.qos.partitions if writer.qos else None)
    if reader.qos and writer.qos and not partitions_match(r_parts, w_parts):
        return _not_matched(
            reader, writer, "partition", f"reader partitions {r_parts}, writer partitions {w_parts}"
        )
    if reader.type_name and writer.type_name and reader.type_name != writer.type_name:
        return _not_matched(
            reader,
            writer,
            "type_name",
            f"reader type {reader.type_name!r}, writer type {writer.type_name!r}",
        )
    return None


def _report(
    reader: EndpointInfo, writer: EndpointInfo, mode: Literal["mock", "live"]
) -> tuple[MismatchReport | None, list[str]]:
    """RxO analysis of a pair that DDS would match: the report (if any) and unchecked names."""
    assert reader.qos and writer.qos
    analysis = analyze_pair(reader.qos, writer.qos)
    if not analysis.details:
        return None, analysis.unchecked
    return (
        MismatchReport(
            topic=reader.topic,
            reader_guid=reader.guid,
            writer_guid=writer.guid,
            reader_participant_guid=reader.participant_guid,
            reader_participant_name=reader.participant_name,
            writer_participant_guid=writer.participant_guid,
            writer_participant_name=writer.participant_name,
            reader_type_name=reader.type_name,
            writer_type_name=writer.type_name,
            incompatible_policies=[d.policy for d in analysis.details],
            severity="incompatible" if analysis.incompatible else "risky",
            details=analysis.details,
            unchecked=analysis.unchecked,
            mode_effective=mode,
        ),
        analysis.unchecked,
    )


def _is_builtin(topic: str) -> bool:
    return topic.startswith("DCPS")


def _orphan_hints(by_topic: dict[str, list[EndpointInfo]], scope: set[str]) -> list[str]:
    """Orphan topics, and near-identical names across the writer-only / reader-only sides."""
    no_reader = sorted(t for t, e in by_topic.items() if all(x.role == "writer" for x in e))
    no_writer = sorted(t for t, e in by_topic.items() if all(x.role == "reader" for x in e))
    hints: list[str] = []
    paired: set[str] = set()
    for wt in no_reader:
        for rt in no_writer:
            if levenshtein(wt, rt) <= _MAX_NEAR_DISTANCE and (wt in scope or rt in scope):
                paired.update((wt, rt))
                hints.append(
                    f"Topic {wt!r} has a writer but no reader, and {rt!r} has a reader but "
                    f"no writer: the names differ by {levenshtein(wt, rt)} edit(s). "
                    "Likely a topic name typo."
                )
    for topic in no_reader + no_writer:
        if topic in scope and topic not in paired:
            side = "writers but no reader" if topic in no_reader else "readers but no writer"
            hints.append(f"Topic {topic!r} has {side}: there is no pair to compare.")
    return hints


def _type_id_hints(pairs: list[tuple[EndpointInfo, EndpointInfo]]) -> list[str]:
    hints = []
    for reader, writer in pairs:
        if reader.type_id and writer.type_id and reader.type_id != writer.type_id:
            hints.append(
                f"Topic {reader.topic!r}: type ids differ between reader {reader.guid} and "
                f"writer {writer.guid} for type {reader.type_name!r}; they may still be "
                "assignable under XTypes, TopicForge cannot confirm."
            )
    return hints


def _unchecked_hints(unchecked_counts: dict[str, int], skipped: int) -> list[str]:
    hints = [
        f"{count} pair(s) could not be checked on {name}: a side did not announce a value."
        for name, count in sorted(unchecked_counts.items())
    ]
    if skipped:
        hints.append(
            f"{skipped} endpoint(s) announced no usable QoS profile and were left out of pairing."
        )
    return hints


def scan_endpoints(
    endpoints: Iterable[EndpointInfo],
    *,
    topic: str | None = None,
    mode_effective: Literal["mock", "live"] = "live",
) -> MismatchScan:
    """Pair every reader with every writer per topic and build the `MismatchScan`.

    The observer's own endpoints are ignored. `topic` scopes reports and
    pairs; near-name hints still look at every topic so a typo on the other
    side is found.
    """
    by_topic: dict[str, list[EndpointInfo]] = {}
    for ep in endpoints:
        if not ep.is_observer and not _is_builtin(ep.topic):
            by_topic.setdefault(ep.topic, []).append(ep)
    scope = {t for t in by_topic if topic is None or t == topic}

    reports: list[MismatchReport] = []
    not_matched: list[NotMatchedPair] = []
    pairs_checked = 0
    skipped = 0
    unchecked_counts: dict[str, int] = {}
    matched_pairs: list[tuple[EndpointInfo, EndpointInfo]] = []
    for tname in sorted(scope):
        eps = by_topic[tname]
        readers = [e for e in eps if e.role == "reader"]
        writers = [e for e in eps if e.role == "writer"]
        skipped += sum(1 for e in eps if e.qos is None)
        for reader in (r for r in readers if r.qos):
            for writer in (w for w in writers if w.qos):
                pairs_checked += 1
                apart = _separation(reader, writer)
                if apart is not None:
                    not_matched.append(apart)
                    continue
                matched_pairs.append((reader, writer))
                report, unchecked = _report(reader, writer, mode_effective)
                for name in unchecked:
                    unchecked_counts[name] = unchecked_counts.get(name, 0) + 1
                if report is not None:
                    reports.append(report)

    hints = _orphan_hints(by_topic, scope) + _type_id_hints(matched_pairs)
    hints += _unchecked_hints(unchecked_counts, skipped)
    return MismatchScan(
        reports=reports,
        not_matched=not_matched,
        hints=hints[:_MAX_HINTS],
        pairs_checked=pairs_checked,
        topics_scanned=len(scope),
        policies_checked=list(POLICIES_CHECKED),
        policies_unchecked=list(POLICIES_UNCHECKED),
        mode_effective=mode_effective,
    )


__all__ = ["POLICIES_CHECKED", "POLICIES_UNCHECKED", "levenshtein", "scan_endpoints"]
