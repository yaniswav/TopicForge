"""Tests for the binding-free DDS discovery-sample introspection helpers.

Extracted from the Cyclone and Fast adapters (Lot 0, audit 2026-07-08) so
the `getattr`-with-fallback field extraction is testable without the
`cyclonedds` / `fastdds` bindings. The helpers stay vendor-qualified
because the two vendors expose subtly different sample shapes; the tests
pin those documented differences (notably: Fast reads only `topic_name`
while Cyclone also falls back to `topic`).

Synthetic duck-typed objects only.
"""

from __future__ import annotations

import pytest

from topicforge.adapters.common.dds_introspection import (
    cyclone_extract_guid,
    cyclone_extract_hostname,
    cyclone_extract_topic_name,
    cyclone_extract_vendor_id,
    fast_extract_guid,
    fast_extract_hostname,
    fast_extract_topic_name,
    fast_extract_vendor_id,
    is_removal,
)


class _Obj:
    """Minimal attribute bag for building duck-typed discovery samples."""

    def __init__(self, **attrs: object) -> None:
        for key, value in attrs.items():
            setattr(self, key, value)


# ------------------------------- is_removal --------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (None, False),
        ("ALIVE", False),
        ("REMOVED_PARTICIPANT", True),
        ("participant DISPOSED", True),
        ("DROPPED", True),
        (0, False),
    ],
)
def test_is_removal(status: object, expected: bool):
    assert is_removal(status) is expected


def test_is_removal_enum_like_object():
    class _Status:
        def __str__(self) -> str:
            return "REMOVED_DURABLE_READER"

    assert is_removal(_Status()) is True


# ---------------------------- Cyclone extractors ---------------------------


def test_cyclone_extract_guid_bytes():
    assert cyclone_extract_guid(_Obj(key=b"\x01" * 16)) == b"\x01" * 16


def test_cyclone_extract_guid_inner_value():
    assert cyclone_extract_guid(_Obj(key=_Obj(value=b"\x02" * 16))) == b"\x02" * 16


def test_cyclone_extract_guid_missing_returns_none():
    assert cyclone_extract_guid(_Obj()) is None


def test_cyclone_extract_vendor_id_bytes():
    assert cyclone_extract_vendor_id(_Obj(vendor_id=b"\x01\x16")) == (1, 22)


def test_cyclone_extract_vendor_id_tuple():
    assert cyclone_extract_vendor_id(_Obj(vendor_id=(1, 5))) == (1, 5)


def test_cyclone_extract_vendor_id_missing_returns_none():
    assert cyclone_extract_vendor_id(_Obj()) is None


def test_cyclone_extract_hostname_str_and_bytes():
    assert cyclone_extract_hostname(_Obj(hostname="robot1")) == "robot1"
    assert cyclone_extract_hostname(_Obj(hostname=b"robot2")) == "robot2"


def test_cyclone_extract_hostname_missing_returns_none():
    assert cyclone_extract_hostname(_Obj()) is None


def test_cyclone_extract_topic_name_primary_and_fallback():
    assert cyclone_extract_topic_name(_Obj(topic_name="/scan")) == "/scan"
    # Cyclone falls back to `topic` when `topic_name` is absent.
    assert cyclone_extract_topic_name(_Obj(topic="/tf")) == "/tf"


def test_cyclone_extract_topic_name_missing_returns_none():
    assert cyclone_extract_topic_name(_Obj()) is None


# ------------------------------ Fast extractors ----------------------------


def test_fast_extract_guid_bytes():
    assert fast_extract_guid(_Obj(guid=b"\x03" * 16)) == b"\x03" * 16


def test_fast_extract_guid_from_int_sequence():
    assert fast_extract_guid(_Obj(guid=(1, 2, 3))) == bytes([1, 2, 3])


def test_fast_extract_guid_inner_data():
    assert fast_extract_guid(_Obj(key=_Obj(data=b"\x04" * 16))) == b"\x04" * 16


def test_fast_extract_guid_missing_returns_none():
    assert fast_extract_guid(_Obj()) is None


def test_fast_extract_vendor_id_from_nested_info():
    assert fast_extract_vendor_id(_Obj(info=_Obj(vendor_id=b"\x01\x05"))) == (1, 5)


def test_fast_extract_vendor_id_tuple():
    assert fast_extract_vendor_id(_Obj(vendor_id=(1, 22))) == (1, 22)


def test_fast_extract_vendor_id_missing_returns_none():
    assert fast_extract_vendor_id(_Obj()) is None


def test_fast_extract_hostname_reads_name_attr():
    # Fast checks `name` (Cyclone does not): pin the difference.
    assert fast_extract_hostname(_Obj(name="node_x")) == "node_x"


def test_fast_extract_topic_name_only_topic_name_attr():
    assert fast_extract_topic_name(_Obj(topic_name="/img")) == "/img"
    # Unlike Cyclone, Fast does NOT fall back to `topic`.
    assert fast_extract_topic_name(_Obj(topic="/img")) is None


def test_common_reexports_are_importable():
    # The adapters import these via the package `__init__`, not the submodule.
    from topicforge.adapters import common

    assert callable(common.cyclone_extract_guid)
    assert callable(common.fast_qos_to_profile)
    assert callable(common.is_removal)


# ------------------ defensive fallback branches (binding-shape variance) ----
# These pin the getattr-fallback paths that exist precisely to absorb
# cross-binding-version shape differences: the branches the audit flagged as
# the silent-failure risk if they ever regress.


def test_cyclone_extract_guid_skips_absent_attrs_then_finds_guid():
    # key / participant_key absent -> loop continues to the `guid` attr.
    assert cyclone_extract_guid(_Obj(guid=b"\x07" * 16)) == b"\x07" * 16


def test_cyclone_extract_guid_non_bytes_without_value_returns_none():
    assert cyclone_extract_guid(_Obj(key=12345)) is None


def test_cyclone_extract_vendor_id_from_vendor_attr():
    # Falls back from `vendor_id` to the `vendor` attribute.
    assert cyclone_extract_vendor_id(_Obj(vendor=b"\x01\x16")) == (1, 22)


def test_cyclone_extract_vendor_id_inner_vendorId_attr():
    assert cyclone_extract_vendor_id(_Obj(vendor_id=_Obj(vendorId=(1, 5)))) == (1, 5)


def test_cyclone_extract_hostname_from_user_data_bytes():
    assert cyclone_extract_hostname(_Obj(user_data=b"ud-host")) == "ud-host"


def test_fast_extract_guid_inner_value_bytes():
    assert fast_extract_guid(_Obj(key=_Obj(value=b"\x08" * 16))) == b"\x08" * 16


def test_fast_extract_guid_inner_guidprefix_sequence():
    assert fast_extract_guid(_Obj(guid=_Obj(guidPrefix=[1, 2, 3]))) == bytes([1, 2, 3])


def test_fast_extract_guid_non_coercible_sequence_returns_none():
    # A sequence of non-ints can't be coerced to bytes -> None, no raise.
    assert fast_extract_guid(_Obj(guid=["x", "y"])) is None


def test_fast_extract_vendor_id_inner_vendor_id_attr():
    assert fast_extract_vendor_id(_Obj(vendor_id=_Obj(vendor_id=(1, 5)))) == (1, 5)


def test_fast_extract_hostname_bytes():
    assert fast_extract_hostname(_Obj(hostname=b"fasthost")) == "fasthost"


def test_fast_extract_hostname_missing_returns_none():
    assert fast_extract_hostname(_Obj()) is None


def test_fast_extract_topic_name_missing_returns_none():
    assert fast_extract_topic_name(_Obj()) is None
