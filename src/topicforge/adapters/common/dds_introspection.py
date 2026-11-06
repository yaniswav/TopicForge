"""Defensive field extraction from DDS discovery samples, without the bindings.

Kept here so the `getattr`-with-fallback logic is unit-testable without
`cyclonedds` installed. The helpers are vendor-qualified (`cyclone_*`)
so another binding can add its own without merging sample shapes.

Every helper returns `None` or a safe default rather than raising, so one
odd sample cannot fail a tool call.
"""

from __future__ import annotations

import uuid
from typing import Any

# Cyclone variants


def cyclone_extract_guid(sample: Any) -> bytes | None:
    """Pull the 16-byte GUID off a Cyclone discovery sample, if present.

    cyclonedds 11.0.1 exposes the builtin-topic key as a `uuid.UUID`
    (observed on a live bus), so `.bytes` is checked alongside raw bytes and
    `.value` wrappers.
    """
    for attr in ("key", "participant_key", "guid"):
        v = getattr(sample, attr, None)
        if v is None:
            continue
        if isinstance(v, bytes):
            return v
        if isinstance(v, uuid.UUID):
            return v.bytes
        inner = getattr(v, "value", None)
        if isinstance(inner, bytes):
            return inner
    return None


def cyclone_extract_vendor_id(sample: Any) -> tuple[int, int] | None:
    """Pull the 2-byte OMG vendor_id off a Cyclone discovery sample, if present."""
    v = getattr(sample, "vendor_id", None)
    if v is None:
        v = getattr(sample, "vendor", None)
    if v is None:
        return vendor_id_from_guid(cyclone_extract_guid(sample))
    if isinstance(v, bytes) and len(v) >= 2:
        return (v[0], v[1])
    inner = getattr(v, "vendorId", None)
    if isinstance(inner, (bytes, tuple, list)) and len(inner) >= 2:
        return (inner[0], inner[1])
    if isinstance(v, (tuple, list)) and len(v) >= 2:
        return (v[0], v[1])
    return vendor_id_from_guid(cyclone_extract_guid(sample))


def vendor_id_from_guid(guid: bytes | None) -> tuple[int, int] | None:
    """Read the vendor id from the first two bytes of an RTPS GUID prefix.

    The cyclonedds builtin participant sample has no vendor field (key, qos
    and sample_info only, observed with 11.0.1). RTPS section 9.3.1.5
    recommends starting the GUID prefix with the vendor id; eProsima (01.0F)
    and Eclipse (01.10) do. Dust DDS and RTI Connext do not, so they map to
    "unknown" unless their first two bytes collide with an assigned id.
    """
    if guid is None or len(guid) < 2:
        return None
    return (guid[0], guid[1])


# DDS InstanceStateKind bit values (DDS 1.4 section 2.2.2.5.1.9): ALIVE = 16,
# NOT_ALIVE_DISPOSED = 32, NOT_ALIVE_NO_WRITERS = 64.
_INSTANCE_STATE_ALIVE = 16


def is_alive_sample(sample: Any) -> bool:
    """True unless the sample's SampleInfo says the instance is gone.

    Builtin discovery readers keep the last sample of a participant or
    endpoint after it leaves; a lease expiry or dispose only flips
    `instance_state` to NOT_ALIVE. Without this check a stopped participant
    never disappears and a dead writer still yields QoS mismatches (observed
    on a live bus). Samples without a SampleInfo count as alive.
    """
    info = getattr(sample, "sample_info", None)
    if info is None:
        return True
    if getattr(info, "valid_data", True) is False:
        return False
    state = getattr(info, "instance_state", None)
    if state is None:
        return True
    return int(state) == _INSTANCE_STATE_ALIVE


def _iter_qos(sample: Any) -> list[Any]:
    """Return the policies in `sample.qos` as a list; empty on any oddity."""
    try:
        return list(getattr(sample, "qos", None) or ())
    except Exception:
        return []


def _type_leaf(obj: Any) -> str:
    """Last component of an object's (possibly scoped) class name."""
    return type(obj).__name__.rsplit(".", 1)[-1]


def _text_attr(sample: Any, attrs: tuple[str, ...]) -> str | None:
    """First non-empty str / bytes attribute among `attrs`, decoded as text."""
    for attr in attrs:
        v = getattr(sample, attr, None)
        if isinstance(v, (bytes, bytearray)):
            decoded = bytes(v).decode("utf-8", errors="replace")
            if decoded:
                return decoded
        if isinstance(v, str) and v:
            return v
    return None


def cyclone_extract_participant_name(sample: Any) -> str | None:
    """Pull the EntityName QoS (the announced participant name) off a sample.

    cyclonedds 11.0.1 exposes it as `Policy.EntityName(name=...)` among the
    items of `sample.qos`, present only when the remote application set it.
    Never raises.
    """
    try:
        for policy in _iter_qos(sample):
            if _type_leaf(policy) == "EntityName":
                name = getattr(policy, "name", None)
                if isinstance(name, str) and name:
                    return name
        return _text_attr(sample, ("participant_name",))
    except Exception:
        return None


def cyclone_extract_hostname(sample: Any) -> str | None:
    """Pull the announced hostname off a Cyclone sample, if exposed.

    Reads the `__Hostname` `Property` of `sample.qos` (observed live with
    cyclonedds 11.0.1), then falls back to plain sample attributes. Never
    raises.
    """
    try:
        for policy in _iter_qos(sample):
            if _type_leaf(policy) == "Property" and getattr(policy, "key", None) == "__Hostname":
                value = getattr(policy, "value", None)
                if isinstance(value, str) and value:
                    return value
        return _text_attr(sample, ("hostname", "participant_name", "user_data"))
    except Exception:
        return None


def cyclone_extract_pid(sample: Any) -> int | None:
    """Pid announced in the `__Pid` `Property` of a Cyclone participant sample, else `None`."""
    try:
        for policy in _iter_qos(sample):
            if _type_leaf(policy) == "Property" and getattr(policy, "key", None) == "__Pid":
                value = str(getattr(policy, "value", "")).strip()
                return int(value) if value.isdigit() else None
    except Exception:
        return None
    return None


def cyclone_extract_topic_name(sample: Any) -> str | None:
    """Pull the topic name off a Cyclone endpoint sample (`topic_name` then `topic`)."""
    v = getattr(sample, "topic_name", None)
    if v is None:
        v = getattr(sample, "topic", None)
    if isinstance(v, str) and v:
        return v
    return None


def cyclone_extract_type_name(sample: Any) -> str | None:
    """Pull the data type name off a Cyclone endpoint sample, if it carries one.

    Two endpoints on the same topic only match when their type names agree,
    so the type name is what tells "incompatible QoS" apart from "never
    meant to match".
    """
    v = getattr(sample, "type_name", None)
    if isinstance(v, str) and v:
        return v
    return None


__all__ = [
    "cyclone_extract_guid",
    "cyclone_extract_hostname",
    "cyclone_extract_participant_name",
    "cyclone_extract_topic_name",
    "cyclone_extract_vendor_id",
]
