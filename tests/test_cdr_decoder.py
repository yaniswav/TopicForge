"""Unit tests for `topicforge.adapters.common.cdr_decoder`.

Pure-Python tests of the 6 helpers extracted from `dds_cyclone/adapter.py`
in v0.4.0 Phase 3. No DDS dependency. The Phase 1.5 XTypes Cyclone tests
(gated by `requires_cyclonedds`) continue to exercise the same logic
through their full pipeline — these tests pin the extracted contract
in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from topicforge.adapters.common.cdr_decoder import (
    decode_dynamic_sample,
    decode_field_value,
    dynamic_type_name,
    extract_publish_ns_from_payload,
    extract_seq_from_payload,
    iter_field_names,
)

# ---------------------------------------------------------------------------
# iter_field_names
# ---------------------------------------------------------------------------


def test_iter_field_names_dataclass() -> None:
    @dataclass
    class Sample:
        a: int
        b: str

    names = iter_field_names(Sample(a=1, b="x"))
    assert set(names) == {"a", "b"}


def test_iter_field_names_pydantic_style() -> None:
    from typing import ClassVar

    class _FakePydantic:
        __fields__: ClassVar[dict[str, str]] = {"foo": "...", "bar": "..."}

        def __init__(self) -> None:
            self.foo = 1
            self.bar = 2

    names = iter_field_names(_FakePydantic())
    assert set(names) == {"foo", "bar"}


def test_iter_field_names_slots() -> None:
    class _Slotted:
        __slots__ = ("x", "y")

        def __init__(self) -> None:
            self.x = 1
            self.y = 2

    names = iter_field_names(_Slotted())
    assert set(names) == {"x", "y"}


def test_iter_field_names_plain_object_filters_dunders() -> None:
    class _Bare:
        def __init__(self) -> None:
            self.public = 1
            self._private = 2

    names = iter_field_names(_Bare())
    assert "public" in names
    assert "_private" not in names


def test_iter_field_names_unknown_shape_returns_empty() -> None:
    assert iter_field_names(42) == []


# ---------------------------------------------------------------------------
# decode_field_value
# ---------------------------------------------------------------------------


def test_decode_field_value_primitives_pass_through() -> None:
    for v in ("hello", 42, 3.14, True, False, None):
        assert decode_field_value(v) == v


def test_decode_field_value_sequence_elementwise() -> None:
    assert decode_field_value([1, 2, 3]) == [1, 2, 3]
    assert decode_field_value((1.0, 2.0)) == [1.0, 2.0]


def test_decode_field_value_dict_recurses_string_keys() -> None:
    out = decode_field_value({1: "one", "two": 2})
    assert out == {"1": "one", "two": 2}


def test_decode_field_value_nested_dataclass_struct() -> None:
    @dataclass
    class Inner:
        x: int

    @dataclass
    class Outer:
        inner: Inner
        name: str

    result = decode_field_value(Outer(inner=Inner(x=7), name="topic"))
    assert result == {"inner": {"x": 7}, "name": "topic"}


def test_decode_field_value_unsupported_falls_back_to_repr() -> None:
    class _Opaque:
        def __repr__(self) -> str:
            return "<opaque>"

    assert decode_field_value(_Opaque()) == "<opaque>"


# ---------------------------------------------------------------------------
# decode_dynamic_sample
# ---------------------------------------------------------------------------


def test_decode_dynamic_sample_full_path() -> None:
    @dataclass
    class Beat:
        seq: int
        status: str

    payload = decode_dynamic_sample(Beat(seq=42, status="ok"))
    assert payload["seq"] == 42
    assert payload["status"] == "ok"
    assert payload["_decode_status"] == "full"


def test_decode_dynamic_sample_partial_path() -> None:
    """A field that raises on getattr triggers the partial-decode branch."""

    class _PartiallyBroken:
        __slots__ = ("bad", "good")

        def __init__(self) -> None:
            self.good = 1
            # bad is left unset → AttributeError on getattr

    payload = decode_dynamic_sample(_PartiallyBroken())
    assert payload["_decode_status"] == "partial"
    assert payload["good"] == 1
    assert "bad" in payload["_decode_note"]


def test_decode_dynamic_sample_raw_path_when_no_fields() -> None:
    payload = decode_dynamic_sample(object())
    assert payload["_decode_status"] == "raw"
    assert "_decode_note" in payload


# ---------------------------------------------------------------------------
# dynamic_type_name
# ---------------------------------------------------------------------------


def test_dynamic_type_name_prefers_type_name() -> None:
    class _T:
        type_name = "pkg/msg/Foo"

    assert dynamic_type_name(_T()) == "pkg/msg/Foo"


def test_dynamic_type_name_falls_back_to_name() -> None:
    class _T:
        name = "FallbackName"

    assert dynamic_type_name(_T()) == "FallbackName"


def test_dynamic_type_name_default() -> None:
    assert dynamic_type_name(object()) == "dds/dynamic"


# ---------------------------------------------------------------------------
# extract_seq_from_payload + extract_publish_ns_from_payload
# ---------------------------------------------------------------------------


def test_extract_seq_from_payload_direct_keys() -> None:
    assert extract_seq_from_payload({"seq": 7}) == 7
    assert extract_seq_from_payload({"sequence_number": 12}) == 12
    assert extract_seq_from_payload({"sequence_id": 99}) == 99


def test_extract_seq_from_payload_none_when_absent() -> None:
    assert extract_seq_from_payload({"other": 1}) is None


def test_extract_seq_from_payload_ignores_non_int() -> None:
    assert extract_seq_from_payload({"seq": "42"}) is None


def test_extract_publish_ns_direct() -> None:
    assert extract_publish_ns_from_payload({"publish_ns": 1_000_000}) == 1_000_000


def test_extract_publish_ns_header_stamp() -> None:
    payload: dict[str, Any] = {"header": {"stamp": {"sec": 1, "nanosec": 500_000_000}}}
    assert extract_publish_ns_from_payload(payload) == 1_500_000_000


def test_extract_publish_ns_none_when_unavailable() -> None:
    assert extract_publish_ns_from_payload({}) is None
    assert extract_publish_ns_from_payload({"header": {"frame_id": "x"}}) is None
