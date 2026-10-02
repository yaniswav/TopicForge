"""Defensive field extraction from DDS discovery samples: binding-free.

Extracted from the Cyclone and Fast adapters (Lot 0, audit 2026-07-08) so
the `getattr`-with-fallback sample introspection is unit-testable without
the `cyclonedds` / `fastdds` bindings installed.

The two vendors expose subtly different discovery-sample shapes, so the
helpers stay **vendor-qualified** (`cyclone_*` / `fast_*`) and preserve each
adapter's exact behavior byte-for-byte: unifying them into a single set is
deliberately deferred to the Lot 5 adapter-dedup work, which the real-bus
integration rig can verify. Merging untested extraction paths blind (no
bindings here) is exactly the silent-regression risk the audit flagged.

Every helper returns `None` / safe defaults rather than raising: a single
odd discovery sample must never break a whole tool call.
"""

from __future__ import annotations

import uuid
from typing import Any

# ---------------------------------------------------------------------------
# Cyclone variants
# ---------------------------------------------------------------------------


def cyclone_extract_guid(sample: Any) -> bytes | None:
    """Pull the 16-byte GUID off a Cyclone discovery sample, if present.

    cyclonedds 11.0.1 exposes the builtin-topic key as a `uuid.UUID`
    (observed on a live bus), so `.bytes` is checked alongside raw bytes and
    `.value` wrappers. Before this was handled every participant came back
    without a GUID and they all collapsed onto one "unknown" entry.
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

    The cyclonedds builtin participant sample carries no vendor field (key,
    qos and sample_info only, observed with 11.0.1). RTPS section 9.3.1.5
    recommends that implementations start the GUID prefix with their vendor
    id, and some do (eProsima 01.0F, Eclipse 01.10). Others do not: Dust DDS
    and RTI Connext fill the prefix differently, so they map to "unknown"
    rather than to a wrong vendor, as long as their first two bytes do not
    collide with an assigned id. This is a convention, not a guarantee.
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
    endpoint after it leaves: its lease expiring, or an explicit dispose,
    only flips `instance_state` to a NOT_ALIVE value. Treating those cached
    samples as live meant a stopped participant never disappeared and a
    dead writer still produced QoS mismatch reports (observed on a live
    bus). Samples without a SampleInfo are kept, so duck-typed test doubles
    and bindings that do not expose one keep their previous behaviour.
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


# ---------------------------------------------------------------------------
# Fast DDS variants
# ---------------------------------------------------------------------------


def is_removal(status: Any) -> bool:
    """Detect a 'participant/endpoint removed' discovery status across
    binding versions. Fast DDS exposes status as either an enum value
    or a string label: accept both.
    """
    if status is None:
        return False
    s = str(status).upper()
    return "REMOVED" in s or "DISPOSED" in s or "DROPPED" in s


def fast_extract_guid(sample: Any) -> bytes | None:
    """Pull a 16-byte GUID off a Fast DDS discovery sample."""
    for attr in ("guid", "key", "participant_key"):
        v = getattr(sample, attr, None)
        if v is None:
            continue
        if isinstance(v, bytes):
            return v
        for inner_attr in ("value", "data", "guidPrefix"):
            inner = getattr(v, inner_attr, None)
            if isinstance(inner, bytes):
                return inner
            if isinstance(inner, (tuple, list)) and inner:
                try:
                    return bytes(int(b) & 0xFF for b in inner)
                except (TypeError, ValueError):
                    continue
        if isinstance(v, (tuple, list)) and v:
            try:
                return bytes(int(b) & 0xFF for b in v)
            except (TypeError, ValueError):
                continue
    return None


def fast_extract_vendor_id(sample: Any) -> tuple[int, int] | None:
    """Pull the 2-byte OMG vendor_id off a Fast DDS discovery sample."""
    v = getattr(sample, "vendor_id", None)
    if v is None:
        info = getattr(sample, "info", None)
        if info is not None:
            v = getattr(info, "vendor_id", None)
    if v is None:
        return None
    if isinstance(v, bytes) and len(v) >= 2:
        return (v[0], v[1])
    if isinstance(v, (tuple, list)) and len(v) >= 2:
        try:
            return (int(v[0]), int(v[1]))
        except (TypeError, ValueError):
            return None
    inner = getattr(v, "vendor_id", None)
    if isinstance(inner, (bytes, tuple, list)) and len(inner) >= 2:
        try:
            return (int(inner[0]), int(inner[1]))
        except (TypeError, ValueError):
            return None
    return None


def fast_extract_hostname(sample: Any) -> str | None:
    """Pull a hostname / participant-name hint off a Fast DDS sample, if exposed."""
    for attr in ("hostname", "participant_name", "name", "user_data"):
        v = getattr(sample, attr, None)
        if isinstance(v, (bytes, bytearray)):
            try:
                decoded = v.decode("utf-8", errors="replace")
            except (UnicodeError, AttributeError):
                continue
            if decoded:
                return decoded
        if isinstance(v, str) and v:
            return v
    return None


def fast_extract_topic_name(sample: Any) -> str | None:
    """Pull the topic name off a Fast DDS endpoint sample (`topic_name` only)."""
    v = getattr(sample, "topic_name", None)
    if isinstance(v, str) and v:
        return v
    return None


__all__ = [
    "cyclone_extract_guid",
    "cyclone_extract_hostname",
    "cyclone_extract_participant_name",
    "cyclone_extract_topic_name",
    "cyclone_extract_vendor_id",
    "fast_extract_guid",
    "fast_extract_hostname",
    "fast_extract_topic_name",
    "fast_extract_vendor_id",
    "is_removal",
]
