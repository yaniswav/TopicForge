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
    vendor_id_from_guid,
)
from topicforge.adapters.common.qos_normalize import apply_history_policy, cyclone_qos_to_profile
from topicforge.adapters.common.topic_filter import no_match_note, resolve_topic_filter
from topicforge.models import (
    DepartedEndpoint,
    EndpointInfo,
    EndpointListing,
    QosProfile,
    TopicSummary,
)

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
    vendors_by_guid: Mapping[str, str] | None = None,
    qos_to_profile: Callable[[Any], QosProfile | None] = cyclone_qos_to_profile,
) -> dict[str, Any]:
    """Flatten one endpoint discovery sample into the fields of `EndpointInfo`.

    `participants_by_guid` maps a formatted participant guid to its announced
    name, `vendors_by_guid` to its vendor tag (`unknown` when absent). Never
    raises on an odd sample: missing pieces become `None`.
    """
    participant_guid = format_participant_key(getattr(sample, "participant_key", None))
    vendor = (vendors_by_guid or {}).get(participant_guid) or _vendor_from_participant_key(sample)
    is_observer = observer_guid is not None and participant_guid == observer_guid
    return {
        "guid": format_guid(cyclone_extract_guid(sample)),
        "role": role,
        "participant_guid": participant_guid,
        "participant_name": participants_by_guid.get(participant_guid),
        "participant_vendor": vendor,
        "topic": cyclone_extract_topic_name(sample) or "unknown",
        "type_name": cyclone_extract_type_name(sample),
        "type_id": type_id_text(sample),
        "qos": apply_history_policy(qos_to_profile(sample), vendor=vendor, is_observer=is_observer),
        "announced_ns": announced_ns_of(sample),
        "is_observer": is_observer,
    }


def _effective_partitions(endpoint: EndpointInfo) -> list[str]:
    """Partitions that count for matching: no policy or an empty list is the default `""`."""
    names = endpoint.qos.partitions if endpoint.qos else None
    return list(names) if names else [""]


def _departed_of(endpoints: Iterable[EndpointInfo]) -> list[DepartedEndpoint]:
    """Departed entries, newest first."""
    found = [
        DepartedEndpoint(
            guid=e.guid,
            participant_guid=e.participant_guid,
            participant_name=e.participant_name,
            gone_ns=e.gone_ns,
        )
        for e in endpoints
    ]
    return sorted(found, key=lambda d: -(d.gone_ns or 0))


def summarize_by_topic(
    endpoints: Iterable[EndpointInfo], departed: Iterable[EndpointInfo] = ()
) -> list[TopicSummary]:
    """Group live endpoints by topic, flag topics with only one side, list departed ones."""
    groups: dict[str, list[EndpointInfo]] = {}
    for ep in endpoints:
        groups.setdefault(ep.topic, [])
        groups[ep.topic].append(ep)
    gone: dict[str, list[EndpointInfo]] = {}
    for ep in departed:
        gone.setdefault(ep.topic, []).append(ep)
        groups.setdefault(ep.topic, [])
    summaries = []
    for topic in sorted(groups):
        eps = groups[topic]
        gone_eps = gone.get(topic, [])
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
                type_names=sorted({e.type_name for e in [*eps, *gone_eps] if e.type_name}),
                writer_count=writers,
                reader_count=readers,
                partitions=sorted({p for e in eps for p in _effective_partitions(e)}),
                departed_writers=_departed_of(e for e in gone_eps if e.role == "writer"),
                departed_readers=_departed_of(e for e in gone_eps if e.role == "reader"),
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
    departed_records: Iterable[Mapping[str, Any]] = (),
    include_departed: bool = False,
) -> EndpointListing:
    """Apply the filters to `endpoint_record` dicts and assemble the envelope.

    The observer's own endpoints are dropped unless `include_observer`.
    `total_discovered` counts every record before any filter; the roll-up
    covers every match, while `endpoints` is capped at `MAX_LISTED_ENDPOINTS`.
    """
    all_records = list(records)
    gone_records = list(departed_records)
    wanted_participant = participant_guid.lower() if participant_guid else None
    visible = [r for r in all_records if include_observer or not r["is_observer"]]
    known = {r["topic"] for r in visible} | {r["topic"] for r in gone_records}
    resolved: str | None = topic
    note = None
    if topic is not None:
        resolved, note = resolve_topic_filter(topic, known)

    def selected(rec: Mapping[str, Any]) -> bool:
        return (topic is None or rec["topic"] == resolved) and (
            wanted_participant is None or rec["participant_guid"].lower() == wanted_participant
        )

    matched = [
        EndpointInfo(**rec, domain_id=domain_id, mode_effective=mode_effective)
        for rec in visible
        if selected(rec)
    ]
    live_guids = {r["guid"] for r in all_records}
    gone = [
        EndpointInfo(**rec, domain_id=domain_id, mode_effective=mode_effective)
        for rec in gone_records
        if selected(rec) and rec["guid"] not in live_guids
    ]
    if topic is not None and resolved is None:
        note = no_match_note(topic, known) + (
            f" ({len(all_records)} endpoint(s) discovered on other topics)" if all_records else ""
        )
    shown = matched + gone if include_departed else matched
    shown.sort(key=lambda e: (e.topic, e.role, e.guid))
    listed = shown[:MAX_LISTED_ENDPOINTS]
    return EndpointListing(
        domain_id=domain_id,
        snapshot_ns=time.time_ns() if snapshot_ns is None else snapshot_ns,
        observer_guid=observer_guid,
        endpoints=listed,
        by_topic=summarize_by_topic(matched, gone),
        total_discovered=len(all_records),
        returned=len(listed),
        truncated=len(shown) > len(listed),
        departed_endpoints=len(gone),
        excluded_observer_endpoints=(
            0 if include_observer else sum(1 for r in all_records if r["is_observer"])
        ),
        note=note,
        mode_effective=mode_effective,
    )


