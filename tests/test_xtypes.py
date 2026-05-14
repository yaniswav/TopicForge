"""Unit tests for `topicforge.adapters.common.xtypes`.

Pure-logic helpers — no DDS dependency. Same convention as
`tests/test_qos_analyzer.py` and `tests/test_lifecycle_buffer.py`.
"""

from __future__ import annotations

from topicforge.adapters.common.xtypes import (
    _RAW_BYTES_PREVIEW_LIMIT,
    annotate_full,
    annotate_partial,
    annotate_raw,
)


def test_annotate_full_marks_status_and_passes_fields_through() -> None:
    payload = annotate_full({"seq": 7, "battery_pct": 81.2, "status": "ok"})
    assert payload["_decode_status"] == "full"
    assert payload["seq"] == 7
    assert payload["battery_pct"] == 81.2
    assert payload["status"] == "ok"
    # `full` is never accompanied by raw bytes or notes.
    assert "_raw_bytes_hex" not in payload
    assert "_decode_note" not in payload


def test_annotate_full_returns_independent_dict() -> None:
    src = {"seq": 1}
    payload = annotate_full(src)
    payload["seq"] = 999
    assert src["seq"] == 1  # caller's dict is not mutated


def test_annotate_partial_carries_note_and_raw_bytes() -> None:
    payload = annotate_partial(
        {"seq": 7},
        note="union 'mode' skipped",
        raw_bytes=bytes.fromhex("cafebabe"),
    )
    assert payload["_decode_status"] == "partial"
    assert payload["_decode_note"] == "union 'mode' skipped"
    assert payload["_raw_bytes_hex"] == "cafebabe"
    assert payload["seq"] == 7


def test_annotate_partial_without_raw_bytes() -> None:
    payload = annotate_partial({"seq": 7}, note="optional field omitted")
    assert payload["_decode_status"] == "partial"
    assert payload["_decode_note"] == "optional field omitted"
    assert "_raw_bytes_hex" not in payload


def test_annotate_raw_carries_status_note_and_bytes() -> None:
    payload = annotate_raw(bytes.fromhex("deadbeef"), note="binding unavailable")
    assert payload["_decode_status"] == "raw"
    assert payload["_decode_note"] == "binding unavailable"
    assert payload["_raw_bytes_hex"] == "deadbeef"


def test_annotate_raw_truncates_large_payloads() -> None:
    big_payload = bytes(b"\x01" * (_RAW_BYTES_PREVIEW_LIMIT))  # 2x in hex
    payload = annotate_raw(big_payload, note="big blob")
    assert isinstance(payload["_raw_bytes_hex"], str)
    assert len(payload["_raw_bytes_hex"]) == _RAW_BYTES_PREVIEW_LIMIT
    assert payload["_raw_bytes_truncated"] is True


def test_annotate_raw_does_not_flag_truncation_when_under_limit() -> None:
    payload = annotate_raw(bytes.fromhex("dead"), note="small")
    assert payload["_raw_bytes_hex"] == "dead"
    assert "_raw_bytes_truncated" not in payload


def test_raw_bytes_preview_limit_is_documented_constant() -> None:
    """Pin the constant — tool description quotes it."""
    assert _RAW_BYTES_PREVIEW_LIMIT == 4096
