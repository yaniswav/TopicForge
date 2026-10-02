"""Build `EndpointInfo` records from vendor-native samples, then scan them.

Fast DDS entry point of `detect_qos_mismatches`. Its listener keeps raw
discovery objects whose layout has not been checked on a real bus, so
records are built with the vendor's `qos_to_profile` / `extract_*` callables
and carry no participant names. Cyclone goes through
`common.endpoints.endpoint_infos_from_samples`. Both end in
`qos_scan.scan_endpoints`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from topicforge.adapters.common.dds_helpers import format_guid
from topicforge.adapters.common.qos_normalize import apply_history_policy
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
        # Fast DDS discovery carries no History; the vendor callable may fill a default.
        qos=apply_history_policy(qos_to_profile(sample), vendor="fast"),
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
    """Scan vendor-native subscription and publication samples.

    Endpoints with no resolvable topic name are dropped; those whose QoS
    cannot be normalized are counted in a hint and not paired.
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
