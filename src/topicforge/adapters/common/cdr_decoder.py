"""Vendor-agnostic CDR / dynamic-type payload decoder.

Extracted from `dds_cyclone/adapter.py` in v0.4.0 Phase 3 so the same
field-by-field decode logic powers both live DDS samples (via the
Cyclone XTypes pipeline from Phase 1.5) and recorded bag samples
(via the Phase 3 `services/bag_service.py`).

The 6 public helpers below operate on **Python value shapes** — they
do not import any DDS or ROS binding. The CycloneDdsAdapter wraps its
`cyclonedds.dynamic` typed-reader pipeline around them ; the bag
service wraps `rosbags.AnyReader` around them.

Public API:

* `iter_field_names(sample)` — list field names from a dynamic-type
  Python object (dataclass / Pydantic / slots / dict-attr).
* `decode_field_value(value)` — recursive decode of one field value.
* `decode_dynamic_sample(sample)` — full sample → payload dict with
  `_decode_status` annotation.
* `dynamic_type_name(type_object)` — best-effort message-type name.
* `extract_seq_from_payload(payload)` — pull `seq` / `sequence_number`
  / `sequence_id` from a decoded payload dict.
* `extract_publish_ns_from_payload(payload)` — pull `publish_ns` or
  `header.stamp.{sec,nanosec}` from a decoded payload dict.

Tests live in `tests/test_cdr_decoder.py` — pure logic, no DDS / bag
dependency required.
"""

from __future__ import annotations

from typing import Any

from topicforge.adapters.common.xtypes import (
    annotate_full,
    annotate_partial,
    annotate_raw,
)


def decode_dynamic_sample(sample: Any) -> dict[str, object]:
    """Decode `sample` field-by-field into a payload dict.

    Strategy:
      * Iterate the sample's declared fields (via `__dataclass_fields__`,
        `__fields__`, or `__slots__` — whichever the binding chose).
      * For each field, attempt `getattr` + recursive decode of nested
        structs / sequences / primitives.
      * Per-field exception → mark the field as undecoded ; surface the
        whole sample as `annotate_partial` with a comma-joined list of
        failed field names in `_decode_note`.
      * If no fields could be decoded → `annotate_raw` with the sample's
        repr captured in the note.
    """
    decoded: dict[str, object] = {}
    failed_fields: list[str] = []

    for field_name in iter_field_names(sample):
        try:
            value = getattr(sample, field_name)
            decoded[field_name] = decode_field_value(value)
        except Exception:  # pragma: no cover — per-field defense
            failed_fields.append(field_name)

    if not decoded:
        return annotate_raw(
            b"",
            note=(
                f"dynamic sample exposed no decodable fields ; sample type: {type(sample).__name__}"
            ),
        )
    if failed_fields:
        return annotate_partial(
            decoded,
            note=f"undecoded fields: {', '.join(failed_fields)}",
            raw_bytes=None,
        )
    return annotate_full(decoded)


def iter_field_names(sample: Any) -> list[str]:
    """Best-effort list of field names on a dynamic-type sample.

    Tries (in order) dataclass-style `__dataclass_fields__`, Pydantic-
    style `__fields__`, slot-based `__slots__`, then bare `__dict__`
    keys. Returns an empty list when none apply.
    """
    fields = getattr(sample, "__dataclass_fields__", None)
    if fields:
        return list(fields)
    fields = getattr(sample, "__fields__", None)
    if fields:
        return list(fields)
    slots = getattr(sample, "__slots__", None)
    if slots:
        return list(slots)
    return (
        [name for name in vars(sample) if not name.startswith("_")]
        if hasattr(sample, "__dict__")
        else []
    )


def decode_field_value(value: Any) -> object:
    """Recursive decode of a single dynamic-type field value.

    Primitives and strings pass through. Sequences (list / tuple) are
    decoded element-wise. Dicts are decoded recursively. Nested
    struct-like objects (exposing dataclass / Pydantic / slot fields)
    recurse via the same field-iteration logic. Unsupported types
    (bytes, custom classes that resist iteration) collapse to their
    `repr()` so the payload remains JSON-serializable.
    """
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [decode_field_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): decode_field_value(v) for k, v in value.items()}
    # Nested struct — recurse.
    if any(hasattr(value, attr) for attr in ("__dataclass_fields__", "__fields__", "__slots__")):
        nested: dict[str, object] = {}
        for field_name in iter_field_names(value):
            try:
                nested[field_name] = decode_field_value(getattr(value, field_name))
            except Exception:  # pragma: no cover
                nested[field_name] = f"<undecoded {type(value).__name__}.{field_name}>"
        return nested
    return repr(value)


def dynamic_type_name(type_object: Any) -> str:
    """Best-effort message-type name from a resolved TypeObject."""
    for attr in ("type_name", "name", "__name__"):
        v = getattr(type_object, attr, None)
        if isinstance(v, str) and v:
            return v
    return "dds/dynamic"


def extract_seq_from_payload(payload: dict[str, object]) -> int | None:
    """Best-effort sequence-number extraction from a decoded payload.

    Looks at common field names used by ROS / DDS message conventions :
    `seq`, `sequence_number`, `sequence_id`. The `header.seq` form
    common in ROS1 isn't recursed into here ; ROS2 messages typically
    flatten it. Returns `None` if no integer-valued sequence key
    is present.
    """
    for key in ("seq", "sequence_number", "sequence_id"):
        value = payload.get(key)
        if isinstance(value, int):
            return value
    return None


def extract_publish_ns_from_payload(payload: dict[str, object]) -> int | None:
    """Best-effort publish-timestamp extraction from a decoded payload.

    Looks for `publish_ns` directly, then for a ROS-style nested
    `header.stamp.{sec,nanosec}` shape that the decoded XTypes
    representation can expose. Returns `None` when neither is
    present.
    """
    direct = payload.get("publish_ns")
    if isinstance(direct, int):
        return direct
    header = payload.get("header")
    if isinstance(header, dict):
        stamp = header.get("stamp")
        if isinstance(stamp, dict):
            sec = stamp.get("sec")
            nsec = stamp.get("nanosec")
            if isinstance(sec, int) and isinstance(nsec, int):
                return sec * 1_000_000_000 + nsec
    return None


__all__ = [
    "decode_dynamic_sample",
    "decode_field_value",
    "dynamic_type_name",
    "extract_publish_ns_from_payload",
    "extract_seq_from_payload",
    "iter_field_names",
]
