"""Live `sample_messages`: the time budget, size cap and the notes of a short result."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest

from topicforge.adapters.base import AdapterError
from topicforge.adapters.ros2_live import adapter as adapter_module
from topicforge.adapters.ros2_live.adapter import Ros2CliAdapter
from topicforge.adapters.ros2_live.echo_stream import EchoDocument, EchoRun

_MODULE = "topicforge.adapters.ros2_live.adapter"
_INFO = "Type: sensor_msgs/msg/Imu\nPublisher count: 1\nSubscription count: 0\n"
_LATCHED_INFO = (
    _INFO
    + "\nNode name: talker\n"
    + "Endpoint type: PUBLISHER\n"
    + "Reliability: RELIABLE\n"
    + "Durability: TRANSIENT_LOCAL\n"
)
_DOC = "header:\n  stamp:\n    sec: 5\n    nanosec: 1\n  frame_id: base_link\n"


def _doc() -> EchoDocument:
    return EchoDocument(_DOC, 1)


def _stub_info(monkeypatch: pytest.MonkeyPatch, info: str = _INFO, delay_s: float = 0.0) -> None:
    monkeypatch.setattr(f"{_MODULE}.shutil.which", lambda name: f"/fake/bin/{name}")

    def run(*_a: object, **_k: object) -> SimpleNamespace:
        time.sleep(delay_s)
        return SimpleNamespace(returncode=0, stdout=info, stderr="")

    monkeypatch.setattr(f"{_MODULE}.subprocess.run", run)


def _stub_echo(monkeypatch: pytest.MonkeyPatch, run: EchoRun) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    def fake_stream(cmd: list[str], **kw: Any) -> EchoRun:
        seen.append(kw)
        return run

    monkeypatch.setattr(f"{_MODULE}.stream_echo", fake_stream)
    return seen


def test_timeout_bounds_the_topic_lookup_too(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_info(monkeypatch, delay_s=0.4)
    seen = _stub_echo(monkeypatch, EchoRun())
    Ros2CliAdapter().sample_messages("/imu", count=1, timeout_s=5)
    assert 4.0 < seen[0]["deadline_s"] <= 4.6


def test_the_topic_lookup_never_waits_longer_than_timeout_s(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(f"{_MODULE}.shutil.which", lambda name: f"/fake/bin/{name}")
    timeouts: list[float] = []

    def run(*_a: object, **kw: Any) -> SimpleNamespace:
        timeouts.append(float(kw["timeout"]))
        return SimpleNamespace(returncode=0, stdout=_INFO, stderr="")

    monkeypatch.setattr(f"{_MODULE}.subprocess.run", run)
    _stub_echo(monkeypatch, EchoRun(documents=[_doc()]))
    Ros2CliAdapter().sample_messages("/imu", count=1, timeout_s=2)
    assert timeouts == [2.0]


def test_a_short_result_says_fewer_than_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_info(monkeypatch)
    _stub_echo(monkeypatch, EchoRun(documents=[_doc()]))
    result = Ros2CliAdapter().sample_messages("/imu", count=3, timeout_s=7)
    assert result.count == 1 and result.note is not None
    assert result.note.startswith("1 of 3 messages within 7 s (fewer than requested).")
    assert "latched" not in result.note


def test_a_short_latched_result_hints_count_one(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_info(monkeypatch, _LATCHED_INFO)
    _stub_echo(monkeypatch, EchoRun(documents=[_doc()]))
    result = Ros2CliAdapter().sample_messages("/imu", count=3)
    assert result.note is not None and "latched" in result.note and "`count` 1" in result.note


def test_an_early_exit_with_messages_reports_the_exit_and_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_info(monkeypatch)
    run = EchoRun(documents=[_doc()], exit_code=1, stderr_tail="rcl error | shutdown")
    _stub_echo(monkeypatch, run)
    result = Ros2CliAdapter().sample_messages("/imu", count=3)
    assert result.count == 1 and result.note is not None
    assert "exited early (exit 1): rcl error | shutdown" in result.note
    assert "within" not in result.note


def test_an_exit_with_code_zero_says_the_cli_exited_early(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_info(monkeypatch)
    _stub_echo(monkeypatch, EchoRun(exit_code=0))
    result = Ros2CliAdapter().sample_messages("/imu", count=2)
    assert result.note is not None and "exited early (exit 0)" in result.note
    assert "within" not in result.note


def test_oversized_messages_are_reported_and_the_cap_is_passed_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_info(monkeypatch)
    seen = _stub_echo(monkeypatch, EchoRun(documents=[_doc()], oversized=2))
    result = Ros2CliAdapter(max_message_chars=1024 * 1024).sample_messages("/imu", count=1)
    assert seen[0]["max_document_chars"] == 1024 * 1024
    assert result.count == 1 and result.note is not None
    assert "2 message(s) over 1.0 MiB were dropped" in result.note
    assert "arrays_summary_only" in result.note


def test_decoding_stops_when_the_time_budget_is_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_info(monkeypatch)
    _stub_echo(monkeypatch, EchoRun(documents=[_doc(), _doc(), _doc()]))
    real = adapter_module.parse_echo_document

    def slow_parse(text: str, **kw: Any) -> Any:
        time.sleep(0.6)
        return real(text, **kw)

    monkeypatch.setattr(adapter_module, "parse_echo_document", slow_parse)
    monkeypatch.setattr(adapter_module, "_PARSE_GRACE_SEC", 0.0)
    result = Ros2CliAdapter().sample_messages("/imu", count=3, timeout_s=1)
    assert result.count == 2
    assert result.note is not None and "1 message(s) were received but not decoded" in result.note


def test_a_cli_that_cannot_start_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_info(monkeypatch)

    def boom(*_a: object, **_k: object) -> object:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("topicforge.adapters.ros2_live.echo_stream.subprocess.Popen", boom)
    with pytest.raises(AdapterError, match="could not start"):
        Ros2CliAdapter().sample_messages("/imu", count=1)
