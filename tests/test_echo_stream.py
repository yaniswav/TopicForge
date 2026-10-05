"""The streaming runner for `ros2 topic echo`, against a fake process (no ROS 2)."""

from __future__ import annotations

import queue
import subprocess
import time
from typing import Any

import pytest

from topicforge.adapters.base import AdapterError
from topicforge.adapters.ros2_live import echo_stream, process_tree
from topicforge.adapters.ros2_live.echo_stream import stream_echo

_EOF = object()


class _Pipe:
    """A text pipe: `readline` blocks until a line is fed or the pipe is closed."""

    def __init__(self) -> None:
        self._lines: queue.Queue[object] = queue.Queue()

    def feed(self, text: str) -> None:
        for line in text.splitlines(keepends=True):
            self._lines.put(line)

    def close(self) -> None:
        self._lines.put(_EOF)

    def readline(self) -> str:
        item = self._lines.get()
        if item is _EOF:
            self._lines.put(_EOF)  # later reads also see the end
            return ""
        return str(item)


class FakeProcess:
    """Stands in for `Popen`: prints scripted text, optionally exits by itself."""

    pid = 4242

    def __init__(self, script: str = "", *, exit_code: int | None = None, err: str = "") -> None:
        self.stdout = _Pipe()
        self.stderr = _Pipe()
        self.exit_code = exit_code
        self.stdout.feed(script)
        self.stderr.feed(err)
        if exit_code is not None:
            self.stdout.close()
            self.stderr.close()
        self.killed = False

    def poll(self) -> int | None:
        return self.exit_code

    def wait(self, timeout: float | None = None) -> int:
        if self.exit_code is None:
            raise subprocess.TimeoutExpired("ros2", timeout or 0)
        return self.exit_code

    def kill(self) -> None:
        self.killed = True


def _message(i: int) -> str:
    return f"data: m{i}\n---\n"


@pytest.fixture
def killed_processes(monkeypatch: pytest.MonkeyPatch) -> list[FakeProcess]:
    """Replace the tree kill with one that records the process and closes its pipes."""
    killed: list[FakeProcess] = []
    # The fake pid must never be attached to a real Windows job object.
    monkeypatch.setattr(echo_stream.JobObject, "attach", lambda pid: None)

    def fake_kill(proc: FakeProcess, job: object = None) -> None:
        killed.append(proc)
        proc.killed = True
        proc.stdout.close()
        proc.stderr.close()

    monkeypatch.setattr(echo_stream, "kill_process_tree", fake_kill)
    return killed


def _run(proc: FakeProcess, **kwargs: Any) -> Any:
    seen: dict[str, Any] = {}

    def factory(cmd: list[str], **options: Any) -> FakeProcess:
        seen["cmd"] = cmd
        seen["options"] = options
        return proc

    result = stream_echo(["ros2", "topic", "echo", "/t"], popen=factory, **kwargs)
    result.seen = seen  # type: ignore[attr-defined]
    return result


def test_collects_the_requested_count_and_kills_the_process_early(
    killed_processes: list[FakeProcess],
) -> None:
    proc = FakeProcess("".join(_message(i) for i in range(10)))
    run = _run(proc, count=3, deadline_s=5)
    assert [d.text for d in run.documents] == [f"data: m{i}\n" for i in range(3)]
    assert killed_processes == [proc]
    assert run.exit_code is None


def test_documents_carry_the_wall_clock_at_read_time(
    killed_processes: list[FakeProcess],
) -> None:
    before = time.time_ns()
    run = _run(FakeProcess(_message(0)), count=1, deadline_s=5)
    assert before <= run.documents[0].received_ns <= time.time_ns()


def test_the_deadline_returns_a_partial_result_and_kills(
    killed_processes: list[FakeProcess],
) -> None:
    proc = FakeProcess(_message(0) + _message(1))  # then silence
    started = time.monotonic()
    run = _run(proc, count=5, deadline_s=0.3)
    assert time.monotonic() - started < 3
    assert len(run.documents) == 2
    assert killed_processes == [proc]


def test_no_output_at_the_deadline_gives_an_empty_run(
    killed_processes: list[FakeProcess],
) -> None:
    run = _run(FakeProcess(), count=1, deadline_s=0.2)
    assert run.documents == [] and run.exit_code is None
    assert len(killed_processes) == 1


def test_a_message_cut_off_by_the_deadline_is_not_returned(
    killed_processes: list[FakeProcess],
) -> None:
    run = _run(FakeProcess(_message(0) + "data: half"), count=2, deadline_s=0.2)
    assert [d.text for d in run.documents] == ["data: m0\n"]


def test_a_process_that_exits_on_its_own_reports_its_exit_code(
    killed_processes: list[FakeProcess],
) -> None:
    proc = FakeProcess("", exit_code=1, err="warming up\nrcl_init failed\n")
    run = _run(proc, count=1, deadline_s=5)
    assert run.documents == [] and run.exit_code == 1
    assert run.stderr_tail == "warming up | rcl_init failed"


