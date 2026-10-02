"""Payload shape for decoded or raw DDS user-topic samples.

Owning the shape here keeps the Cyclone and Fast adapters' output
identical. Three builders extend `MessageSample.payload`:

* `annotate_full(fields)`: every field decoded, `_decode_status="full"`.
* `annotate_partial(fields, note, raw_bytes)`: some fields decoded,
  `_decode_status="partial"`, raw bytes kept as hex.
* `annotate_raw(raw_bytes, note)`: nothing decoded, `_decode_status="raw"`.

Reserved payload keys are `_decode_status`, `_decode_note` and
`_raw_bytes_hex`; user field names must not collide. The leading
underscore matches the `_raw_text` key of the ROS2 CLI path.
"""

from __future__ import annotations

from typing import Literal

DecodeStatus = Literal["full", "partial", "raw"]
"""Tag for `MessageSample.payload['_decode_status']`.

* `full`: every field of the discovered type was decoded.
* `partial`: some fields decoded; sub-structures the binding cannot
  resolve (unions, recursive types, optionals) are omitted and
  `_raw_bytes_hex` carries the original bytes.
* `raw`: nothing decoded (no dynamic XTypes support, TypeObject
  resolution failed). `_decode_note` says why.
"""

_RAW_BYTES_PREVIEW_LIMIT = 4096
"""Cap on `_raw_bytes_hex` length in hex chars. Longer payloads are truncated
and flagged with `_raw_bytes_truncated=True`."""


def annotate_full(fields: dict[str, object]) -> dict[str, object]:
    """Build a payload with every IDL field decoded.

    Fields are merged at the top level, with no wrapping `data` key.
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
    """Build a payload with some fields decoded and the rest kept as hex.

    `note` says what the binding could not decode. It is shown to the LLM,
    so keep it short.
    """
    payload: dict[str, object] = dict(fields)
    payload["_decode_status"] = "partial"
    payload["_decode_note"] = note
    if raw_bytes is not None:
        payload.update(_encode_raw_bytes(raw_bytes))
    return payload


def annotate_raw(raw_bytes: bytes, *, note: str) -> dict[str, object]:
    """Build a payload with nothing decoded: the bytes are kept as hex.

    The shape is the same whether the binding lacks dynamic XTypes or the
    decode call raised; `_decode_note` carries the reason.
    """
    payload: dict[str, object] = {
        "_decode_status": "raw",
        "_decode_note": note,
    }
    payload.update(_encode_raw_bytes(raw_bytes))
    return payload


def _encode_raw_bytes(raw_bytes: bytes) -> dict[str, object]:
    """Hex-encode `raw_bytes`, truncating to the preview limit.

    Slices before encoding so a large payload never allocates its full hex.
    """
    truncated = len(raw_bytes) * 2 > _RAW_BYTES_PREVIEW_LIMIT
    hex_str = raw_bytes[: _RAW_BYTES_PREVIEW_LIMIT // 2].hex() if truncated else raw_bytes.hex()
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
