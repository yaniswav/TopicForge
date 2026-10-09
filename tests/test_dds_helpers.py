"""Unit tests for `adapters/common/dds_helpers`.

Pure tests: no DDS middleware needed, no monkeypatching, no fixtures.
"""

from __future__ import annotations

from typing import get_args

import pytest

from topicforge.adapters.base import AdapterError
from topicforge.adapters.common import (
    DDS_ONLY_ERROR_MSG,
    VendorTag,
    canonicalize_vendor_id,
    format_guid,
    resolve_user_topic,
    validate_domain_id,
)
from topicforge.adapters.common.dds_helpers import _VENDOR_ID_MAP


def test_validate_domain_id_accepts_range_bounds() -> None:
    validate_domain_id(0)
    validate_domain_id(232)  # no raise


def test_validate_domain_id_rejects_out_of_range() -> None:
    with pytest.raises(AdapterError, match="domain_id"):
        validate_domain_id(-1)
    with pytest.raises(AdapterError, match="domain_id"):
        validate_domain_id(233)


# ---------------------------------------------------------------------------
# canonicalize_vendor_id: OMG vendor_id mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "tag"),
    [
        ((0x01, 0x01), "rti"),  # RTI Connext
        ((0x01, 0x02), "opensplice"),  # ADLink OpenSplice
        ((0x01, 0x03), "opendds"),  # OCI OpenDDS
        ((0x01, 0x05), "intercom"),  # Kongsberg InterCOM
        ((0x01, 0x06), "coredx"),  # Twin Oaks CoreDX
        ((0x01, 0x0A), "rti_micro"),  # RTI Connext Micro
        ((0x01, 0x0F), "fast"),  # eProsima Fast DDS
        ((0x01, 0x10), "cyclone"),  # Eclipse Cyclone DDS
        ((0x01, 0x14), "dust"),  # S2E Dust DDS
    ],
)
def test_vendor_id_maps_to_tag(raw: tuple[int, int], tag: str) -> None:
    """One row per mapped vendor, from the official OMG RTPS vendor id list."""
    assert canonicalize_vendor_id(raw) == tag


def test_vendor_id_map_covers_exactly_the_documented_vendors() -> None:
    """The parametrized table above is the whole map: a new row needs a new test."""
    assert len(_VENDOR_ID_MAP) == 9


@pytest.mark.parametrize(
    "raw",
    [
        (0x01, 0x04),  # MilSoft Mil-DDS
        (0x01, 0x09),  # ETRI Diamond DDS
        (0x01, 0x11),  # GurumDDS
        (0x01, 0x12),  # Atostek RustDDS
        (0x01, 0x13),  # ZRDDS
        (0x01, 0x15),  # eProsima Safe DDS: a separate product, not Fast DDS
        (0x01, 0x16),  # Federated Designs: once wrongly mapped to Cyclone
        (0x99, 0x99),
    ],
)
def test_unmapped_vendor_id_collapses_to_unknown(raw: tuple[int, int]) -> None:
    """Any vendor without a first-class tag falls back to 'unknown': never raises."""
    assert canonicalize_vendor_id(raw) == "unknown"


def test_vendor_id_accepts_bytes() -> None:
    assert canonicalize_vendor_id(b"\x01\x10") == "cyclone"


def test_vendor_id_short_bytes_collapses_to_unknown() -> None:
    """A truncated bytes input never raises."""
    assert canonicalize_vendor_id(b"\x01") == "unknown"


def test_vendor_id_short_tuple_collapses_to_unknown() -> None:
    assert canonicalize_vendor_id((0x01,)) == "unknown"  # type: ignore[arg-type]


def test_vendor_id_none_collapses_to_unknown() -> None:
    assert canonicalize_vendor_id(None) == "unknown"


# ---------------------------------------------------------------------------
# format_guid: OMG GUID rendering
# ---------------------------------------------------------------------------


def test_format_guid_full_bytes() -> None:
    raw = bytes(range(16))
    expected = "00010203.04050607.08090a0b.0c0d0e0f"
    assert format_guid(raw) == expected


def test_format_guid_accepts_tuple_of_ints() -> None:
    raw = tuple(range(16))
    expected = "00010203.04050607.08090a0b.0c0d0e0f"
    assert format_guid(raw) == expected


def test_format_guid_already_string_returns_lowercased() -> None:
    assert format_guid("ABCDEF12.34567890.0AAAAAAA.BBBBBBBB") == (
        "abcdef12.34567890.0aaaaaaa.bbbbbbbb"
    )


def test_format_guid_none_returns_unknown() -> None:
    assert format_guid(None) == "unknown"


