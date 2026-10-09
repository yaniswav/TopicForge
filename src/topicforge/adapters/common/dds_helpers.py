"""Vendor-neutral DDS helpers shared by the Cyclone and Fast adapters.

Pure functions with no DDS dependency at import time: the OMG vendor-id to
vendor-tag map, GUID formatting, user-topic placeholders and the DDS-only
error message. Widening the `ParticipantInfo.vendor` Literal changes the
wire contract, so it needs a CHANGELOG entry.
"""

from __future__ import annotations

from collections.abc import Iterable
from itertools import islice
from typing import Any, Literal

from topicforge.adapters.base import AdapterError
from topicforge.adapters.common.topic_filter import no_match_note, resolve_topic_filter
from topicforge.adapters.common.xtypes import annotate_raw
from topicforge.models import MessageSample, SampleResult

_DDS_DOMAIN_MIN = 0
_DDS_DOMAIN_MAX = 232


def validate_domain_id(domain_id: int) -> None:
    """Raise `AdapterError` when `domain_id` is outside the DDS range 0..232."""
    if domain_id < _DDS_DOMAIN_MIN or domain_id > _DDS_DOMAIN_MAX:
        raise AdapterError(
            f"domain_id must be in {_DDS_DOMAIN_MIN}..{_DDS_DOMAIN_MAX}, got {domain_id}"
        )


def take_bounded(source: Iterable[Any], limit: int) -> list[Any]:
    """Consume at most `limit` items from `source`; a negative `limit` yields `[]`.

    `read_iter(timeout=...)` resets its timeout on every sample, so on a
    fast topic the iterator never ends and `list(...)[:n]` would hang.
    """
    return list(islice(source, max(limit, 0)))


DYNAMIC_DECODE_DISABLED_NOTE = (
    "dynamic XTypes decode is disabled in this release pending real-bus "
    "validation; topic presence is reported, payload is not decoded"
)
"""`_decode_note` carried by the user-topic placeholder sample.

The placeholder says the topic is on the bus. It says nothing about traffic:
no sample was received, so it must never feed the metrics buffer.
"""


BUILTIN_DCPS_TOPICS = frozenset({"DCPSParticipant", "DCPSSubscription", "DCPSPublication"})
"""The builtin discovery topics: the only DDS topics whose samples are readable."""

USER_TOPIC_NOTE = (
    "payload decoding is disabled for DDS user topics, so no samples are "
    "returned; this does not mean the topic is silent. Use list_endpoints for "
    "the topic's presence, writers, readers and QoS"
)
"""`SampleResult.note` of a user-topic peek."""


def user_topic_result(
    topic: str,
    mode_effective: Literal["mock", "live"],
    resolution_note: str | None = None,
) -> SampleResult:
    """Honest result of peeking a user topic: no samples, and a note saying why.

    `resolution_note` says which discovered name the requested one matched.
    """
    note = USER_TOPIC_NOTE if resolution_note is None else f"{resolution_note}. {USER_TOPIC_NOTE}"
    return SampleResult(topic=topic, count=0, samples=[], mode_effective=mode_effective, note=note)


def resolve_user_topic(
    topic: str, known: Iterable[str | None], domain_id: int
) -> tuple[str, str | None]:
    """The discovered DDS topic a requested name selects, and a note when it was not exact.

    Accepts `/scan`, `scan` and `rt/scan` interchangeably (ROS 2 name
    mangling). Raises `AdapterError`, listing the closest discovered topics,
    when no endpoint matches.
    """
    names = {name for name in known if name}
    resolved, note = resolve_topic_filter(topic, names)
    if resolved is None:
        raise AdapterError(
            f"DDS topic {topic!r} not discovered on domain {domain_id}. "
            f"{no_match_note(topic, names)}. Confirm a publisher is alive and reachable, or "
            "call list_participants / detect_qos_mismatches first to inspect current bus state."
        )
    return resolved, note


def metrics_status(
    topic: str, samples_observed: int
) -> Literal["ok", "no_samples_yet", "unsupported_user_topic"]:
    """Status of a `TopicMetrics`: user topics are unsupported, builtin ones are data or not yet."""
    if topic not in BUILTIN_DCPS_TOPICS:
        return "unsupported_user_topic"
    return "ok" if samples_observed > 0 else "no_samples_yet"


def declared_hz_from_endpoints(endpoints: Iterable[Any], topic: str) -> float | None:
    """`1 / deadline` of the shortest finite writer Deadline on `topic`, else `None`.

    Declared by the application in discovery, not measured. Readers are
    ignored: their Deadline is a requirement, not a promise.
    """
    periods = [
        e.qos.deadline_ns
        for e in endpoints
        if e.role == "writer"
        and e.dds_topic == topic
        and e.qos is not None
        and e.qos.deadline_ns
        and e.qos.deadline_ns > 0
    ]
    return 1e9 / min(periods) if periods else None


