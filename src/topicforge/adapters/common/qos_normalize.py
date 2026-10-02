"""Vendor QoS -> canonical `QosProfile` normalization: binding-free, testable.

Extracted from `dds_cyclone/adapter.py` and `dds_fast/adapter.py`
(Lot 0, audit 2026-07-08) so the QoS normalization that feeds
`detect_qos_mismatches` (the flagship DDS diagnostic) is unit-testable
**without** the `cyclonedds` / `fastdds` bindings installed. Previously
these functions lived below a top-level `import fastdds` / `from cyclonedds
...` in their adapters, so the entire QoS normalization path (and the bug
class where a renamed policy key silently returns `None` -> no mismatch ever
reported) was unreachable by the test suite.

Both adapters import these and alias them back to their original
`_cyclone_qos_to_profile` / `_fast_qos_to_profile` names, so their call
sites are unchanged.

The Cyclone path keys policies by their binding class name (pure string
constants below). The Fast path keys by integer enum value, and those
integers come from the `fastdds` module: so `fast_qos_to_profile` takes
the three int->str maps as parameters (the adapter builds them from
`fastdds` and passes them in), keeping this module free of any binding
import.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from topicforge.models import QosProfile

# CycloneDDS exposes QoS policies as instances of nested classes under
# `cyclonedds.qos.Policy.*`: we read them by simple class name to stay
# binding-version-agnostic.
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


def duration_to_ns(value: Any) -> int | None:
    """A binding duration as nanoseconds; `None` when infinite or unreadable.

    `None` is the canonical "infinite / not set" on every duration field of
    `QosProfile`, so the infinite sentinel must never leak through as a
    9.2e18 deadline (it would also read as a finite reader request in the
    mismatch analyzer).
    """
    if hasattr(value, "to_nanoseconds"):
        value = value.to_nanoseconds()
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0 or value >= INFINITE_DURATION_NS:
        return None
    return int(value)


def _first_attr(policy: Any, names: tuple[str, ...]) -> Any:
    """First non-None attribute of `policy` among `names`."""
    for name in names:
        value = getattr(policy, name, None)
        if value is not None:
            return value
    return None


def _data_representation(policy: Any) -> list[str] | None:
    """`XCDR1` / `XCDR2` flags of a DataRepresentation policy, `None` if neither is set."""
    found = []
    if getattr(policy, "use_cdrv0_representation", False):
        found.append("XCDR1")
    if getattr(policy, "use_xcdrv2_representation", False):
        found.append("XCDR2")
    return found or None


def _extended_policies(qos: Any) -> dict[str, Any]:
    """The liveliness / ownership / partition / latency / ordering / representation fields.

    Reads by the last component of the scoped class name, like the core
    policies. Absent policies leave their key out, which `QosProfile`
    defaults to `None`.
    """
    out: dict[str, Any] = {}
    for policy in qos:
        cls_name = type(policy).__name__.rsplit(".", 1)[-1]
        if cls_name in CYCLONE_LIVELINESS_NAMES:
            out["liveliness_kind"] = CYCLONE_LIVELINESS_NAMES[cls_name]
            out["liveliness_lease_ns"] = duration_to_ns(
                _first_attr(policy, ("lease_duration", "duration"))
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
            out["latency_budget_ns"] = duration_to_ns(_first_attr(policy, ("budget", "duration")))
        elif cls_name in CYCLONE_DESTINATION_ORDER_NAMES:
            out["destination_order"] = CYCLONE_DESTINATION_ORDER_NAMES[cls_name]
        elif cls_name == "DataRepresentation":
            out["data_representation"] = _data_representation(policy)
    # DDS semantics: no Partition policy (or an empty list) is the default partition "".
    out["partitions"] = out.get("partitions") or [""]
    return out


def cyclone_qos_to_profile(sample: Any) -> QosProfile | None:
    """Map a Cyclone discovery sample's QoS into the canonical QosProfile.

    Returns `None` when essential QoS policies (reliability, durability,
    history) are missing: the analyzer needs all three present to
    produce a meaningful pair report.
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
        for policy in qos:
            # cyclonedds 11.0.1 names policy classes with their scope,
            # e.g. "Reliability.BestEffort" (observed on a live bus). Matching
            # the bare "BestEffort" against that full name never succeeded, so
            # no profile was ever built and no mismatch was ever reported.
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
                deadline_ns = duration_to_ns(_first_attr(policy, ("duration", "deadline")))
        extended = _extended_policies(qos)
    except (TypeError, AttributeError):  # defensive against odd qos shapes
        return None

    if reliability is None or durability is None or history is None:
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
    """Map a Fast DDS discovery sample's QoS into the canonical QosProfile.

    `reliability_map` / `durability_map` / `history_map` are the binding's
    integer-enum -> canonical-string tables. The adapter builds them from
    `fastdds` constants and passes them in, so this function stays free of
    any binding import and is testable with synthetic maps.

    Returns `None` when reliability, durability, or history cannot be
    resolved: the analyzer needs all three.
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

    if reliability is None or durability is None or history is None:
        return None

    return QosProfile(
        reliability=reliability,  # type: ignore[arg-type]
        durability=durability,  # type: ignore[arg-type]
        history=history,  # type: ignore[arg-type]
        history_depth=history_depth,
        deadline_ns=deadline_ns,
    )


__all__ = [
    "CYCLONE_DURABILITY_NAMES",
    "CYCLONE_HISTORY_NAMES",
    "CYCLONE_RELIABILITY_NAMES",
    "INFINITE_DURATION_NS",
    "cyclone_qos_to_profile",
    "duration_to_ns",
    "fast_qos_to_profile",
]
