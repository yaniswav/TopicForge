"""Endpoint discovery records and the `list_endpoints` envelope: binding-free.

The Cyclone adapter turns each DCPSPublication / DCPSSubscription sample into
a flat record with `endpoint_record`, and every adapter (Cyclone, mock) builds
the final `EndpointListing` with `build_endpoint_listing`, so the filtering,
the observer exclusion, the cap and the orphan roll-up are written once and
unit-tested with fake samples, without any DDS binding installed.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Literal

from topicforge.adapters.common.dds_helpers import canonicalize_vendor_id, format_guid
from topicforge.adapters.common.dds_introspection import (
    cyclone_extract_guid,
    cyclone_extract_participant_name,
    cyclone_extract_topic_name,
    cyclone_extract_type_name,
    cyclone_extract_vendor_id,
)
from topicforge.adapters.common.qos_normalize import cyclone_qos_to_profile
from topicforge.models import EndpointInfo, EndpointListing, QosProfile, TopicSummary

MAX_LISTED_ENDPOINTS = 500
"""Hard cap on `EndpointListing.endpoints` (the roll-up still covers all matches)."""

_MAX_TYPE_ID_HEX = 64


def _uuid_bytes(value: Any) -> bytes | None:
    """16 raw bytes out of a `uuid.UUID`, bytes, or `.value` wrapper; else `None`."""
    raw = getattr(value, "bytes", None)
    if isinstance(raw, bytes):
        return raw
    if isinstance(value, bytes):
        return value
    inner = getattr(value, "value", None)
    return inner if isinstance(inner, bytes) else None


def format_participant_key(value: Any) -> str:
    """Render a participant key like the participant guids of `list_participants`."""
    return format_guid(_uuid_bytes(value))


def type_id_text(sample: Any) -> str | None:
    """Compact `COMPLETE:<hex>` form of the sample's XTypes type id, else `None`.

    cyclonedds exposes `typeid` (a TypeIdentifier) on endpoint samples, `None`
    when the writer announced no complete type. The hex is capped, so a
    long identifier stays a short string.
    """
    tid = getattr(sample, "typeid", None)
    if tid is None:
        return None
    try:
        raw = bytes(tid.serialize())
    except Exception:
        return None
    if not raw:
        return None
    return f"COMPLETE:{raw.hex()[:_MAX_TYPE_ID_HEX]}"


def announced_ns_of(sample: Any) -> int | None:
    """Source timestamp (ns) of a discovery sample's SampleInfo, `None` when absent."""
    info = getattr(sample, "sample_info", None)
    ts = getattr(info, "source_timestamp", None)
    if isinstance(ts, int) and not isinstance(ts, bool) and ts > 0:
        return ts
    return None


def endpoint_record(
    sample: Any,
    role: Literal["writer", "reader"],
    participants_by_guid: Mapping[str, str | None],
    observer_guid: str | None,
    *,
    qos_to_profile: Callable[[Any], QosProfile | None] = cyclone_qos_to_profile,
) -> dict[str, Any]:
    """Flatten one endpoint discovery sample into the fields of `EndpointInfo`.

    `participants_by_guid` maps a formatted participant guid to its announced
    name. Never raises on an odd sample: missing pieces become `None`.
    """
    participant_guid = format_participant_key(getattr(sample, "participant_key", None))
    return {
        "guid": format_guid(cyclone_extract_guid(sample)),
        "role": role,
        "participant_guid": participant_guid,
        "participant_name": participants_by_guid.get(participant_guid),
        "topic": cyclone_extract_topic_name(sample) or "unknown",
        "type_name": cyclone_extract_type_name(sample),
        "type_id": type_id_text(sample),
        "qos": qos_to_profile(sample),
        "announced_ns": announced_ns_of(sample),
        "is_observer": observer_guid is not None and participant_guid == observer_guid,
    }


def _effective_partitions(endpoint: EndpointInfo) -> list[str]:
    """Partitions that count for matching: no policy or an empty list is the default `""`."""
    names = endpoint.qos.partitions if endpoint.qos else None
    return list(names) if names else [""]


def summarize_by_topic(endpoints: Iterable[EndpointInfo]) -> list[TopicSummary]:
    """Group endpoints by topic and flag topics with only one side."""
    groups: dict[str, list[EndpointInfo]] = {}
    for ep in endpoints:
        groups.setdefault(ep.topic, []).append(ep)
    summaries = []
    for topic in sorted(groups):
        eps = groups[topic]
        writers = sum(1 for e in eps if e.role == "writer")
        readers = len(eps) - writers
        orphan: Literal["no_reader", "no_writer"] | None = None
        if writers and not readers:
            orphan = "no_reader"
        elif readers and not writers:
            orphan = "no_writer"
        summaries.append(
            TopicSummary(
                topic=topic,
                type_names=sorted({e.type_name for e in eps if e.type_name}),
                writer_count=writers,
                reader_count=readers,
                partitions=sorted({p for e in eps for p in _effective_partitions(e)}),
                orphan=orphan,
            )
        )
    return summaries


