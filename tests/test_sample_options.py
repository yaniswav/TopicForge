"""`sample_messages` array options and size caps, through the Inspector and the MCP layer."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from topicforge.adapters.base import AdapterError
from topicforge.adapters.ros2_mock import MockAdapter
from topicforge.config import Settings, load_settings
from topicforge.constants import (
    DEFAULT_MAX_SAMPLE_BYTES,
    DEFAULT_SAMPLE_TIMEOUT_S,
    MAX_SAMPLE_CALL_FACTOR,
    MAX_SAMPLE_COUNT,
)
from topicforge.models import MessageSample, SampleResult
from topicforge.server import build_app
from topicforge.services import Inspector
from topicforge.services.sample_budget import apply_sample_budget, sample_size_bytes


def _sample(payload: dict[str, object]) -> MessageSample:
    return MessageSample(topic="/t", message_type="p/msg/T", timestamp_ns=0, payload=payload)


class _Recorder(MockAdapter):
    """Mock adapter that records the options it receives and returns canned samples."""

    def __init__(self, samples: list[MessageSample]) -> None:
        self.seen: dict[str, Any] = {}
        self._samples = samples
        self.note: str | None = None

    def sample_messages(self, topic: str, count: int, **options: Any) -> SampleResult:
        self.seen = options
        return SampleResult(
            topic=topic,
            count=len(self._samples),
            samples=self._samples,
            mode_effective=self.effective_mode,
            note=self.note,
        )


# ---- Inspector -------------------------------------------------------------


def test_options_are_forwarded_to_the_adapter() -> None:
    adapter = _Recorder([])
    Inspector(adapter).sample_messages("/scan", 1, max_array_length=1024, arrays_summary_only=True)
    assert adapter.seen == {
        "max_array_length": 1024,
        "arrays_summary_only": True,
        "timeout_s": DEFAULT_SAMPLE_TIMEOUT_S,
    }


def test_defaults_match_the_cli_default() -> None:
    adapter = _Recorder([])
    Inspector(adapter).sample_messages("/scan", 1)
    assert adapter.seen == {
        "max_array_length": 128,
        "arrays_summary_only": False,
        "timeout_s": DEFAULT_SAMPLE_TIMEOUT_S,
    }


def test_none_means_no_truncation_and_is_forwarded() -> None:
    adapter = _Recorder([])
    Inspector(adapter).sample_messages("/scan", 1, max_array_length=None)
    assert adapter.seen["max_array_length"] is None


@pytest.mark.parametrize("bad", [0, -5, 65537, True, "10"])
def test_invalid_max_array_length_is_rejected(bad: Any) -> None:
    with pytest.raises(AdapterError, match="max_array_length"):
        Inspector(_Recorder([])).sample_messages("/scan", 1, max_array_length=bad)


@pytest.mark.parametrize("ok", [1, 128, 65536])
def test_max_array_length_bounds_are_accepted(ok: int) -> None:
    Inspector(_Recorder([])).sample_messages("/scan", 1, max_array_length=ok)


def test_truncation_is_reported_in_the_note() -> None:
    cut = _sample({"ranges": [1.0], "_truncated_fields": ["ranges"]})
    result = Inspector(_Recorder([cut])).sample_messages("/scan", 1)
    assert result.note is not None
    assert "cut" in result.note and "max_array_length" in result.note


def test_no_note_when_nothing_was_cut() -> None:
    result = Inspector(_Recorder([_sample({"data": "1"})])).sample_messages("/scan", 1)
    assert result.note is None


def test_mock_mode_accepts_and_ignores_the_options() -> None:
    result = Inspector(MockAdapter()).sample_messages(
        "/cmd_vel", 2, max_array_length=None, arrays_summary_only=True
    )
    assert result.count == 2 and result.note is None


# ---- size budget -----------------------------------------------------------


def test_a_message_over_the_cap_is_dropped_with_a_note() -> None:
    big = _sample({"data": "x" * 5000})
    small = _sample({"data": "y"})
    kept, notes = apply_sample_budget([big, small], per_message_bytes=2000)
    assert kept == [small]
    assert len(notes) == 1 and "per-message cap" in notes[0] and "max_array_length" in notes[0]


def test_the_per_call_cap_stops_accumulation() -> None:
    size = sample_size_bytes(_sample({"data": "x" * 1000}))
    per_message = size + 10
    samples = [_sample({"data": "x" * 1000}) for _ in range(MAX_SAMPLE_CALL_FACTOR + 3)]
    kept, notes = apply_sample_budget(samples, per_message_bytes=per_message)
    assert len(kept) == MAX_SAMPLE_CALL_FACTOR
    assert "3 message(s) dropped" in notes[0] and "per-call cap" in notes[0]


def test_samples_within_the_budget_pass_unchanged() -> None:
    samples = [_sample({"a": i}) for i in range(5)]
    kept, notes = apply_sample_budget(samples, per_message_bytes=DEFAULT_MAX_SAMPLE_BYTES)
    assert kept == samples and notes == []


def test_inspector_applies_the_configured_cap() -> None:
    adapter = _Recorder([_sample({"data": "x" * 5000})])
    result = Inspector(adapter, max_sample_bytes=2000).sample_messages("/scan", 1)
    assert result.count == 0 and result.samples == []
    assert result.note is not None and "dropped" in result.note


def test_peek_bag_samples_is_capped_too() -> None:
    class _Bag(MockAdapter):
        def peek_bag_samples(self, path: str, topic: str, count: int) -> Any:
            from topicforge.models import SampleResult

            return SampleResult(
                topic=topic,
                count=2,
                samples=[_sample({"d": "x" * 5000}), _sample({"d": "y"})],
                mode_effective="live",
                note="decoded with Humble",
            )

    result = Inspector(_Bag(), max_sample_bytes=2000).peek_bag_samples("/tmp/a.mcap", "/t", 2)
    assert result.count == 1
    assert result.note is not None
    assert "decoded with Humble" in result.note and "dropped" in result.note


# ---- settings --------------------------------------------------------------


def test_max_sample_bytes_defaults_to_one_mib() -> None:
    assert load_settings(env={}).max_sample_bytes == 1024 * 1024


def test_max_sample_bytes_from_env() -> None:
    assert load_settings(env={"TOPICFORGE_MAX_SAMPLE_BYTES": "65536"}).max_sample_bytes == 65536


@pytest.mark.parametrize("bad", ["abc", "10", "999999999999"])
def test_max_sample_bytes_rejects_bad_values(bad: str) -> None:
    with pytest.raises(ValueError, match="TOPICFORGE_MAX_SAMPLE_BYTES"):
        load_settings(env={"TOPICFORGE_MAX_SAMPLE_BYTES": bad})


# ---- MCP tool surface ------------------------------------------------------


def _app() -> Any:
    return build_app(
        Settings(mode="mock", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False)
    )


def test_tool_schema_exposes_the_options() -> None:
    tools = {t.name: t for t in asyncio.run(_app().list_tools())}
    props = tools["sample_messages"].input_schema["properties"]
    assert props["max_array_length"]["default"] == 128
    assert props["arrays_summary_only"]["default"] is False
    assert props["count"]["maximum"] == MAX_SAMPLE_COUNT
    assert props["timeout_s"]["default"] == DEFAULT_SAMPLE_TIMEOUT_S
    assert props["timeout_s"]["minimum"] == 1 and props["timeout_s"]["maximum"] == 45


def test_tool_call_over_the_cap_is_capped_not_rejected() -> None:
    out = asyncio.run(_app().call_tool("sample_messages", {"topic": "/cmd_vel", "count": 60}))
    assert "capped to 50" in json.dumps(out, default=str)


def test_tool_call_with_options_succeeds_in_mock_mode() -> None:
    out = asyncio.run(
        _app().call_tool(
            "sample_messages",
            {"topic": "/cmd_vel", "count": 1, "max_array_length": 512, "arrays_summary_only": True},
        )
    )
    text = json.dumps(out, default=str)
    assert "/cmd_vel" in text


def test_tool_call_rejects_an_out_of_range_length() -> None:
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError):
        asyncio.run(
            _app().call_tool("sample_messages", {"topic": "/cmd_vel", "max_array_length": 0})
        )


class _LiveRecorder(_Recorder):
    @property
    def effective_mode(self) -> Any:
        return "live"


def test_cut_fields_are_named_in_the_note() -> None:
    cut = _sample({"data": "abcd...", "_truncated_fields": ["data"]})
    result = Inspector(_Recorder([cut])).sample_messages("/chat", 1)
    assert result.note is not None and "_truncated_fields" in result.note


def test_the_adapter_note_comes_first_and_is_kept() -> None:
    adapter = _Recorder([])
    adapter.note = "0 of 3 messages within 10 s."
    result = Inspector(adapter).sample_messages("/scan", 3)
    assert result.note == "0 of 3 messages within 10 s."


def test_a_count_over_the_cap_is_capped_with_a_note() -> None:
    adapter = _Recorder([])
    seen: list[int] = []
    original = adapter.sample_messages

    def spy(topic: str, count: int, **options: Any) -> SampleResult:
        seen.append(count)
        return original(topic, count, **options)

    adapter.sample_messages = spy  # type: ignore[method-assign]
    result = Inspector(adapter).sample_messages("/scan", 60)
    assert seen == [MAX_SAMPLE_COUNT]
    assert result.note is not None and "capped to 50 (requested 60)" in result.note


def test_a_count_at_the_cap_has_no_cap_note() -> None:
    assert Inspector(_Recorder([])).sample_messages("/scan", MAX_SAMPLE_COUNT).note is None


@pytest.mark.parametrize("bad", [0.5, 45.5, 60, -1, True, "10"])
def test_invalid_timeout_is_rejected(bad: Any) -> None:
    with pytest.raises(AdapterError, match="timeout_s"):
        Inspector(_Recorder([])).sample_messages("/scan", 1, timeout_s=bad)


@pytest.mark.parametrize("ok", [1, 10, 45.0])
def test_timeout_bounds_are_accepted_and_forwarded(ok: float) -> None:
    adapter = _Recorder([])
    Inspector(adapter).sample_messages("/scan", 1, timeout_s=ok)
    assert adapter.seen["timeout_s"] == ok


def test_empty_result_has_no_timeout_note_when_nothing_was_requested() -> None:
    assert Inspector(_LiveRecorder([])).sample_messages("/scan", 0).note is None


def test_mock_empty_result_has_no_timeout_note() -> None:
    assert Inspector(_Recorder([])).sample_messages("/scan", 1).note is None