def test_format_guid_short_bytes_zero_padded() -> None:
    """A 4-byte input pads with zeros instead of raising: defensive against
    edge-case bindings that return partial GUIDs."""
    assert format_guid(b"\x01\x02\x03\x04") == ("01020304.00000000.00000000.00000000")


def test_format_guid_truncates_long_bytes() -> None:
    """A 20-byte input drops the extra 4 bytes silently."""
    raw = bytes(range(20))
    expected = "00010203.04050607.08090a0b.0c0d0e0f"
    assert format_guid(raw) == expected


# ---------------------------------------------------------------------------
# DDS_ONLY_ERROR_MSG: remediation contract
# ---------------------------------------------------------------------------


def test_dds_only_error_msg_mentions_remediation() -> None:
    """The DDS-only error message must point at the standard remediation
    path so an LLM client can take action without re-reading docs."""
    assert "TOPICFORGE_DDS_BACKEND" in DDS_ONLY_ERROR_MSG
    assert "TOPICFORGE_MODE" in DDS_ONLY_ERROR_MSG


def test_dds_only_error_msg_preserves_substring_match_token() -> None:
    """`tests/test_cyclone_adapter.py` and friends use `match="DDS observability only"`
    via `pytest.raises`. The substring must survive any future re-wording so those
    test files don't quietly regress."""
    assert "DDS observability only" in DDS_ONLY_ERROR_MSG


def test_dds_only_error_msg_mentions_composite_remediation() -> None:
    """The message names the composite adapter as the remedy."""
    assert "composite adapter" in DDS_ONLY_ERROR_MSG


def test_dds_only_error_msg_lists_affected_tools() -> None:
    """The message names every ROS2-side tool a DDS-only adapter cannot serve,
    so the LLM caller does not need to introspect the protocol to know what is
    blocked."""
    for tool in ("list_topics", "get_topic_info", "sample_messages", "analyze_bag"):
        assert tool in DDS_ONLY_ERROR_MSG, f"missing tool name in error message: {tool!r}"


def test_every_canonical_vendor_tag_is_valid_participant_literal() -> None:
    """Every tag in `_VENDOR_ID_MAP` is accepted by the `ParticipantInfo.vendor` Literal.

    A mapped tag missing from the Literal would raise a ValidationError when
    the adapter builds its output.
    """
    from topicforge.models import ParticipantEvent, ParticipantInfo

    tags = set(_VENDOR_ID_MAP.values()) | set(get_args(VendorTag))
    for tag in tags:
        info = ParticipantInfo(guid="g", vendor=tag, domain_id=0)
        assert info.vendor == tag
        event = ParticipantEvent(
            guid="g",
            event_type="discovered",
            vendor=tag,
            timestamp_ns=0,
            domain_id=0,
        )
        assert event.vendor == tag


def test_vendor_tag_matches_schema_literals() -> None:
    """`VendorTag` (adapters) and the schema `vendor` Literals (models) must
    hold the same values, or adapter output fails Pydantic validation."""
    from topicforge.models import ParticipantEvent, ParticipantInfo

    expected = set(get_args(VendorTag))
    for model in (ParticipantInfo, ParticipantEvent):
        annotation = model.model_fields["vendor"].annotation
        assert annotation is not None
        assert set(get_args(annotation)) == expected, model.__name__
    assert set(_VENDOR_ID_MAP.values()) <= expected


# ---- peek_dds_samples name resolution ----------------------------------------


def test_resolve_user_topic_accepts_ros_names() -> None:
    known = {"rt/scan", "rt/odom", None}
    assert resolve_user_topic("rt/scan", known, 0) == ("rt/scan", None)
    resolved, note = resolve_user_topic("/scan", known, 0)
    assert resolved == "rt/scan"
    assert note is not None and "'/scan'" in note and "'rt/scan'" in note
    assert resolve_user_topic("scan", known, 0)[0] == "rt/scan"


def test_resolve_user_topic_unknown_lists_the_closest_topics() -> None:
    with pytest.raises(AdapterError) as err:
        resolve_user_topic("/scna", {"rt/scan", "rt/odom"}, 3)
    message = str(err.value)
    assert "not discovered on domain 3" in message and "rt/scan" in message


def test_user_topic_result_carries_the_resolution_note() -> None:
    from topicforge.adapters.common import user_topic_result

    result = user_topic_result("/scan", "live", "filter '/scan' matched the DDS topic 'rt/scan'")
    assert result.topic == "/scan" and result.count == 0
    assert result.note is not None
    assert result.note.startswith("filter '/scan' matched") and "disabled" in result.note