def build_endpoint_listing(
    records: Iterable[Mapping[str, Any]],
    *,
    domain_id: int,
    mode_effective: Literal["mock", "live"],
    observer_guid: str | None,
    topic: str | None = None,
    participant_guid: str | None = None,
    include_observer: bool = False,
    snapshot_ns: int | None = None,
) -> EndpointListing:
    """Apply the filters to `endpoint_record` dicts and assemble the envelope.

    The observer's own endpoints are dropped unless `include_observer`.
    `total_discovered` counts every record before any filter ; the roll-up
    covers every match, while `endpoints` is capped at `MAX_LISTED_ENDPOINTS`.
    """
    all_records = list(records)
    wanted_participant = participant_guid.lower() if participant_guid else None
    matched = [
        EndpointInfo(**rec, domain_id=domain_id, mode_effective=mode_effective)
        for rec in all_records
        if (include_observer or not rec["is_observer"])
        and (topic is None or rec["topic"] == topic)
        and (wanted_participant is None or rec["participant_guid"].lower() == wanted_participant)
    ]
    matched.sort(key=lambda e: (e.topic, e.role, e.guid))
    listed = matched[:MAX_LISTED_ENDPOINTS]
    return EndpointListing(
        domain_id=domain_id,
        snapshot_ns=time.time_ns() if snapshot_ns is None else snapshot_ns,
        observer_guid=observer_guid,
        endpoints=listed,
        by_topic=summarize_by_topic(matched),
        total_discovered=len(all_records),
        returned=len(listed),
        truncated=len(matched) > len(listed),
        mode_effective=mode_effective,
    )


def participant_names(participant_samples: Iterable[Any]) -> dict[str, str | None]:
    """Announced participant name by formatted guid, from raw DCPSParticipant samples."""
    return {
        format_guid(cyclone_extract_guid(s)): cyclone_extract_participant_name(s)
        for s in participant_samples
    }


def listing_from_samples(
    participant_samples: Iterable[Any],
    publication_samples: Iterable[Any],
    subscription_samples: Iterable[Any],
    **listing_kwargs: Any,
) -> EndpointListing:
    """Raw builtin samples in, `EndpointListing` out: the whole pure pipeline.

    Keeps the sample source swappable (direct reads today, a cache filled by a
    tracker thread later) without touching the logic. `listing_kwargs` are the
    `build_endpoint_listing` keywords (`domain_id`, `mode_effective`,
    `observer_guid`, filters).
    """
    names = participant_names(participant_samples)
    observer = listing_kwargs.get("observer_guid")
    records = [endpoint_record(s, "writer", names, observer) for s in publication_samples]
    records += [endpoint_record(s, "reader", names, observer) for s in subscription_samples]
    return build_endpoint_listing(records, **listing_kwargs)


RAW_TEXT_MAX_CHARS = 300


def builtin_payload(
    topic: str, sample: Any, names: Mapping[str, str | None], observer: str | None
) -> dict[str, object]:
    """Structured payload of one builtin DCPS sample (the `peek_dds_samples` shape).

    Keeps `vendor`, `guid`, `topic_name` and `type_name` (the example harness
    reads them) and adds the endpoint facts. `_raw_text` (the binding's repr,
    truncated) is included only when nothing structured could be read.
    """
    payload: dict[str, object] = {
        "vendor": canonicalize_vendor_id(cyclone_extract_vendor_id(sample)),
        "guid": format_guid(cyclone_extract_guid(sample)),
        "topic_name": cyclone_extract_topic_name(sample),
        "type_name": cyclone_extract_type_name(sample),
    }
    if topic == "DCPSParticipant":
        guid = str(payload["guid"])
        payload.update(
            role="participant",
            participant_guid=guid,
            participant_name=names.get(guid),
            type_id=None,
            is_observer=guid == observer,
        )
    else:
        role: Literal["writer", "reader"] = "writer" if topic == "DCPSPublication" else "reader"
        rec = endpoint_record(sample, role, names, observer)
        qos = rec["qos"]
        payload.update(
            role=role,
            participant_guid=rec["participant_guid"],
            participant_name=rec["participant_name"],
            type_id=rec["type_id"],
            is_observer=rec["is_observer"],
            qos=qos.model_dump(mode="json") if qos is not None else None,
        )
    payload["announced_ns"] = announced_ns_of(sample)
    if payload["guid"] == "unknown":
        raw = repr(sample)
        if len(raw) > RAW_TEXT_MAX_CHARS:
            raw = raw[:RAW_TEXT_MAX_CHARS] + "... [truncated]"
        payload["_raw_text"] = raw
    return payload