def test_messages_printed_before_an_exit_are_kept(killed_processes: list[FakeProcess]) -> None:
    run = _run(FakeProcess(_message(0) + _message(1), exit_code=0), count=5, deadline_s=5)
    assert len(run.documents) == 2 and run.exit_code == 0


def test_stderr_is_kept_to_the_last_lines(killed_processes: list[FakeProcess]) -> None:
    err = "".join(f"line {i}\n" for i in range(20))
    run = _run(FakeProcess("", exit_code=2, err=err), count=1, deadline_s=5)
    assert run.stderr_tail.endswith("line 19") and "line 0" not in run.stderr_tail


def test_the_process_is_started_unbuffered_without_a_shell_or_stdin(
    killed_processes: list[FakeProcess],
) -> None:
    run = _run(FakeProcess(_message(0)), count=1, deadline_s=5)
    options = run.seen["options"]
    assert run.seen["cmd"] == ["ros2", "topic", "echo", "/t"]
    assert options["env"]["PYTHONUNBUFFERED"] == "1"
    assert options["env"]["PYTHONIOENCODING"] == "utf-8"
    assert options["stdin"] == subprocess.DEVNULL
    assert options["stdout"] == subprocess.PIPE and options["stderr"] == subprocess.PIPE
    assert options["encoding"] == "utf-8" and options["errors"] == "replace"
    assert "shell" not in options


def test_the_process_gets_its_own_group_on_posix(
    monkeypatch: pytest.MonkeyPatch, killed_processes: list[FakeProcess]
) -> None:
    monkeypatch.setattr(process_tree.sys, "platform", "linux")
    run = _run(FakeProcess(_message(0)), count=1, deadline_s=5)
    assert run.seen["options"]["start_new_session"] is True


def test_the_process_hides_its_window_on_windows(
    monkeypatch: pytest.MonkeyPatch, killed_processes: list[FakeProcess]
) -> None:
    monkeypatch.setattr(process_tree.sys, "platform", "win32")
    monkeypatch.setattr(process_tree.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    run = _run(FakeProcess(_message(0)), count=1, deadline_s=5)
    assert run.seen["options"]["creationflags"] == 0x08000000


def test_a_message_over_the_cap_is_dropped_while_streaming(
    killed_processes: list[FakeProcess],
) -> None:
    big = "data: " + "x" * 200 + "\n"
    script = _message(0) + big + "more: y\n---\n" + _message(1)
    run = _run(FakeProcess(script), count=2, deadline_s=5, max_document_chars=100)
    assert [d.text for d in run.documents] == ["data: m0\n", "data: m1\n"]
    assert run.oversized == 1


def test_an_unfinished_oversized_message_is_counted(killed_processes: list[FakeProcess]) -> None:
    script = _message(0) + "data: " + "x" * 200 + "\n"
    run = _run(FakeProcess(script), count=2, deadline_s=0.3, max_document_chars=100)
    assert len(run.documents) == 1 and run.oversized == 1


def test_no_cap_keeps_every_message(killed_processes: list[FakeProcess]) -> None:
    run = _run(FakeProcess("data: " + "x" * 5000 + "\n---\n"), count=1, deadline_s=5)
    assert len(run.documents) == 1 and run.oversized == 0


def test_non_ascii_text_survives_the_stream(killed_processes: list[FakeProcess]) -> None:
    run = _run(FakeProcess("data: h\u00e9llo \u4e16\u754c\n---\n"), count=1, deadline_s=5)
    assert run.documents[0].text == "data: h\u00e9llo \u4e16\u754c\n"


def test_stderr_is_bounded(killed_processes: list[FakeProcess]) -> None:
    err = "".join(f"line {i}\n" for i in range(500))
    run = _run(FakeProcess("", exit_code=2, err=err), count=1, deadline_s=5)
    assert run.stderr_tail.endswith("line 499")


@pytest.mark.parametrize(
    "error", [PermissionError(13, "Permission denied"), OSError(193, "bad exe")]
)
def test_a_process_that_cannot_start_is_an_adapter_error(error: OSError) -> None:
    def factory(cmd: list[str], **_options: Any) -> FakeProcess:
        raise error

    with pytest.raises(AdapterError, match="could not start `ros2`"):
        stream_echo(
            ["/opt/ros/bin/ros2", "topic", "echo", "/t"], count=1, deadline_s=1, popen=factory
        )


def test_the_call_returns_within_the_deadline_plus_the_stop(
    killed_processes: list[FakeProcess],
) -> None:
    started = time.monotonic()
    _run(FakeProcess(_message(0)), count=5, deadline_s=1.0)
    assert time.monotonic() - started < 1.0 + 0.5
