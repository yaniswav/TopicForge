"""Vendor-neutral DDS helpers — shared between Cyclone and Fast adapters.

Pure functions with no DDS dependency at module level. Maps OMG-RTPS
vendor IDs to TopicForge's canonical vendor tag, renders 16-byte GUIDs
in the canonical OMG textual form, and centralizes the DDS-only error
message used when a DDS-only adapter is asked for ROS2 introspection.

The vendor_id table comes from the official OMG Vendor IDs document
(`portals.omg.org/dds/sites/default/files/Vendor%20IDs.pdf`). When a new
vendor is observed in the wild, add a row here ; do NOT widen the
`ParticipantInfo.vendor` Literal without a CHANGELOG entry — it is a
soft-breaking wire change.
"""

from __future__ import annotations

from typing import Literal

from topicforge.adapters.base import AdapterError

_DDS_DOMAIN_MIN = 0
_DDS_DOMAIN_MAX = 232


def validate_domain_id(domain_id: int) -> None:
    """Raise `AdapterError` when `domain_id` is outside the DDS range 0..232.

    Shared by every DDS adapter constructor (Cyclone, Fast, OpenDDS, Dust) so
    the bound check — and its exact message — is defined once. (Lot 5.)
    """
    if domain_id < _DDS_DOMAIN_MIN or domain_id > _DDS_DOMAIN_MAX:
        raise AdapterError(
            f"domain_id must be in {_DDS_DOMAIN_MIN}..{_DDS_DOMAIN_MAX}, got {domain_id}"
        )


VendorTag = Literal["cyclone", "fast", "rti", "mock", "unknown"]
"""Canonical vendor tag exposed on `ParticipantInfo.vendor`.

Kept in sync with `models/schemas.py:ParticipantInfo.vendor` Literal. A
mismatch between the two would surface as a Pydantic ValidationError at
adapter output construction time — caught by the cross-vendor tests.
"""

# OMG vendor_id (2-byte octet array) → canonical tag.
# Source: portals.omg.org/dds/sites/default/files/Vendor%20IDs.pdf
# Entries collapse to "unknown" when there is no first-class TopicForge
# tag for them today ; observers still see those participants on the
# bus via the OMG protocol guarantee, they just report as "unknown".
_VENDOR_ID_MAP: dict[tuple[int, int], VendorTag] = {
    (0x01, 0x01): "rti",  # Real-Time Innovations Connext
    (0x01, 0x02): "unknown",  # PrismTech / OpenSplice (EOL)
    (0x01, 0x03): "unknown",  # OCI / OpenDDS
    (0x01, 0x04): "unknown",  # MilSoft Open DDS
    (0x01, 0x05): "fast",  # eProsima Fast DDS
    (0x01, 0x06): "unknown",  # GurumNetworks GurumDDS
    (0x01, 0x07): "unknown",  # Twin Oaks Computing CoreDX
    (0x01, 0x09): "unknown",  # ADLink / Vortex (pre-Cyclone)
    (0x01, 0x0A): "unknown",  # PrismTech Vortex Lite
    (0x01, 0x0B): "unknown",  # TechSoft InterCOM
    (0x01, 0x0C): "unknown",  # Kongsberg Defence & Aerospace
    (0x01, 0x0F): "unknown",  # ZRDDS
    (0x01, 0x10): "unknown",  # GurumNetworks GurumDDS-Light
    (0x01, 0x11): "unknown",  # Dust DDS (Rust)
    (0x01, 0x16): "cyclone",  # Eclipse CycloneDDS
}


def canonicalize_vendor_id(raw: tuple[int, int] | bytes | None) -> VendorTag:
    """Map a 2-byte OMG vendor_id to the TopicForge canonical tag.

    Accepts the tuple form `(byte0, byte1)` or a `bytes` of length 2.
    Returns `"unknown"` for any input not in the lookup table — never
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
      * `None` → `"unknown"`

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
    "This adapter serves DDS observability only — it cannot run the ROS2 "
    "graph tools (list_topics, get_topic_info, sample_messages, analyze_bag, "
    "peek_bag_samples). To get both surfaces in one process: install ROS2 "
    "and source the workspace so `ros2` is on PATH, then re-run with "
    "TOPICFORGE_MODE=live — the v0.4.0 CompositeAdapter routes ROS2 tools to "
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
