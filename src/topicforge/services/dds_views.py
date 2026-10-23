"""Derived views of DDS results that save a client from counting or joining by hand.

Pure functions over the result models, applied by the `Inspector` after an adapter
answers, so every backend (mock, Cyclone, Fast DDS) gets them:

- `restarts_by_name`: how many GUIDs each participant name carried, and how often it left;
- `compact_endpoints`: the `fields="compact"` form of an endpoint listing;
- `with_event_ages`: seconds-ago next to the `*_ns` timestamps of participant events;
- `domain_note`: the sentence that says a requested `domain_id` was not the one observed.
"""

from __future__ import annotations

from collections.abc import Iterable

from topicforge.models import (
    EndpointInfo,
    EndpointListing,
    NameLifecycle,
    ParticipantEvent,
    ParticipantInfo,
    QosProfile,
)

_NS_PER_S = 1_000_000_000

# Participant names that identify no process. ROS 2 names every participant of a
# context after its security enclave, `/` by default, so on Fast DDS (and on any rmw
# since Iron) one key would lump every node of the graph together.
_SHARED_NAMES = frozenset({"/"})


def _tally(rows: Iterable[tuple[str | None, str, bool]]) -> tuple[dict[str, NameLifecycle], int]:
    """Count lives and departures per name from `(name, guid, lost)` rows.

    Returns the names that restarted or left (see `restarts_by_name`) and how many rows had
    no usable name (empty, or shared by every node such as the enclave `/`). A name is kept
    when it carries more than one GUID or at least one departure.
    """
    guids: dict[str, set[str]] = {}
    departures: dict[str, int] = {}
    unnamed = 0
    for name, guid, lost in rows:
        if not name or name in _SHARED_NAMES:
            unnamed += 1
            continue
        guids.setdefault(name, set()).add(guid)
        if lost:
            departures[name] = departures.get(name, 0) + 1
    table = {
        name: NameLifecycle(lives=len(found), departures=departures.get(name, 0))
        for name, found in sorted(guids.items())
        if len(found) > 1 or departures.get(name, 0) > 0
    }
    return table, unnamed


def _unnamed_note(unnamed: int, what: str, by_node: bool = False) -> str | None:
    if not unnamed:
        return None
    note = (
        f"{unnamed} unnamed {what} not counted (no name, or the name `/` that every ROS 2 "
        "node shares): restarts cannot be tied together."
    )
    return note + (" `restarts_by_node` counts them by node name." if by_node else "")


def restarts_from_participants(
    participants: Iterable[ParticipantInfo],
) -> tuple[dict[str, NameLifecycle], str | None]:
    """`restarts_by_name` for a participant listing: lives are listed GUIDs, departures `left`."""
    table, unnamed = _tally((p.name, p.guid, p.status == "left") for p in participants)
    return table, _unnamed_note(unnamed, "participant(s) are", by_node=True)


def restarts_from_nodes(participants: Iterable[ParticipantInfo]) -> dict[str, NameLifecycle]:
    """`restarts_by_node`: the same tally keyed by ROS 2 node name, TopicForge's own CLI left out."""
    rows = (
        (node, p.guid, p.status == "left")
        for p in participants
        if not p.is_topicforge_cli
        for node in p.node_names
    )
    return _tally(rows)[0]


def restarts_from_events(
    events: Iterable[ParticipantEvent],
) -> tuple[dict[str, NameLifecycle], str | None]:
    """`restarts_by_name` for an event window: lives are GUIDs seen, departures `lost` events."""
    table, unnamed = _tally((e.name, e.guid, e.event_type == "lost") for e in events)
    return table, _unnamed_note(unnamed, "event(s) are")


def with_event_ages(events: list[ParticipantEvent], now_ns: int) -> list[ParticipantEvent]:
    """Copies of `events` with `age_s`, the seconds between each event and `now_ns`."""
    return [
        e.model_copy(update={"age_s": round(max(0, now_ns - e.timestamp_ns) / _NS_PER_S, 3)})
        for e in events
    ]


