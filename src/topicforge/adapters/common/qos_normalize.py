"""Vendor QoS -> canonical `QosProfile` normalization: binding-free, testable.

Lives outside the adapters because they import their SDK at module top
level and so cannot be tested without it. A renamed policy key here would
silently return `None` and hide every mismatch.

The Cyclone path keys policies by binding class name. The Fast path keys
by integer enum value, so `fast_qos_to_profile` takes the three int->str
maps as parameters and this module imports no binding.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from topicforge.models import QosProfile

# Cyclone exposes QoS policies as nested classes under
# `cyclonedds.qos.Policy.*`; they are matched by bare class name.
CYCLONE_RELIABILITY_NAMES: dict[str, str] = {"Reliable": "RELIABLE", "BestEffort": "BEST_EFFORT"}
CYCLONE_DURABILITY_NAMES: dict[str, str] = {
    "Volatile": "VOLATILE",
    "TransientLocal": "TRANSIENT_LOCAL",
    "Transient": "TRANSIENT",
    "Persistent": "PERSISTENT",
}
CYCLONE_HISTORY_NAMES: dict[str, str] = {"KeepLast": "KEEP_LAST", "KeepAll": "KEEP_ALL"}
CYCLONE_LIVELINESS_NAMES: dict[str, str] = {
    "Automatic": "AUTOMATIC",
    "ManualByParticipant": "MANUAL_BY_PARTICIPANT",
    "ManualByTopic": "MANUAL_BY_TOPIC",
}
CYCLONE_OWNERSHIP_NAMES: dict[str, str] = {"Shared": "SHARED", "Exclusive": "EXCLUSIVE"}
CYCLONE_DESTINATION_ORDER_NAMES: dict[str, str] = {
    "ByReceptionTimestamp": "BY_RECEPTION_TIMESTAMP",
    "BySourceTimestamp": "BY_SOURCE_TIMESTAMP",
}

# cyclonedds reports an infinite duration as the largest int64.
INFINITE_DURATION_NS = 9_223_372_036_854_775_807


def duration_state(value: Any) -> tuple[int | None, bool]:
    """A binding duration as `(nanoseconds, readable)`.

    Infinite is `(None, True)`, so the int64 sentinel never leaks through as
    a 9.2e18 deadline (the mismatch analyzer would read it as a finite
    request). An unreadable value is `(None, False)`: the caller records the
    policy as unknown instead of letting it pass for infinite.
    """
    if hasattr(value, "to_nanoseconds"):
        value = value.to_nanoseconds()
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None, False
    if value >= INFINITE_DURATION_NS:
        return None, True
    return int(value), True


def duration_to_ns(value: Any) -> int | None:
    """A binding duration as nanoseconds; `None` when infinite or unreadable.

    Use `duration_state` to tell the two apart.
    """
    return duration_state(value)[0]


def _first_attr(policy: Any, names: tuple[str, ...]) -> Any:
    """First non-None attribute of `policy` among `names`."""
    for name in names:
        value = getattr(policy, name, None)
        if value is not None:
            return value
    return None


def _duration(policy: Any, attrs: tuple[str, ...], name: str, unknown: list[str]) -> int | None:
    """A duration field; an unreadable value is recorded in `unknown` under `name`."""
    ns, readable = duration_state(_first_attr(policy, attrs))
    if not readable and name not in unknown:
        unknown.append(name)
    return ns


def _data_representation(policy: Any) -> list[str] | None:
    """`XCDR1` / `XCDR2` flags of a DataRepresentation policy, `None` if neither is set."""
    found = []
    if getattr(policy, "use_cdrv0_representation", False):
        found.append("XCDR1")
    if getattr(policy, "use_xcdrv2_representation", False):
        found.append("XCDR2")
    return found or None


def _extended_policies(qos: Any) -> dict[str, Any]:
    """The liveliness, ownership, partition, latency, ordering and representation fields.

    Absent policies leave their key out; `QosProfile` defaults it to `None`.
    """
    out: dict[str, Any] = {}
    unknown: list[str] = []
    for policy in qos:
        cls_name = type(policy).__name__.rsplit(".", 1)[-1]
        if cls_name in CYCLONE_LIVELINESS_NAMES:
            out["liveliness_kind"] = CYCLONE_LIVELINESS_NAMES[cls_name]
            out["liveliness_lease_ns"] = _duration(
                policy, ("lease_duration", "duration"), "Liveliness", unknown
            )
        elif cls_name in CYCLONE_OWNERSHIP_NAMES:
            out["ownership_kind"] = CYCLONE_OWNERSHIP_NAMES[cls_name]
        elif cls_name == "OwnershipStrength":
            strength = getattr(policy, "strength", None)
            if isinstance(strength, int) and not isinstance(strength, bool):
                out["ownership_strength"] = strength
        elif cls_name == "Partition":
            names = getattr(policy, "partitions", None)
            if names is not None:
                out["partitions"] = [str(n) for n in names]
        elif cls_name == "LatencyBudget":
            out["latency_budget_ns"] = _duration(
                policy, ("budget", "duration"), "LatencyBudget", unknown
            )
        elif cls_name in CYCLONE_DESTINATION_ORDER_NAMES:
            out["destination_order"] = CYCLONE_DESTINATION_ORDER_NAMES[cls_name]
        elif cls_name == "DataRepresentation":
            out["data_representation"] = _data_representation(policy)
    # DDS semantics: no Partition policy (or an empty list) is the default partition "".
    out["partitions"] = out.get("partitions") or [""]
    if unknown:
        out["unknown_policies"] = unknown
    return out


def cyclone_qos_to_profile(sample: Any) -> QosProfile | None:
    """Map a Cyclone discovery sample's QoS to a `QosProfile`.

    Returns `None` when reliability or durability is missing. A missing
    history leaves `history` as `None`: SEDP does not carry it by spec.
    """
    qos = getattr(sample, "qos", None)
    if qos is None:
        return None

    reliability: str | None = None
    durability: str | None = None
    history: str | None = None
    history_depth: int | None = None
    deadline_ns: int | None = None
    deadline_unreadable = False

    try:
        for policy in qos:
            # cyclonedds 11.0.1 scopes policy class names, e.g.
            # "Reliability.BestEffort" (observed on a live bus).
            cls_name = type(policy).__name__.rsplit(".", 1)[-1]
            if cls_name in CYCLONE_RELIABILITY_NAMES:
                reliability = CYCLONE_RELIABILITY_NAMES[cls_name]
            elif cls_name in CYCLONE_DURABILITY_NAMES:
                durability = CYCLONE_DURABILITY_NAMES[cls_name]
            elif cls_name in CYCLONE_HISTORY_NAMES:
                history = CYCLONE_HISTORY_NAMES[cls_name]
                depth = getattr(policy, "depth", None)
                if isinstance(depth, int):
                    history_depth = depth
            elif cls_name == "Deadline":
                deadline_ns, readable = duration_state(
                    _first_attr(policy, ("duration", "deadline"))
                )
                deadline_unreadable = not readable
        extended = _extended_policies(qos)
        if deadline_unreadable:
            extended["unknown_policies"] = [*extended.get("unknown_policies", []), "Deadline"]
    except (TypeError, AttributeError):  # defensive against odd qos shapes
        return None

    if reliability is None or durability is None:
        return None

    return QosProfile(
        reliability=reliability,  # type: ignore[arg-type]
        durability=durability,  # type: ignore[arg-type]
        history=history,  # type: ignore[arg-type]
        history_depth=history_depth,
        deadline_ns=deadline_ns,
        **extended,
    )


def fast_qos_to_profile(
    sample: Any,
    *,
    reliability_map: Mapping[int, str],
    durability_map: Mapping[int, str],
    history_map: Mapping[int, str],
) -> QosProfile | None:
    """Map a Fast DDS discovery sample's QoS to a `QosProfile`.

    The three maps are the binding's integer-enum -> canonical-string
    tables, built by the adapter from `fastdds` constants. Returns `None`
    when reliability or durability cannot be resolved.
    """
    qos = getattr(sample, "qos", None)
    if qos is None:
        return None

    reliability: str | None = None
    durability: str | None = None
    history: str | None = None
    history_depth: int | None = None
    deadline_ns: int | None = None

    try:
        rel = getattr(qos, "reliability", None) or getattr(qos, "m_reliability", None)
        if rel is not None:
            kind = getattr(rel, "kind", None)
            if kind is not None:
                reliability = reliability_map.get(kind)

        dur = getattr(qos, "durability", None) or getattr(qos, "m_durability", None)
        if dur is not None:
            kind = getattr(dur, "kind", None)
            if kind is not None:
                durability = durability_map.get(kind)

        hist = getattr(qos, "history", None) or getattr(qos, "m_history", None)
        if hist is not None:
            kind = getattr(hist, "kind", None)
            if kind is not None:
                history = history_map.get(kind)
            depth = getattr(hist, "depth", None)
            if isinstance(depth, int):
                history_depth = depth

        ddl = getattr(qos, "deadline", None) or getattr(qos, "m_deadline", None)
        if ddl is not None:
            period = getattr(ddl, "period", None)
            if period is not None:
                sec = getattr(period, "seconds", None)
                if sec is None:
                    sec = getattr(period, "sec", None) or 0
                nsec = getattr(period, "nanosec", None)
                if nsec is None:
                    nsec = getattr(period, "nanoseconds", None) or 0
                if sec or nsec:
                    deadline_ns = int(sec) * 1_000_000_000 + int(nsec)
    except (TypeError, AttributeError):  # defensive
        return None

    if reliability is None or durability is None:
        return None

    return QosProfile(
        reliability=reliability,  # type: ignore[arg-type]
        durability=durability,  # type: ignore[arg-type]
        history=history,  # type: ignore[arg-type]
        history_depth=history_depth,
        deadline_ns=deadline_ns,
    )


HISTORY_NOT_ANNOUNCED_NOTE = (
    "History is not carried by DDS discovery (it is not part of the builtin endpoint "
    "data), so it is not reported for this endpoint."
)


def apply_history_policy(
    qos: QosProfile | None, *, vendor: str, is_observer: bool = False
) -> QosProfile | None:
    """Report History only where the observed value was really announced.

    The Cyclone binding fills every missing QoS with its own defaults, so a
    Cyclone peer showing KEEP_LAST depth 1 is indistinguishable from "not
    announced". Fast DDS and RTI do not send History at all, and an unknown
    vendor cannot be trusted. The observer's own endpoints are authoritative.
    Anything else becomes `history=None` plus an explanatory `history_note`.
    """
    if qos is None or is_observer:
        return qos
    announced = (
        vendor == "cyclone"
        and qos.history is not None
        and not (qos.history == "KEEP_LAST" and qos.history_depth in (None, 1))
    )
    if announced:
        return qos
    return qos.model_copy(
        update={
            "history": None,
            "history_depth": None,
            "history_note": HISTORY_NOT_ANNOUNCED_NOTE,
        }
    )


__all__ = [
    "CYCLONE_DURABILITY_NAMES",
    "CYCLONE_HISTORY_NAMES",
    "CYCLONE_RELIABILITY_NAMES",
    "HISTORY_NOT_ANNOUNCED_NOTE",
    "INFINITE_DURATION_NS",
    "apply_history_policy",
    "cyclone_qos_to_profile",
    "duration_state",
    "duration_to_ns",
    "fast_qos_to_profile",
]