def _vendor_from_participant_key(sample: Any) -> str:
    """Vendor from the GUID prefix carried by the endpoint itself.

    Used when the participant is no longer in the live cache (it left): the
    endpoint's `participant_key` still holds the prefix the vendor came from.
    """
    key = getattr(sample, "participant_key", None)
    raw = getattr(key, "bytes", key)
    if not isinstance(raw, (bytes, bytearray)):
        return "unknown"
    return canonicalize_vendor_id(vendor_id_from_guid(bytes(raw)))


def participant_vendors(participant_samples: Iterable[Any]) -> dict[str, str]:
    """Vendor tag by formatted guid, from raw DCPSParticipant samples (as `list_participants`)."""
    return {
        format_guid(cyclone_extract_guid(s)): canonicalize_vendor_id(cyclone_extract_vendor_id(s))
        for s in participant_samples
    }


def participant_names(participant_samples: Iterable[Any]) -> dict[str, str | None]:
    """Announced participant name by formatted guid, from raw DCPSParticipant samples."""
    return {
        format_guid(cyclone_extract_guid(s)): cyclone_extract_participant_name(s)
        for s in participant_samples
    }


def endpoint_infos_from_samples(
    participant_samples: Iterable[Any],
    publication_samples: Iterable[Any],
    subscription_samples: Iterable[Any],
    *,
    domain_id: int,
    mode_effective: Literal["mock", "live"],
    observer_guid: str | None,
) -> list[EndpointInfo]:
    """Raw builtin samples in, every `EndpointInfo` out (no filtering, observer included)."""
    names = participant_names(participant_samples)
    vendors = participant_vendors(participant_samples)
    records = [
        endpoint_record(s, "writer", names, observer_guid, vendors_by_guid=vendors)
        for s in publication_samples
    ]
    records += [
        endpoint_record(s, "reader", names, observer_guid, vendors_by_guid=vendors)
        for s in subscription_samples
    ]
    return [
        EndpointInfo(**rec, domain_id=domain_id, mode_effective=mode_effective) for rec in records
    ]


def departed_endpoint_records(
    departed: Iterable[tuple[str, Any, int, str | None]],
    names: Mapping[str, str | None],
    vendors: Mapping[str, str],
    observer_guid: str | None,
) -> list[dict[str, Any]]:
    """Records of endpoints whose participant left: `(role, sample, gone_ns, name)` in.

    The participant name remembered at departure wins over the (now absent)
    announcement; `gone_ns` marks the record as departed.
    """
    out = []
    for role, sample, gone_ns, name in departed:
        rec = endpoint_record(sample, role, names, observer_guid, vendors_by_guid=vendors)  # type: ignore[arg-type]
        rec["participant_name"] = name or rec["participant_name"]
        rec["gone_ns"] = gone_ns
        out.append(rec)
    return out


def listing_from_samples(
    participant_samples: Iterable[Any],
    publication_samples: Iterable[Any],
    subscription_samples: Iterable[Any],
    *,
    departed: Iterable[tuple[str, Any, int, str | None]] = (),
    **listing_kwargs: Any,
) -> EndpointListing:
    """Raw builtin samples in, `EndpointListing` out: the whole pure pipeline.

    Keeps the sample source swappable (direct reads today, a cache filled by a
    tracker thread later) without touching the logic. `listing_kwargs` are the
    `build_endpoint_listing` keywords (`domain_id`, `mode_effective`,
    `observer_guid`, filters).
    """
    names = participant_names(participant_samples)
    vendors = participant_vendors(participant_samples)
    observer = listing_kwargs.get("observer_guid")
    records = [
        endpoint_record(s, "writer", names, observer, vendors_by_guid=vendors)
        for s in publication_samples
    ]
    records += [
        endpoint_record(s, "reader", names, observer, vendors_by_guid=vendors)
        for s in subscription_samples
    ]
    gone = departed_endpoint_records(departed, names, vendors, observer)
    return build_endpoint_listing(records, departed_records=gone, **listing_kwargs)


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