def user_topic_placeholder(topic: str, count: int, *, note: str) -> list[MessageSample]:
    """Return one `annotate_raw(b"", note=note)` sample, or `[]` when `count <= 0`.

    The placeholder is not a received sample: never record it into a
    `MetricsBuffer`.
    """
    if count <= 0:
        return []
    return [
        MessageSample(
            topic=topic,
            message_type="dds/unknown",
            timestamp_ns=0,
            stamp_source="none",
            payload=annotate_raw(b"", note=note),
        )
    ]


VendorTag = Literal[
    "cyclone",
    "fast",
    "rti",
    "rti_micro",
    "opensplice",
    "opendds",
    "coredx",
    "intercom",
    "dust",
    "mock",
    "unknown",
]
"""Canonical vendor tag exposed on `ParticipantInfo.vendor`.

Must match the `vendor` Literals in `models/schemas.py`
(`tests/test_dds_helpers.py` pins this).
"""

# OMG vendor_id (2-byte octet array) -> canonical tag.
# Source: the OMG RTPS vendor ID list (dds-foundation.org/dds-rtps-vendor-and-product-ids),
# cross-checked against Fast DDS `VendorId_t.hpp`, Cyclone `ddsi__vendor.h`
# and Dust DDS `types.rs`.
# Only vendors with their own TopicForge tag are listed; every other id
# (MilSoft, Lakota, ETRI Diamond, Vortex, Qeo, Gurum, RustDDS, ZRDDS,
# eProsima Safe DDS, ...) maps to "unknown" but is still observed through
# RTPS discovery. Safe DDS is not folded into "fast": it is a separate
# safety-certified implementation.
_VENDOR_ID_MAP: dict[tuple[int, int], VendorTag] = {
    (0x01, 0x01): "rti",  # Real-Time Innovations, RTI Connext DDS
    (0x01, 0x02): "opensplice",  # ADLink, OpenSplice DDS
    (0x01, 0x03): "opendds",  # Object Computing Inc. (OCI), OpenDDS
    (0x01, 0x05): "intercom",  # Kongsberg, InterCOM DDS
    (0x01, 0x06): "coredx",  # Twin Oaks Computing, CoreDX DDS
    (0x01, 0x0A): "rti_micro",  # Real-Time Innovations, RTI Connext DDS Micro
    (0x01, 0x0F): "fast",  # eProsima, FastRTPS / Fast DDS
    (0x01, 0x10): "cyclone",  # Eclipse Foundation, Eclipse Cyclone DDS
    (0x01, 0x14): "dust",  # S2E Software Systems, Dust DDS
}


def canonicalize_vendor_id(raw: tuple[int, int] | bytes | None) -> VendorTag:
    """Map a 2-byte OMG vendor_id, as a tuple or bytes, to a vendor tag.

    Never raises: `None`, short input and unlisted ids give `"unknown"`.
    """
    if raw is None:
        return "unknown"
    if isinstance(raw, bytes):
        if len(raw) < 2:
            return "unknown"
        key: tuple[int, int] = (raw[0], raw[1])
    else:
        if len(raw) < 2:
            return "unknown"
        key = (raw[0], raw[1])
    return _VENDOR_ID_MAP.get(key, "unknown")


def format_guid(raw: bytes | tuple[int, ...] | str | None) -> str:
    """Render a 16-byte OMG GUID in `xxxxxxxx.xxxxxxxx.xxxxxxxx.xxxxxxxx` form.

    Accepts 16 bytes, a tuple of 16 ints, or an already formatted string
    (lowercased); `None` gives `"unknown"`. Never raises: input shorter
    than 16 bytes is zero-padded, longer input truncated.
    """
    if raw is None:
        return "unknown"
    if isinstance(raw, str):
        return raw.lower()
    if isinstance(raw, bytes):
        data = raw[:16].ljust(16, b"\x00")
        groups = [data[i : i + 4].hex() for i in range(0, 16, 4)]
        return ".".join(groups)
    ints = list(raw)[:16]
    while len(ints) < 16:
        ints.append(0)
    hex_chars = "".join(f"{b & 0xFF:02x}" for b in ints)
    return ".".join(hex_chars[i : i + 8] for i in range(0, 32, 8))


DDS_ONLY_ERROR_MSG = (
    "This adapter serves DDS observability only: it cannot run the ROS2 "
    "graph tools (list_topics, get_topic_info, sample_messages, analyze_bag, "
    "peek_bag_samples). To get both surfaces in one process: install ROS2 "
    "and source the workspace so `ros2` is on PATH, then re-run with "
    "TOPICFORGE_MODE=live: the composite adapter routes ROS2 tools to "
    "the CLI and DDS tools to your TOPICFORGE_DDS_BACKEND automatically. "
    "For offline development use TOPICFORGE_MODE=mock. On a DDS-only setup "
    "use list_endpoints for topics and wiring, and peek_dds_samples on "
    "DCPSPublication / DCPSSubscription for the raw discovery records."
)
"""Raised by the DDS adapters for the ROS2 methods they do not serve.

Tests pin the substrings `"DDS observability only"`,
`"TOPICFORGE_DDS_BACKEND"` and `"TOPICFORGE_MODE"`.
"""
