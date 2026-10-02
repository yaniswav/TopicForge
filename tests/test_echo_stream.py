"""The streaming runner for `ros2 topic echo`, against a fake process (no ROS 2)."""

from __future__ import annotations

import queue
import subprocess
import time
from typing import Any

import pytest

from topicforge.adapters.ros2_live import echo_stream
from topicforge.adapters.ros2_live.echo_stream import kill_process_tree, stream_echo

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

    def fake_kill(proc: FakeProcess) -> None:
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
    assert options["stdin"] == subprocess.DEVNULL
    assert options["stdout"] == subprocess.PIPE and options["stderr"] == subprocess.PIPE
    assert options["encoding"] == "utf-8" and options["errors"] == "replace"
    assert "shell" not in options


def test_the_process_gets_its_own_group_on_posix(
    monkeypatch: pytest.MonkeyPatch, killed_processes: list[FakeProcess]
) -> None:
    monkeypatch.setattr(echo_stream.sys, "platform", "linux")
    run = _run(FakeProcess(_message(0)), count=1, deadline_s=5)
    assert run.seen["options"]["start_new_session"] is True


def test_the_process_hides_its_window_on_windows(
    monkeypatch: pytest.MonkeyPatch, killed_processes: list[FakeProcess]
) -> None:
    monkeypatch.setattr(echo_stream.sys, "platform", "win32")
    monkeypatch.setattr(echo_stream.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    run = _run(FakeProcess(_message(0)), count=1, deadline_s=5)
    assert run.seen["options"]["creationflags"] == 0x08000000


# ---- kill_process_tree -----------------------------------------------------


def test_kill_interrupts_the_group_first_then_kills_it_on_posix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(echo_stream.sys, "platform", "linux")
    monkeypatch.setattr(echo_stream, "_GRACE_SEC", 0.1)
    monkeypatch.setattr(echo_stream.os, "getpgid", lambda pid: 77, raising=False)
    monkeypatch.setattr(
        echo_stream.os, "killpg", lambda pgid, sig: calls.append((pgid, sig)), raising=False
    )
    monkeypatch.setattr(echo_stream.signal, "SIGINT", 2, raising=False)
    monkeypatch.setattr(echo_stream.signal, "SIGKILL", 9, raising=False)
    proc = FakeProcess("")
    kill_process_tree(proc)
    assert calls == [(77, 2), (77, 9)]
    assert proc.killed  # the plain kill is a backstop


def test_kill_does_not_wait_out_the_grace_period_for_a_process_that_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc = FakeProcess("")
    monkeypatch.setattr(echo_stream.sys, "platform", "linux")
    monkeypatch.setattr(echo_stream, "_GRACE_SEC", 30.0)
    monkeypatch.setattr(echo_stream.os, "getpgid", lambda pid: 77, raising=False)
    monkeypatch.setattr(echo_stream.signal, "SIGINT", 2, raising=False)
    monkeypatch.setattr(echo_stream.signal, "SIGKILL", 9, raising=False)

    def killpg(pgid: int, sig: int) -> None:
        if sig == 2:
            proc.exit_code = 130  # exits on SIGINT

    monkeypatch.setattr(echo_stream.os, "killpg", killpg, raising=False)
    started = time.monotonic()
    kill_process_tree(proc)
    assert time.monotonic() - started < 5


def test_kill_uses_taskkill_for_the_whole_tree_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def fake_run(cmd: list[str], **_kw: Any) -> None:
        commands.append(cmd)

    monkeypatch.setattr(echo_stream.sys, "platform", "win32")
    monkeypatch.setattr(echo_stream.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(echo_stream.subprocess, "run", fake_run)
    kill_process_tree(FakeProcess(""))
    assert commands == [["taskkill", "/F", "/T", "/PID", "4242"]]


def test_kill_is_a_no_op_for_a_process_that_already_exited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise AssertionError("must not signal an exited process")

    monkeypatch.setattr(echo_stream.subprocess, "run", boom)
    monkeypatch.setattr(echo_stream.os, "killpg", boom, raising=False)
    proc = FakeProcess("", exit_code=0)
    kill_process_tree(proc)
    assert not proc.killed


def test_kill_survives_a_group_that_is_already_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    def gone(*_a: Any) -> int:
        raise ProcessLookupError

    monkeypatch.setattr(echo_stream.sys, "platform", "linux")
    monkeypatch.setattr(echo_stream.os, "getpgid", gone, raising=False)
    monkeypatch.setattr(echo_stream.os, "killpg", gone, raising=False)
    monkeypatch.setattr(echo_stream.signal, "SIGINT", 2, raising=False)
    proc = FakeProcess("")
    kill_process_tree(proc)
    assert proc.killed
