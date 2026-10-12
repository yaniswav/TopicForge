"""The process runner, exercised against real child processes (Python scripts).

No ROS 2 needed: the children are small Python programs that sleep, spawn a
grandchild that holds the output pipes, or print a lot. These prove what
`subprocess.run(timeout=...)` could not: the deadline holds, the whole tree is
killed, output is bounded, and a clean exit leaves a daemon alone.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from topicforge.adapters.ros2_live import process_runner
from topicforge.adapters.ros2_live.process_runner import run_process

_IS_WINDOWS = sys.platform == "win32"


def _script(tmp_path: Path, body: str) -> list[str]:
    path = tmp_path / "child.py"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return [sys.executable, str(path)]


def _alive(pid: int) -> bool:
    if _IS_WINDOWS:
        import ctypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        k32.OpenProcess.restype = ctypes.c_void_p
        handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        k32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
        k32.CloseHandle(ctypes.c_void_p(handle))
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie still answers signal 0; a reaped child does not.
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as stat:
            return stat.read().split(")")[-1].split()[0] != "Z"
    except OSError:
        return True


def _wait_gone(pid: int, within_s: float = 5.0) -> bool:
    end = time.monotonic() + within_s
    while time.monotonic() < end:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return not _alive(pid)


def _read_pid(path: Path, within_s: float = 10.0) -> int:
    end = time.monotonic() + within_s
    while time.monotonic() < end:
        if path.exists() and path.read_text().strip():
            return int(path.read_text().strip())
        time.sleep(0.05)
    raise AssertionError("child never wrote its grandchild pid")


def test_success_returns_stdout_and_exit_code_zero(tmp_path: Path) -> None:
    cmd = _script(tmp_path, "print('hello')\n")
    result = run_process(cmd, deadline_s=20)
    assert result.returncode == 0 and result.stdout.strip() == "hello"
    assert not result.timed_out and not result.truncated


def test_non_zero_exit_code_and_stderr_are_reported(tmp_path: Path) -> None:
    cmd = _script(tmp_path, "import sys\nsys.stderr.write('boom\\n')\nsys.exit(3)\n")
    result = run_process(cmd, deadline_s=20)
    assert result.returncode == 3 and "boom" in result.stderr and not result.timed_out


def test_output_is_decoded_as_utf8_with_replacement(tmp_path: Path) -> None:
    cmd = _script(
        tmp_path,
        "import sys\nsys.stdout.buffer.write('h\\u00e9llo '.encode() + b'\\xff\\n')\n",
    )
    expected = "h" + chr(0xE9) + "llo " + chr(0xFFFD)
    assert run_process(cmd, deadline_s=20).stdout.strip() == expected


def test_missing_binary_raises_oserror(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        run_process([str(tmp_path / "no-such-binary")], deadline_s=5)


def test_deadline_is_honoured_for_a_hung_process(tmp_path: Path) -> None:
    cmd = _script(tmp_path, "import time\ntime.sleep(60)\n")
    started = time.monotonic()
    result = run_process(cmd, deadline_s=1.0)
    elapsed = time.monotonic() - started
    assert result.timed_out and result.returncode is None
    assert 0.9 <= elapsed < 8.0


def _grandchild_script(pid_file: Path) -> str:
    return f"""
        import subprocess, sys, time
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        open({str(pid_file)!r}, "w").write(str(child.pid))
        {{tail}}
    """


def test_grandchild_holding_the_pipes_is_killed_at_the_deadline(tmp_path: Path) -> None:
    """The launcher exits at once; the orphan keeps stdout open: the stock `ros2` failure."""
    pid_file = tmp_path / "grandchild.pid"
    cmd = _script(tmp_path, _grandchild_script(pid_file).replace("{tail}", "sys.exit(0)"))
    started = time.monotonic()
    result = run_process(cmd, deadline_s=1.5)
    elapsed = time.monotonic() - started
    grandchild = _read_pid(pid_file)
    assert result.timed_out
    assert elapsed < 10.0, "the runner must not wait for the orphan's pipe"
    assert _wait_gone(grandchild), "the grandchild survived the kill"


def test_grandchild_of_a_running_launcher_is_killed_at_the_deadline(tmp_path: Path) -> None:
    pid_file = tmp_path / "grandchild.pid"
    cmd = _script(tmp_path, _grandchild_script(pid_file).replace("{tail}", "time.sleep(120)"))
    result = run_process(cmd, deadline_s=1.5)
    assert result.timed_out
    assert _wait_gone(_read_pid(pid_file)), "the grandchild survived the kill"


@pytest.mark.skipif(not _IS_WINDOWS, reason="taskkill fallback is Windows-only")
def test_taskkill_fallback_kills_the_tree_without_a_job_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(process_runner.JobObject, "attach", classmethod(lambda cls, pid: None))
    pid_file = tmp_path / "grandchild.pid"
    cmd = _script(tmp_path, _grandchild_script(pid_file).replace("{tail}", "time.sleep(120)"))
    result = run_process(cmd, deadline_s=1.5)
    assert result.timed_out
    assert _wait_gone(_read_pid(pid_file)), "taskkill /T left the grandchild running"


@pytest.mark.skipif(_IS_WINDOWS, reason="process groups and signals are POSIX-only")
def test_posix_group_gets_sigint_before_sigkill(tmp_path: Path) -> None:
    marker = tmp_path / "interrupted"
    cmd = _script(
        tmp_path,
        f"""
        import signal, sys, time
        def stop(*_):
            open({str(marker)!r}, "w").write("sigint")
            sys.exit(0)
        signal.signal(signal.SIGINT, stop)
        time.sleep(60)
        """,
    )
    result = run_process(cmd, deadline_s=1.0)
    assert result.timed_out
    assert marker.exists(), "the CLI must get a chance to leave the graph cleanly"


def test_output_is_capped_and_the_child_is_not_blocked(tmp_path: Path) -> None:
    cmd = _script(
        tmp_path,
        """
        import sys
        chunk = b"x" * 65536
        for _ in range(80):  # 5 MiB
            sys.stdout.buffer.write(chunk)
        sys.stdout.flush()
        """,
    )
    started = time.monotonic()
    result = run_process(cmd, deadline_s=30, max_output_bytes=100_000)
    assert result.returncode == 0 and not result.timed_out
    assert len(result.stdout) == 100_000 and result.truncated
    assert time.monotonic() - started < 25


def test_a_clean_exit_leaves_a_detached_daemon_running(tmp_path: Path) -> None:
    """`ros2` starts its daemon on first use; killing it would make every call cold."""
    pid_file = tmp_path / "daemon.pid"
    detach = (
        "creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP"
        if _IS_WINDOWS
        else "start_new_session=True"
    )
    cmd = _script(
        tmp_path,
        f"""
        import subprocess, sys
        d = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, {detach})
        open({str(pid_file)!r}, "w").write(str(d.pid))
        """,
    )
    result = run_process(cmd, deadline_s=20)
    daemon = _read_pid(pid_file)
    try:
        assert result.returncode == 0 and not result.timed_out
        assert _alive(daemon), "a clean exit must not kill the daemon"
    finally:
        if _IS_WINDOWS:
            subprocess.run(
                ["taskkill", "/F", "/PID", str(daemon)], capture_output=True, check=False
            )
        else:
            os.kill(daemon, 9)
