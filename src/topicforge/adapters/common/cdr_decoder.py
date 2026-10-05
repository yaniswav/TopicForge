"""Vendor-agnostic CDR / dynamic-type payload decoder.

Shared by live DDS samples (Cyclone dynamic types) and recorded bag samples
(`services/bag_service.py`). The helpers operate on plain Python value
shapes and import no DDS or ROS binding.
"""

from __future__ import annotations

from typing import Any

from topicforge.adapters.common.xtypes import (
    annotate_full,
    annotate_partial,
    annotate_raw,
)


def decode_dynamic_sample(
    sample: Any, *, max_array_elements: int | None = None
) -> dict[str, object]:
    """Decode `sample` field by field into a payload dict.

    With `max_array_elements`, a numpy array longer than that is read only up
    to one element past the cap, so the caller can detect and report the cut
    without converting the whole array.

    A field that raises is skipped and the result is `annotate_partial`
    with the failed names in `_decode_note`. If nothing decodes, the result
    is `annotate_raw`.
    """
    decoded: dict[str, object] = {}
    failed_fields: list[str] = []

    for field_name in iter_field_names(sample):
        try:
            value = getattr(sample, field_name)
            decoded[field_name] = decode_field_value(value, max_array_elements=max_array_elements)
        except Exception:  # pragma: no cover: per-field defense
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
    """List field names on a dynamic-type sample.

    Tries `__dataclass_fields__`, `__fields__`, `__slots__`, then public
    `__dict__` keys. Returns an empty list when none apply. Dunder names
    such as the `__msgtype__` marker that `rosbags` messages carry are
    not data and are left out.
    """
    return [name for name in _raw_field_names(sample) if not _is_dunder(name)]


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def _raw_field_names(sample: Any) -> list[str]:
    fields = getattr(sample, "__dataclass_fields__", None)
    if fields:
        return list(fields)
    fields = getattr(sample, "__fields__", None)
    if fields:
        return list(fields)
    slots = getattr(sample, "__slots__", None)
    if slots:
        # `__slots__` may be a bare string; list() would split it into characters.
        return [slots] if isinstance(slots, str) else list(slots)
    return (
        [name for name in vars(sample) if not name.startswith("_")]
        if hasattr(sample, "__dict__")
        else []
    )


_MAX_DECODE_DEPTH = 32
"""Recursion cap for `decode_field_value`. Deeper values collapse to `repr()`
so a self-referential object graph cannot raise `RecursionError`."""


def decode_field_value(
    value: Any, *, max_array_elements: int | None = None, _depth: int = 0
) -> object:
    """Decode one dynamic-type field value into JSON-serializable data.

    Primitives pass through; lists, tuples, numpy arrays, dicts and struct-like objects
    recurse; anything else (bytes, opaque classes) becomes its `repr()`.
    `max_array_elements` is described on `decode_dynamic_sample`. `_depth` is internal.
    """
    if _depth >= _MAX_DECODE_DEPTH:
        return repr(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [
            decode_field_value(v, max_array_elements=max_array_elements, _depth=_depth + 1)
            for v in value
        ]
    # Array-valued fields from `rosbags` are numpy arrays; `tolist` gives plain Python values.
    if callable(getattr(value, "tolist", None)) and hasattr(value, "dtype"):
        if max_array_elements is not None and getattr(value, "ndim", 1) == 1:
            value = value[: max_array_elements + 1]
        return decode_field_value(
            value.tolist(), max_array_elements=max_array_elements, _depth=_depth + 1
        )
    if isinstance(value, dict):
        return {
            str(k): decode_field_value(v, max_array_elements=max_array_elements, _depth=_depth + 1)
            for k, v in value.items()
        }
    # Nested struct: recurse.
    if any(hasattr(value, attr) for attr in ("__dataclass_fields__", "__fields__", "__slots__")):
        nested: dict[str, object] = {}
        for field_name in iter_field_names(value):
            try:
                nested[field_name] = decode_field_value(
                    getattr(value, field_name),
                    max_array_elements=max_array_elements,
                    _depth=_depth + 1,
                )
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
    """Return the integer `seq`, `sequence_number` or `sequence_id`, else `None`.

    `header.seq` (ROS1 style) is not searched.
    """
    for key in ("seq", "sequence_number", "sequence_id"):
        value = payload.get(key)
        if isinstance(value, int):
            return value
    return None


def extract_publish_ns_from_payload(payload: dict[str, object]) -> int | None:
    """Return `publish_ns`, or `header.stamp` converted to ns, else `None`."""
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
