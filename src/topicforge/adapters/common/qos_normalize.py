"""Vendor QoS → canonical `QosProfile` normalization — binding-free, testable.

Extracted from `dds_cyclone/adapter.py` and `dds_fast/adapter.py`
(Lot 0, audit 2026-07-08) so the QoS normalization that feeds
`detect_qos_mismatches` — the flagship DDS diagnostic — is unit-testable
**without** the `cyclonedds` / `fastdds` bindings installed. Previously
these functions lived below a top-level `import fastdds` / `from cyclonedds
...` in their adapters, so the entire QoS normalization path (and the bug
class where a renamed policy key silently returns `None` → no mismatch ever
reported) was unreachable by the test suite.

Both adapters import these and alias them back to their original
`_cyclone_qos_to_profile` / `_fast_qos_to_profile` names, so their call
sites are unchanged.

The Cyclone path keys policies by their binding class name (pure string
constants below). The Fast path keys by integer enum value, and those
integers come from the `fastdds` module — so `fast_qos_to_profile` takes
the three int→str maps as parameters (the adapter builds them from
`fastdds` and passes them in), keeping this module free of any binding
import.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from topicforge.models import QosProfile

# CycloneDDS exposes QoS policies as instances of nested classes under
# `cyclonedds.qos.Policy.*` — we read them by simple class name to stay
# binding-version-agnostic.
CYCLONE_RELIABILITY_NAMES: dict[str, str] = {"Reliable": "RELIABLE", "BestEffort": "BEST_EFFORT"}
CYCLONE_DURABILITY_NAMES: dict[str, str] = {
    "Volatile": "VOLATILE",
    "TransientLocal": "TRANSIENT_LOCAL",
    "Transient": "TRANSIENT",
    "Persistent": "PERSISTENT",
}
CYCLONE_HISTORY_NAMES: dict[str, str] = {"KeepLast": "KEEP_LAST", "KeepAll": "KEEP_ALL"}


def cyclone_qos_to_profile(sample: Any) -> QosProfile | None:
    """Map a Cyclone discovery sample's QoS into the canonical QosProfile.

    Returns `None` when essential QoS policies (reliability, durability,
    history) are missing — the analyzer needs all three present to
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
            cls_name = type(policy).__name__
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
                d = getattr(policy, "duration", None)
                if d is None:
                    d = getattr(policy, "deadline", None)
                if hasattr(d, "to_nanoseconds"):
                    deadline_ns = int(d.to_nanoseconds())
                elif isinstance(d, int):
                    deadline_ns = d
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
    integer-enum → canonical-string tables. The adapter builds them from
    `fastdds` constants and passes them in, so this function stays free of
    any binding import and is testable with synthetic maps.

    Returns `None` when reliability, durability, or history cannot be
    resolved — the analyzer needs all three.
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
    "cyclone_qos_to_profile",
    "fast_qos_to_profile",
]