ENDPOINTLESS_AFTER_S = 10
"""Seconds a participant must have been known before no endpoint at all is worth a note."""


def endpointless_note(participants: Iterable[ParticipantInfo], now_ns: int) -> str | None:
    """Say so when active participants announce no endpoint this server can see, else `None`.

    Two causes fit and the data cannot tell them apart: a participant that really has no
    endpoint, or DDS Security hiding them from an observer without an identity. TopicForge's
    own participant and its `ros2` CLI processes are left out, and so is any participant
    first seen less than `ENDPOINTLESS_AFTER_S` seconds ago (its endpoints may be in flight).
    """
    limit_ns = ENDPOINTLESS_AFTER_S * _NS_PER_S
    count = sum(
        1
        for p in participants
        if p.endpoint_count == 0
        and p.status == "active"
        and not p.is_observer
        and not p.is_topicforge_cli
        and p.first_seen_ns is not None
        and now_ns - p.first_seen_ns > limit_ns
    )
    if not count:
        return None
    return (
        f"{count} participant(s) announce no endpoint TopicForge can see: an endpoint-less "
        "participant, or DDS Security hides them (TopicForge has no identity on this domain)."
    )


def domain_note(requested: int, observed: int | None) -> str | None:
    """Say so when a non-default `domain_id` differs from the joined domain, else `None`.

    `0` is the parameter default, so it cannot be told from "not given" and is never
    flagged. `observed` is `None` when the backend does not report a joined domain.
    """
    if observed is None or requested in (0, observed):
        return None
    return (
        f"domain_id {requested} was ignored: this server observes domain {observed}, joined at "
        f"startup. Set TOPICFORGE_DDS_DOMAIN_ID={requested} and restart to observe domain "
        f"{requested}."
    )


def join_notes(*notes: str | None) -> str | None:
    """Join the non-empty notes into one string, or `None`."""
    kept = [n for n in notes if n]
    return " ".join(kept) or None


def _duration(ns: int) -> str:
    if ns % _NS_PER_S == 0:
        return f"{ns // _NS_PER_S}s"
    if ns % 1_000_000 == 0:
        return f"{ns // 1_000_000}ms"
    return f"{ns}ns"


def qos_one_line(qos: QosProfile | None) -> str:
    """The announced QoS on one line, leaving out the values that are the DDS defaults."""
    if qos is None:
        return "qos unresolved"
    parts = [qos.reliability, qos.durability]
    if qos.history is not None:
        depth = f"({qos.history_depth})" if qos.history_depth is not None else ""
        parts.append(f"{qos.history}{depth}")
    if qos.deadline_ns is not None:
        parts.append(f"deadline={_duration(qos.deadline_ns)}")
    if qos.liveliness_kind not in (None, "AUTOMATIC"):
        lease = f"/{_duration(qos.liveliness_lease_ns)}" if qos.liveliness_lease_ns else ""
        parts.append(f"liveliness={qos.liveliness_kind}{lease}")
    if qos.ownership_kind == "EXCLUSIVE":
        parts.append(f"EXCLUSIVE(strength={qos.ownership_strength})")
    if qos.partitions and qos.partitions != [""]:
        parts.append("partitions=" + ",".join(qos.partitions))
    if qos.latency_budget_ns:
        parts.append(f"latency_budget={_duration(qos.latency_budget_ns)}")
    return " ".join(parts)


def _compact_endpoint(endpoint: EndpointInfo) -> EndpointInfo:
    return endpoint.model_copy(
        update={
            "qos": None,
            "qos_summary": qos_one_line(endpoint.qos),
            "type_id": None,
            "announced_ns": None,
            "ros_topic_note": None,
        }
    )


def compact_endpoints(listing: EndpointListing) -> EndpointListing:
    """The `fields="compact"` form: per-endpoint QoS as one line; type id, stamp and note dropped."""
    return listing.model_copy(
        update={
            "fields": "compact",
            "endpoints": [_compact_endpoint(e) for e in listing.endpoints],
        }
    )
