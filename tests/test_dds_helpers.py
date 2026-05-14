"""Unit tests for `adapters/common/dds_helpers`.

Pure tests — no DDS middleware needed, no monkeypatching, no fixtures.
"""

from __future__ import annotations

from topicforge.adapters.common import (
    DDS_ONLY_ERROR_MSG,
    canonicalize_vendor_id,
    format_guid,
)

# ---------------------------------------------------------------------------
# canonicalize_vendor_id — OMG vendor_id mapping
# ---------------------------------------------------------------------------


def test_cyclone_vendor_id() -> None:
    assert canonicalize_vendor_id((0x01, 0x16)) == "cyclone"


def test_fast_dds_vendor_id() -> None:
    assert canonicalize_vendor_id((0x01, 0x05)) == "fast"


def test_rti_vendor_id() -> None:
    assert canonicalize_vendor_id((0x01, 0x01)) == "rti"


def test_unknown_vendor_id_collapses_to_unknown() -> None:
    """Any vendor not in the lookup table falls back to 'unknown' — never raises."""
    assert canonicalize_vendor_id((0x99, 0x99)) == "unknown"


def test_opensplice_collapses_to_unknown() -> None:
    """OpenSplice (EOL) is intentionally mapped to 'unknown', not its own tag."""
    assert canonicalize_vendor_id((0x01, 0x02)) == "unknown"


def test_opendds_collapses_to_unknown() -> None:
    """OpenDDS exists at the wire level but has no first-class TopicForge tag yet."""
    assert canonicalize_vendor_id((0x01, 0x03)) == "unknown"


def test_dust_dds_collapses_to_unknown() -> None:
    """Dust DDS (Rust) is observed but reports as 'unknown' — no Python adapter."""
    assert canonicalize_vendor_id((0x01, 0x11)) == "unknown"


def test_vendor_id_accepts_bytes() -> None:
    assert canonicalize_vendor_id(b"\x01\x16") == "cyclone"


def test_vendor_id_short_bytes_collapses_to_unknown() -> None:
    """A truncated bytes input never raises."""
    assert canonicalize_vendor_id(b"\x01") == "unknown"


def test_vendor_id_short_tuple_collapses_to_unknown() -> None:
    assert canonicalize_vendor_id((0x01,)) == "unknown"  # type: ignore[arg-type]


def test_vendor_id_none_collapses_to_unknown() -> None:
    assert canonicalize_vendor_id(None) == "unknown"


# ---------------------------------------------------------------------------
# format_guid — OMG GUID rendering
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
    """A 4-byte input pads with zeros instead of raising — defensive against
    edge-case bindings that return partial GUIDs."""
    assert format_guid(b"\x01\x02\x03\x04") == (
        "01020304.00000000.00000000.00000000"
    )


def test_format_guid_truncates_long_bytes() -> None:
    """A 20-byte input drops the extra 4 bytes silently."""
    raw = bytes(range(20))
    expected = "00010203.04050607.08090a0b.0c0d0e0f"
    assert format_guid(raw) == expected


# ---------------------------------------------------------------------------
# DDS_ONLY_ERROR_MSG — remediation contract
# ---------------------------------------------------------------------------


def test_dds_only_error_msg_mentions_remediation() -> None:
    """The DDS-only error message must point at the standard remediation
    path so an LLM client can take action without re-reading docs."""
    assert "TOPICFORGE_DDS_BACKEND" in DDS_ONLY_ERROR_MSG
    assert "TOPICFORGE_MODE" in DDS_ONLY_ERROR_MSG
