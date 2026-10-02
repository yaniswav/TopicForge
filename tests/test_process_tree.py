"""Stopping a process tree: scripted calls, then real processes that outlive their launcher."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

from topicforge.adapters.ros2_live import process_tree
from topicforge.adapters.ros2_live.echo_stream import stream_echo
from topicforge.adapters.ros2_live.process_tree import kill_process_tree


class FakeProcess:
    pid = 4242

    def __init__(self, exit_code: int | None = None) -> None:
        self.exit_code = exit_code
        self.killed = False

    def poll(self) -> int | None:
        return self.exit_code

    def wait(self, timeout: float | None = None) -> int:
        if self.exit_code is None:
            raise subprocess.TimeoutExpired("ros2", timeout or 0)
        return self.exit_code

    def kill(self) -> None:
        self.killed = True


def _posix(monkeypatch: pytest.MonkeyPatch, killpg: Any) -> None:
    monkeypatch.setattr(process_tree.sys, "platform", "linux")
    monkeypatch.setattr(process_tree.os, "killpg", killpg, raising=False)
    monkeypatch.setattr(process_tree.signal, "SIGINT", 2, raising=False)
    monkeypatch.setattr(process_tree.signal, "SIGKILL", 9, raising=False)


def test_kill_interrupts_the_group_first_then_kills_it_on_posix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, int]] = []

    def killpg(pgid: int, sig: int) -> None:
        calls.append((pgid, sig))

    _posix(monkeypatch, killpg)
    monkeypatch.setattr(process_tree, "GRACE_SEC", 0.1)
    proc = FakeProcess()
    kill_process_tree(proc)
    assert calls[0] == (4242, 2) and calls[-1] == (4242, 9)
    assert all(pgid == 4242 for pgid, _ in calls)
    assert proc.killed  # the plain kill is a backstop


def test_kill_signals_the_group_even_when_the_launcher_already_exited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def killpg(pgid: int, sig: int) -> None:
        calls.append(sig)

    _posix(monkeypatch, killpg)
    monkeypatch.setattr(process_tree, "GRACE_SEC", 0.1)
    kill_process_tree(FakeProcess(exit_code=0))
    assert 2 in calls and 9 in calls


def test_kill_does_not_wait_out_the_grace_period_for_a_group_that_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gone = False
    calls: list[int] = []

    def killpg(pgid: int, sig: int) -> None:
        nonlocal gone
        calls.append(sig)
        if gone and sig == 0:
            raise ProcessLookupError
        if sig == 2:
            gone = True  # exits on SIGINT

    _posix(monkeypatch, killpg)
    monkeypatch.setattr(process_tree, "GRACE_SEC", 30.0)
    started = time.monotonic()
    kill_process_tree(FakeProcess())
    assert time.monotonic() - started < 5
    assert 9 not in calls


def test_kill_survives_a_group_that_is_already_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    def gone(*_a: Any) -> None:
        raise ProcessLookupError

    _posix(monkeypatch, gone)
    proc = FakeProcess()
    kill_process_tree(proc)
    assert proc.killed


def test_kill_terminates_the_job_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    terminated: list[bool] = []

    class FakeJob:
        def terminate(self) -> None:
            terminated.append(True)

    monkeypatch.setattr(process_tree.sys, "platform", "win32")
    kill_process_tree(FakeProcess(exit_code=0), FakeJob())  # type: ignore[arg-type]
    assert terminated == [True]


def test_kill_uses_taskkill_for_the_whole_tree_on_windows_without_a_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def fake_run(cmd: list[str], **_kw: Any) -> None:
        commands.append(cmd)

    monkeypatch.setattr(process_tree.sys, "platform", "win32")
    monkeypatch.setattr(process_tree.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(process_tree.subprocess, "run", fake_run)
    kill_process_tree(FakeProcess(exit_code=0))  # taskkill runs even for an exited launcher
    assert commands == [["taskkill", "/F", "/T", "/PID", "4242"]]


def test_no_job_object_off_windows() -> None:
    if sys.platform != "win32":
        assert process_tree.JobObject.attach(os.getpid()) is None


# ---- real processes ---------------------------------------------------------

# A launcher that starts a child printing YAML forever and exits at once, the
# way the `ros2` entry point can leave its worker running.
_CHILD = (
    "import os, time\n"
    "while True:\n"
    "    print('pid: %d\\nname: h\\u00e9llo\\n---' % os.getpid(), flush=True)\n"
    "    time.sleep(0.05)\n"
)


def _launcher(tmp_path: Path) -> list[str]:
    child = tmp_path / "child.py"
    child.write_text(_CHILD, encoding="utf-8")
    code = f"import subprocess, sys\nsubprocess.Popen([sys.executable, {str(child)!r}])\n"
    return [sys.executable, "-c", code]


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        k32.GetExitCodeProcess(handle, ctypes.byref(code))
        k32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat = Path(f"/proc/{pid}/stat")
    return not (stat.exists() and stat.read_text().rsplit(")", 1)[1].split()[0] == "Z")


def _wait_dead(pid: int, seconds: float = 3.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return not _alive(pid)


def test_an_orphaned_child_is_killed_and_the_call_stays_within_its_budget(
    tmp_path: Path,
) -> None:
    started = time.monotonic()
    run = stream_echo(_launcher(tmp_path), count=10_000, deadline_s=2.0)
    elapsed = time.monotonic() - started

    assert elapsed < 2.0 + 3.0
    assert run.documents, "the child's output reaches the runner"
    pid = yaml.safe_load(run.documents[0].text)["pid"]
    assert _wait_dead(pid), "the child outlived the call"
    # PYTHONIOENCODING makes the child write UTF-8 whatever the locale.
    assert yaml.safe_load(run.documents[0].text)["name"] == "h\u00e9llo"


def test_the_count_stops_an_orphaned_child_early(tmp_path: Path) -> None:
    started = time.monotonic()
    run = stream_echo(_launcher(tmp_path), count=3, deadline_s=20.0)
    assert time.monotonic() - started < 10
    assert len(run.documents) == 3
    assert _wait_dead(yaml.safe_load(run.documents[0].text)["pid"])
