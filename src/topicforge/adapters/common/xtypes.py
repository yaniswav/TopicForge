"""XTypes/IDL decode annotations — adapter-agnostic helpers.

`peek_dds_samples` on **user-defined topics** (added in v0.4.0 Phase 1)
returns decoded payloads when the active DDS backend can resolve the
TypeObject via the OMG XTypes builtin discovery topic ; falls back to
an annotated raw-bytes representation otherwise. This module owns the
payload shape so Cyclone and Fast DDS adapters produce identical wire
output regardless of the underlying binding's capabilities.

The shape extends — never replaces — the existing `MessageSample.payload`
dict (which is `dict[str, object]` on the model side, so additions are
safe). Three convenience builders:

* `annotate_full(fields)` — every IDL field decoded ; `_decode_status="full"`.
* `annotate_partial(fields, note, raw_bytes)` — some fields decoded,
  some omitted ; `_decode_status="partial"`. Preserves raw bytes hex so
  a client can re-decode against a richer schema.
* `annotate_raw(raw_bytes, note)` — no decode at all ; `_decode_status="raw"`.

Reserved payload keys: `_decode_status`, `_decode_note`, `_raw_bytes_hex`.
User-topic field names must not collide. The reserved-key prefix is a
single leading underscore — matches the existing `_raw_text` convention
used by `sample_messages` on the ROS2 CLI live path.
"""

from __future__ import annotations

from typing import Literal

DecodeStatus = Literal["full", "partial", "raw"]
"""Tag for `MessageSample.payload['_decode_status']`.

* `full` — every field of the discovered IDL/XTypes type was decoded.
* `partial` — some fields decoded ; opaque sub-structures (unions,
  recursive types, optional fields the binding cannot resolve) are
  omitted and `_raw_bytes_hex` carries the original bytes for client
  re-decoding.
* `raw` — no decode at all (binding does not support dynamic XTypes,
  TypeObject resolution failed, etc.). Use `_decode_note` to explain.
"""

_RAW_BYTES_PREVIEW_LIMIT = 4096
"""Cap on `_raw_bytes_hex` length (in hex chars). Above this, the hex
is truncated and a `_raw_bytes_truncated=True` flag is set so a client
knows what it sees is not the full payload. Mirrors the bounded-output
discipline of `MAX_SAMPLE_COUNT` on `sample_messages` — tool payloads
must stay tractable for an LLM context window.
"""


def annotate_full(fields: dict[str, object]) -> dict[str, object]:
    """Build a payload with every IDL field decoded.

    The decoded fields are merged at the top level (no nesting under a
    `data` key) so an LLM reading the payload sees the message shape
    directly — same convention as the existing mock DDS fixtures.
    """
    payload: dict[str, object] = dict(fields)
    payload["_decode_status"] = "full"
    return payload


def annotate_partial(
    fields: dict[str, object],
    *,
    note: str,
    raw_bytes: bytes | None = None,
) -> dict[str, object]:
    """Build a payload with some fields decoded and the rest preserved
    as raw bytes hex.

    `note` should explain *what* the binding could not decode (e.g.
    `"union 'mode' field skipped (cyclonedds.dynamic does not yet "
    `support unions)"`). Keep it short — this is an LLM-facing hint.
    """
    payload: dict[str, object] = dict(fields)
    payload["_decode_status"] = "partial"
    payload["_decode_note"] = note
    if raw_bytes is not None:
        payload.update(_encode_raw_bytes(raw_bytes))
    return payload


def annotate_raw(raw_bytes: bytes, *, note: str) -> dict[str, object]:
    """Build a payload with no decode — bytes preserved as hex.

    Used when (a) the binding does not expose dynamic XTypes for this
    type, or (b) TypeObject resolution returned but the decode call
    raised. The wire shape is identical regardless of which failure
    path led here ; `_decode_note` carries the diagnostic.
    """
    payload: dict[str, object] = {
        "_decode_status": "raw",
        "_decode_note": note,
    }
    payload.update(_encode_raw_bytes(raw_bytes))
    return payload


def _encode_raw_bytes(raw_bytes: bytes) -> dict[str, object]:
    """Encode bytes as hex with bounded length + truncation flag."""
    hex_str = raw_bytes.hex()
    truncated = len(hex_str) > _RAW_BYTES_PREVIEW_LIMIT
    if truncated:
        hex_str = hex_str[:_RAW_BYTES_PREVIEW_LIMIT]
    out: dict[str, object] = {"_raw_bytes_hex": hex_str}
    if truncated:
        out["_raw_bytes_truncated"] = True
    return out


__all__ = [
    "DecodeStatus",
    "annotate_full",
    "annotate_partial",
    "annotate_raw",
]
