"""Build `EndpointInfo` records from vendor-native samples, then scan them.

Fast DDS entry point of `detect_qos_mismatches`: its listener keeps raw
discovery objects whose layout has never been read on a real bus, so this
builds records with the vendor's own `qos_to_profile` / `extract_*` callables
and best-effort fields (no participant names). The Cyclone adapter goes
through `common.endpoints.endpoint_infos_from_samples` instead. Both end in
the same pure `qos_scan.scan_endpoints`, unit-testable with synthetic samples.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from topicforge.adapters.common.dds_helpers import format_guid
from topicforge.adapters.common.qos_scan import scan_endpoints
from topicforge.models import EndpointInfo, MismatchScan, QosProfile


def _info(
    sample: Any,
    role: Literal["writer", "reader"],
    *,
    qos_to_profile: Callable[[Any], QosProfile | None],
    extract_topic_name: Callable[[Any], str | None],
    extract_guid: Callable[[Any], bytes | None],
    mode_effective: Literal["mock", "live"],
) -> EndpointInfo | None:
    topic = extract_topic_name(sample)
    if topic is None:
        return None
    return EndpointInfo(
        guid=format_guid(extract_guid(sample)),
        role=role,
        participant_guid="unknown",
        topic=topic,
        qos=qos_to_profile(sample),
        is_observer=False,
        domain_id=0,
        mode_effective=mode_effective,
    )


def detect_mismatches_across_endpoints(
    *,
    subs: Any,
    pubs: Any,
    topic: str | None,
    qos_to_profile: Callable[[Any], QosProfile | None],
    extract_topic_name: Callable[[Any], str | None],
    extract_guid: Callable[[Any], bytes | None],
    mode_effective: Literal["mock", "live"] = "live",
) -> MismatchScan:
    """Scan vendor-native subscription / publication samples into a `MismatchScan`.

    Endpoints whose topic name cannot be resolved are dropped ; those whose
    QoS cannot be normalized are counted in a hint and not paired.
    """
    kwargs: dict[str, Any] = {
        "qos_to_profile": qos_to_profile,
        "extract_topic_name": extract_topic_name,
        "extract_guid": extract_guid,
        "mode_effective": mode_effective,
    }
    infos = [_info(s, "reader", **kwargs) for s in subs]
    infos += [_info(s, "writer", **kwargs) for s in pubs]
    return scan_endpoints(
        [i for i in infos if i is not None], topic=topic, mode_effective=mode_effective
    )


__all__ = ["detect_mismatches_across_endpoints"]
