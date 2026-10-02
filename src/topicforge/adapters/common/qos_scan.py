"""Whole-bus QoS scan over discovered endpoints: binding-free, testable with fakes.

Takes the `EndpointInfo` records every adapter can build, pairs readers with
writers per topic and applies, in order: Partition, type name, then the RxO
rules of `qos_analyzer`. Pairs DDS will never match (different partitions or
type names) land in `MismatchScan.not_matched` and are not run through the
RxO rules, so a partition split is never mislabeled as a Reliability problem.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Literal

from topicforge.adapters.common.qos_analyzer import (
    RXO_POLICIES,
    analyze_pair,
    effective_partitions,
    partitions_match,
)
from topicforge.adapters.common.topic_filter import (
    levenshtein,
    no_match_note,
    resolve_topic_filter,
)
from topicforge.models import (
    EndpointInfo,
    MatchedPair,
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
_LATE_JOIN_NS = 1_000_000_000
MAX_ITEMS = 200
"""Cap on `reports`, `matched` and `not_matched`; the `*_total` fields carry the real counts."""
_MAX_COMPARED_ORPHANS = 200
_MAX_DISTANCE_CALLS = 20_000
"""Budget of edit-distance computations per scan (pairs whose lengths differ too much are free)."""


def _not_matched(
    reader: EndpointInfo,
    writer: EndpointInfo,
    reason: Literal["partition", "type_name"],
    detail: str,
) -> NotMatchedPair:
    latent = analyze_pair(reader.qos, writer.qos).incompatible if reader.qos and writer.qos else []
    return NotMatchedPair(
        latent_incompatible_policies=latent,
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


def _same_host(
    reader: EndpointInfo, writer: EndpointInfo, hostnames: Mapping[str, str | None]
) -> bool:
    """True when both endpoints provably run on one host (same participant, or same hostname)."""
    if reader.participant_guid == writer.participant_guid and reader.participant_guid != "unknown":
        return True
    r_host = hostnames.get(reader.participant_guid)
    return r_host is not None and r_host == hostnames.get(writer.participant_guid)


def _late_joiner_note(
    reader: EndpointInfo, writer: EndpointInfo, hostnames: Mapping[str, str | None]
) -> str | None:
    """Why this reader missed earlier samples of a VOLATILE writer, `None` when it did not.

    Both announce times are on their own clocks, so the comparison is only
    meaningful when the two run on the same host.
    """
    if not (writer.qos and writer.qos.durability == "VOLATILE"):
        return None
    if reader.announced_ns is None or writer.announced_ns is None:
        return None
    if reader.announced_ns - writer.announced_ns <= _LATE_JOIN_NS:
        return None
    if not _same_host(reader, writer, hostnames):
        return None
    r = reader.participant_name or reader.guid
    w = writer.participant_name or writer.guid
    return (
        f"reader {r} joined after writer {w}, which is VOLATILE: samples published "
        f"before {r} joined are not delivered to it (by design)."
    )


def _matched(
    reader: EndpointInfo, writer: EndpointInfo, hostnames: Mapping[str, str | None]
) -> MatchedPair:
    note = _late_joiner_note(reader, writer, hostnames)
    return MatchedPair(
        late_joiner=note is not None,
        late_joiner_note=note,
        topic=reader.topic,
        type_name=reader.type_name or writer.type_name,
        reader_guid=reader.guid,
        reader_participant_guid=reader.participant_guid,
        reader_participant_name=reader.participant_name,
        writer_guid=writer.guid,
        writer_participant_guid=writer.participant_guid,
        writer_participant_name=writer.participant_name,
    )


def _is_builtin(topic: str) -> bool:
    return topic.startswith("DCPS")


# ROS 2 name mangling prefixes other than `rt/` (plain topics): service request
# and reply, legacy service/parameter/action prefixes.
_NON_TOPIC_PREFIXES = ("rq/", "rr/", "rs/", "rp/", "ra/")
_INFRASTRUCTURE_TOPICS = frozenset({"rosout", "parameter_events", "ros_discovery_info"})


def _is_typo_candidate(topic: str) -> bool:
    """True for plain topics (`rt/...` or a bare DDS name) worth comparing for typos.

    Services are request/reply pairs (a server without a client is normal, and
    `get_` / `set_` parameter services differ by one edit by design), actions
    carry `_action/` segments, and the logging, parameter-event and discovery
    topics are shared infrastructure. None of them is a user topic name that a
    typo could break.
    """
    if topic.startswith(_NON_TOPIC_PREFIXES):
        return False
    bare = topic[3:] if topic.startswith("rt/") else topic
    bare = bare.strip("/")
    if bare in _INFRASTRUCTURE_TOPICS:
        return False
    return "_action" not in bare.split("/")


def _orphan_side(endpoints: list[EndpointInfo]) -> Literal["writer", "reader"] | None:
    """The only role present on a topic, `None` when it has both (not an orphan)."""
    roles = {e.role for e in endpoints}
    return next(iter(roles)) if len(roles) == 1 else None


def _is_path_suffix(short: str, long: str) -> bool:
    """True when `long` is `<namespace>/<short>`, ignoring leading and trailing slashes."""
    s, lg = short.strip("/"), long.strip("/")
    return s != lg and lg.endswith("/" + s)


def _side_text(side: str) -> str:
    return "has a writer but no reader" if side == "writer" else "has readers but no writer"


def _orphan_hints(
    by_topic: dict[str, list[EndpointInfo]], scope: set[str]
) -> tuple[list[str], list[str]]:
    """Orphan topics checked against every other topic: (near-name hints, plain orphan hints).

    Near names are typos and namespaced twins. The comparison is bounded: pairs
    whose lengths differ by more than the distance are skipped, the edit
    distance gives up early, and at most `_MAX_COMPARED_ORPHANS` orphans are compared.
    """
    candidates = {t: e for t, e in by_topic.items() if _is_typo_candidate(t)}
    sides = {t: _orphan_side(e) for t, e in candidates.items()}
    orphans = sorted(t for t, side in sides.items() if side)
    topics = sorted(candidates)
    near: list[str] = []
    explained: set[str] = set()
    seen: set[frozenset[str]] = set()
    budget = _MAX_DISTANCE_CALLS
    over_budget = False
    for orphan in orphans[:_MAX_COMPARED_ORPHANS]:
        for other in topics:
            if other == orphan or not (orphan in scope or other in scope):
                continue
            key = frozenset((orphan, other))
            if key in seen:
                continue
            both_orphans = sides[other] is not None
            if abs(len(orphan) - len(other)) > _MAX_NEAR_DISTANCE:
                distance = _MAX_NEAR_DISTANCE + 1
            elif budget > 0:
                budget -= 1
                distance = levenshtein(orphan, other, _MAX_NEAR_DISTANCE)
            else:
                over_budget = True
                distance = _MAX_NEAR_DISTANCE + 1
            if distance <= _MAX_NEAR_DISTANCE:
                if both_orphans and sides[other] != sides[orphan]:
                    seen.add(key)
                    explained.update(key)
                    near.append(_typo_hint(orphan, other, sides, distance))
            elif _is_path_suffix(orphan, other) or _is_path_suffix(other, orphan):
                seen.add(key)
                explained.update(key if both_orphans else (orphan,))
                near.append(
                    f"Topic {orphan!r} {_side_text(sides[orphan] or 'writer')}, and "
                    f"{other!r} is the same name with or without a namespace: may be the "
                    "same data under a namespaced/remapped name."
                )
    plain: list[str] = []
    for topic in orphans:
        if topic in scope and topic not in explained:
            side = "writers but no reader" if sides[topic] == "writer" else "readers but no writer"
            plain.append(f"Topic {topic!r} has {side}: there is no pair to compare.")
    if len(orphans) > _MAX_COMPARED_ORPHANS:
        near.insert(
            0,
            f"{len(orphans)} orphan topics: only the first {_MAX_COMPARED_ORPHANS} were "
            "compared for typos, narrow the scan with `topic` to check the rest.",
        )
    if over_budget:
        near.insert(
            0,
            "The topic list is too large to compare every name for typos: near-name "
            "hints may be missing, narrow the scan with `topic`.",
        )
    return near, plain


def _typo_hint(orphan: str, other: str, sides: dict[str, str | None], distance: int) -> str:
    """Hint for a writer-only name next to a reader-only name."""
    wt, rt = (orphan, other) if sides[orphan] == "writer" else (other, orphan)
    return (
        f"Topic {wt!r} has a writer but no reader, and {rt!r} has a reader but "
        f"no writer: the names differ by {distance} edit(s). Likely a topic name typo."
    )


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
    hostnames: Mapping[str, str | None] | None = None,
) -> MismatchScan:
    """Pair every reader with every writer per topic and build the `MismatchScan`.

    The observer's own endpoints are ignored. `topic` scopes reports and
    pairs; near-name hints still look at every topic so a typo on the other
    side is found. `hostnames` maps a participant guid to its announced host,
    used only to tell a late joiner from clock skew. Lists are capped at
    `MAX_ITEMS` (the `*_total` fields keep the real counts).
    """
    hostnames = hostnames or {}
    by_topic: dict[str, list[EndpointInfo]] = {}
    for ep in endpoints:
        if not ep.is_observer and not _is_builtin(ep.topic):
            by_topic.setdefault(ep.topic, []).append(ep)
    scope_hints: list[str] = []
    wanted = topic
    if topic is not None:
        wanted, form_note = resolve_topic_filter(topic, by_topic)
        if form_note:
            scope_hints.append(form_note)
        elif wanted is None:
            scope_hints.append(no_match_note(topic, by_topic))
    scope = {t for t in by_topic if topic is None or t == wanted}

    reports: list[MismatchReport] = []
    not_matched: list[NotMatchedPair] = []
    pairs_checked = 0
    skipped = 0
    unchecked_counts: dict[str, int] = {}
    matched_pairs: list[tuple[EndpointInfo, EndpointInfo]] = []
    matched: list[MatchedPair] = []
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
                if report is None or report.severity != "incompatible":
                    matched.append(_matched(reader, writer, hostnames))

    near, plain = _orphan_hints(by_topic, scope)
    hints = scope_hints + near + _unchecked_hints(unchecked_counts, skipped) + plain
    hints += _type_id_hints(matched_pairs)
    if len(hints) > _MAX_HINTS:
        omitted = len(hints) - _MAX_HINTS
        hints = [*hints[:_MAX_HINTS], f"{omitted} more hint(s) omitted."]
    reports.sort(key=lambda r: r.severity != "incompatible")
    totals = (len(reports), len(matched), len(not_matched))
    return MismatchScan(
        reports=reports[:MAX_ITEMS],
        matched=matched[:MAX_ITEMS],
        not_matched=not_matched[:MAX_ITEMS],
        reports_total=totals[0],
        matched_total=totals[1],
        not_matched_total=totals[2],
        truncated=any(t > MAX_ITEMS for t in totals),
        hints=hints,
        pairs_checked=pairs_checked,
        topics_scanned=len(scope),
        policies_checked=list(POLICIES_CHECKED),
        policies_unchecked=list(POLICIES_UNCHECKED),
        mode_effective=mode_effective,
    )


__all__ = ["POLICIES_CHECKED", "POLICIES_UNCHECKED", "levenshtein", "scan_endpoints"]
