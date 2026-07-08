"""Pair discovered reader/writer endpoints by topic and report QoS mismatches.

Binding-free — extracted from the identical `detect_qos_mismatches` bodies of
`dds_cyclone/adapter.py` and `dds_fast/adapter.py` (Lot 5, audit 2026-07-08).
Both adapters had ~40 lines of the same "group endpoints by topic, pair each
reader against each writer, run the pure analyzer, build a `MismatchReport`"
logic — differing only in the vendor's `qos_to_profile` / `extract_*`
callables and the endpoint source. That logic now lives here, once, and is
unit-testable with synthetic endpoint objects (no `cyclonedds` / `fastdds`).

Also fixes the O(readers x writers) QoS re-parse the audit flagged (P2/M7):
each writer's profile is computed once per topic, not once per reader.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from topicforge.adapters.common.dds_helpers import format_guid
from topicforge.adapters.common.qos_analyzer import detect_mismatches
from topicforge.models import MismatchReport, QosProfile


def detect_mismatches_across_endpoints(
    *,
    subs: list[Any],
    pubs: list[Any],
    topic: str | None,
    qos_to_profile: Callable[[Any], QosProfile | None],
    extract_topic_name: Callable[[Any], str | None],
    extract_guid: Callable[[Any], bytes | None],
    mode_effective: str = "live",
) -> list[MismatchReport]:
    """Return one `MismatchReport` per incompatible (reader, writer) pair.

    `subs` / `pubs` are the discovered subscription / publication endpoint
    samples (vendor-native shapes). `qos_to_profile`, `extract_topic_name`,
    and `extract_guid` are the vendor's binding-free helpers (from
    `common.qos_normalize` / `common.dds_introspection`). Pass `topic` to
    scope to a single topic, or `None` for an exhaustive scan.

    Endpoints whose topic name cannot be resolved, or whose QoS cannot be
    normalized to a `QosProfile`, are skipped — the analyzer needs a full
    profile on both sides to make a meaningful claim.
    """
    by_topic: dict[str, tuple[list[Any], list[Any]]] = {}
    for sample in subs:
        tname = extract_topic_name(sample)
        if tname is None:
            continue
        if topic is not None and tname != topic:
            continue
        by_topic.setdefault(tname, ([], []))[0].append(sample)
    for sample in pubs:
        tname = extract_topic_name(sample)
        if tname is None:
            continue
        if topic is not None and tname != topic:
            continue
        by_topic.setdefault(tname, ([], []))[1].append(sample)

    reports: list[MismatchReport] = []
    for tname, (readers, writers) in by_topic.items():
        # Precompute each writer's profile once per topic — the pre-audit code
        # re-parsed every writer inside the reader loop (O(R*W)). (Audit P2/M7.)
        writer_profiles: list[tuple[Any, QosProfile]] = []
        for writer_sample in writers:
            writer_profile = qos_to_profile(writer_sample)
            if writer_profile is not None:
                writer_profiles.append((writer_sample, writer_profile))

        for reader_sample in readers:
            reader_profile = qos_to_profile(reader_sample)
            if reader_profile is None:
                continue
            for writer_sample, writer_profile in writer_profiles:
                result = detect_mismatches(reader_profile, writer_profile)
                if result is None:
                    continue
                policies, severity = result
                reports.append(
                    MismatchReport(
                        topic=tname,
                        reader_guid=format_guid(extract_guid(reader_sample)),
                        writer_guid=format_guid(extract_guid(writer_sample)),
                        incompatible_policies=policies,
                        severity=severity,
                        mode_effective=mode_effective,  # type: ignore[arg-type]
                    )
                )
    return reports


__all__ = ["detect_mismatches_across_endpoints"]
