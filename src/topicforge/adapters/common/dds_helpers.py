"""Vendor-neutral DDS helpers: shared between Cyclone and Fast adapters.

Pure functions with no DDS dependency at module level. Maps OMG-RTPS
vendor IDs to TopicForge's canonical vendor tag, renders 16-byte GUIDs
in the canonical OMG textual form, and centralizes the DDS-only error
message used when a DDS-only adapter is asked for ROS2 introspection.

The vendor_id table comes from the official OMG Vendor IDs document
(`omgwiki.org/dds/sites/default/files/Vendor IDs.pdf`). When a new
vendor is observed in the wild, add a row here ; do NOT widen the
`ParticipantInfo.vendor` Literal without a CHANGELOG entry: it is a
soft-breaking wire change.
"""

from __future__ import annotations

from collections.abc import Iterable
from itertools import islice
from typing import Any, Literal

from topicforge.adapters.base import AdapterError
from topicforge.adapters.common.xtypes import annotate_raw
from topicforge.models import MessageSample

_DDS_DOMAIN_MIN = 0
_DDS_DOMAIN_MAX = 232


def validate_domain_id(domain_id: int) -> None:
    """Raise `AdapterError` when `domain_id` is outside the DDS range 0..232.

    Shared by every DDS adapter constructor (Cyclone, Fast, OpenDDS, Dust) so
    the bound check (and its exact message) is defined once. (Lot 5.)
    """
    if domain_id < _DDS_DOMAIN_MIN or domain_id > _DDS_DOMAIN_MAX:
        raise AdapterError(
            f"domain_id must be in {_DDS_DOMAIN_MIN}..{_DDS_DOMAIN_MAX}, got {domain_id}"
        )


def take_bounded(source: Iterable[Any], limit: int) -> list[Any]:
    """Consume at most `limit` items from `source` and return them as a list.

    Wraps `itertools.islice` so a lazy binding iterator is never fully
    materialized before truncation. This matters for `read_iter(timeout=...)`
    on a DataReader: its timeout resets every time a sample arrives, so on a
    topic publishing faster than the timeout the iterator never ends and
    `list(read_iter(...))[:n]` would never return. A negative `limit` yields
    an empty list.
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


def user_topic_placeholder(topic: str, count: int, *, note: str) -> list[MessageSample]:
    """Return the single annotated placeholder for an undecodable user topic.

    Empty when `count <= 0`, otherwise exactly one `MessageSample` whose
    payload is `annotate_raw(b"", note=note)`. The placeholder is not a
    received sample: callers must not record it into a `MetricsBuffer`.
    """
    if count <= 0:
        return []
    return [
        MessageSample(
            topic=topic,
            message_type="dds/unknown",
            timestamp_ns=0,
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

Kept in sync with `models/schemas.py:ParticipantInfo.vendor` and
`ParticipantEvent.vendor` Literals. A mismatch between them would surface as
a Pydantic ValidationError at adapter output construction time: pinned by
`tests/test_dds_helpers.py`.
"""

# OMG vendor_id (2-byte octet array) -> canonical tag.
# Source: the official OMG RTPS vendor ID list (omgwiki.org/dds, "Vendor IDs",
# mirrored at dds-foundation.org/dds-rtps-vendor-and-product-ids), cross-checked
# against vendor sources: Fast DDS `VendorId_t.hpp` (eProsima = {0x01, 0x0F}),
# Cyclone `ddsi__vendor.h` (ECLIPSE 0x10, EPROSIMA 0x0f, ADLINK_OSPL 0x02,
# RTI 0x01) and Dust DDS `types.rs` (S2E = [0x01, 0x14]).
# Only vendors with a first-class TopicForge tag are listed. Every other id
# (01.04 MilSoft, 01.07 / 01.08 Lakota and ICOUP, 01.09 ETRI Diamond, 01.0B
# Vortex Cafe, 01.0C PrismTech, 01.0D Vortex Lite, 01.0E Qeo, 01.11 Gurum,
# 01.12 RustDDS, 01.13 ZRDDS, 01.15 eProsima Safe DDS, 01.16 Federated Designs
# and above) falls through to "unknown": observers still see those
# participants via RTPS discovery, they just report as "unknown". Safe DDS is
# deliberately not folded into "fast": it is a separate safety-certified
# implementation, not Fast DDS.
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
    """Map a 2-byte OMG vendor_id to the TopicForge canonical tag.

    Accepts the tuple form `(byte0, byte1)` or a `bytes` of length 2.
    Returns `"unknown"` for any input not in the lookup table: never
    raises. `None` (no vendor_id observed) collapses to `"unknown"`.
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

    Accepts:
      * `bytes` of length 16 (the canonical RTPS binary form)
      * a tuple of 16 ints (each `0..255`)
      * an already-formatted `str` (returned lowercased)
      * `None` -> `"unknown"`

    Never raises ; truncates or zero-pads inputs that are shorter than
    16 bytes so a live-discovery edge case (binding returns a partial
    GUID) still produces something a downstream LLM can parse rather
    than crashing the tool call.
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
    "TOPICFORGE_MODE=live: the v0.4.0 CompositeAdapter routes ROS2 tools to "
    "the CLI and DDS tools to your TOPICFORGE_DDS_BACKEND automatically. "
    "For offline development use TOPICFORGE_MODE=mock."
)
"""Standard message raised by DDS adapters when asked for ROS2 introspection.

Both `CycloneDdsAdapter` and `FastDdsAdapter` raise
`AdapterError(DDS_ONLY_ERROR_MSG)` on the 5 ROS2 methods of the
`MiddlewareAdapter` protocol. The single message keeps the user-facing
remediation text consistent across vendors. Lists every tool affected so
the LLM caller can suggest exactly the right next action.

Test contract: must contain the substrings `"DDS observability only"`,
`"TOPICFORGE_DDS_BACKEND"`, `"TOPICFORGE_MODE"` (pinned by
`tests/test_dds_helpers.py` and the cross-vendor / cyclone / fast adapter
test suites).
"""
