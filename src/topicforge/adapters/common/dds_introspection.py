"""Defensive field extraction from DDS discovery samples — binding-free.

Extracted from the Cyclone and Fast adapters (Lot 0, audit 2026-07-08) so
the `getattr`-with-fallback sample introspection is unit-testable without
the `cyclonedds` / `fastdds` bindings installed.

The two vendors expose subtly different discovery-sample shapes, so the
helpers stay **vendor-qualified** (`cyclone_*` / `fast_*`) and preserve each
adapter's exact behavior byte-for-byte — unifying them into a single set is
deliberately deferred to the Lot 5 adapter-dedup work, which the real-bus
integration rig can verify. Merging untested extraction paths blind (no
bindings here) is exactly the silent-regression risk the audit flagged.

Every helper returns `None` / safe defaults rather than raising — a single
odd discovery sample must never break a whole tool call.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Cyclone variants
# ---------------------------------------------------------------------------


def cyclone_extract_guid(sample: Any) -> bytes | None:
    """Pull the 16-byte GUID off a Cyclone discovery sample, if present."""
    for attr in ("key", "participant_key", "guid"):
        v = getattr(sample, attr, None)
        if v is None:
            continue
        if isinstance(v, bytes):
            return v
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
        return None
    if isinstance(v, bytes) and len(v) >= 2:
        return (v[0], v[1])
    inner = getattr(v, "vendorId", None)
    if isinstance(inner, (bytes, tuple, list)) and len(inner) >= 2:
        return (inner[0], inner[1])
    if isinstance(v, (tuple, list)) and len(v) >= 2:
        return (v[0], v[1])
    return None


def cyclone_extract_hostname(sample: Any) -> str | None:
    """Pull a hostname / participant-name hint off a Cyclone sample, if exposed."""
    for attr in ("hostname", "participant_name", "user_data"):
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


def cyclone_extract_topic_name(sample: Any) -> str | None:
    """Pull the topic name off a Cyclone endpoint sample (`topic_name` then `topic`)."""
    v = getattr(sample, "topic_name", None)
    if v is None:
        v = getattr(sample, "topic", None)
    if isinstance(v, str) and v:
        return v
    return None


# ---------------------------------------------------------------------------
# Fast DDS variants
# ---------------------------------------------------------------------------


def is_removal(status: Any) -> bool:
    """Detect a 'participant/endpoint removed' discovery status across
    binding versions. Fast DDS exposes status as either an enum value
    or a string label — accept both.
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
    "cyclone_extract_topic_name",
    "cyclone_extract_vendor_id",
    "fast_extract_guid",
    "fast_extract_hostname",
    "fast_extract_topic_name",
    "fast_extract_vendor_id",
    "is_removal",
]
