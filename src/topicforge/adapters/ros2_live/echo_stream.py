"""Run `ros2 topic echo` as a stream: collect messages until a count or a deadline.

`echo` has no `--times` option on Humble or Rolling and prints until killed,
so the runner reads its stdout in a thread, stops at the first of N messages
or the wall deadline, and kills the whole process tree. The `ros2` entry
point is a launcher that can leave the real process running when only the
launcher is killed, and an orphan keeps the pipes open.
"""

from __future__ import annotations

import contextlib
import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import IO, Any, Protocol

from topicforge.adapters.ros2_live.echo_parser import ECHO_DOCUMENT_SEPARATOR, join_document_lines

log = logging.getLogger(__name__)

_JOIN_TIMEOUT_SEC = 2.0
_GRACE_SEC = 1.5
_STDERR_TAIL_LINES = 5


class _Process(Protocol):
    """The part of `subprocess.Popen` the runner uses."""

    pid: int
    stdout: IO[str] | None
    stderr: IO[str] | None

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def kill(self) -> None: ...


PopenFactory = Callable[..., _Process]


@dataclass(frozen=True)
class EchoDocument:
    """One complete message as printed by the CLI, with the wall clock at its last line."""

    text: str
    received_ns: int


@dataclass
class EchoRun:
    """Outcome of one streaming run.

    `exit_code` is the process exit code when it ended on its own before the
    run was stopped, `None` when the runner stopped it.
    """

    documents: list[EchoDocument] = field(default_factory=list)
    exit_code: int | None = None
    stderr_tail: str = ""


def stream_echo(
    cmd: list[str],
    *,
    count: int,
    deadline_s: float,
    popen: PopenFactory = subprocess.Popen,
    clock: Callable[[], float] = time.monotonic,
) -> EchoRun:
    """Run `cmd` and return up to `count` messages, within `deadline_s` seconds of the start.

    The process is always stopped (with its children) before returning.
    `popen` and `clock` are injectable for tests.
    """
    started = clock()
    proc = popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        **_spawn_options(),
    )
    lines: queue.Queue[tuple[str, int] | None] = queue.Queue()
    stderr_lines: list[str] = []
    threads = [
        threading.Thread(target=_pump_stdout, args=(proc.stdout, lines), daemon=True),
        threading.Thread(target=_pump_stderr, args=(proc.stderr, stderr_lines), daemon=True),
    ]
    for thread in threads:
        thread.start()

    run = EchoRun()
    current: list[str] = []
    try:
        while len(run.documents) < count:
            remaining = deadline_s - (clock() - started)
            if remaining <= 0:
                break
            try:
                item = lines.get(timeout=remaining)
            except queue.Empty:
                break
            if item is None:
                # A process that closed its output but still runs is killed below.
                with contextlib.suppress(subprocess.TimeoutExpired):
                    run.exit_code = proc.wait(timeout=_JOIN_TIMEOUT_SEC)
                break
            line, received_ns = item
            if line.rstrip() == ECHO_DOCUMENT_SEPARATOR:
                run.documents.append(EchoDocument(join_document_lines(current), received_ns))
                current = []
            else:
                current.append(line.rstrip("\r\n"))
    finally:
        kill_process_tree(proc)
        for thread in threads:
            thread.join(timeout=_JOIN_TIMEOUT_SEC)
    tail = [ln for ln in stderr_lines if ln.strip()][-_STDERR_TAIL_LINES:]
    run.stderr_tail = " | ".join(ln.strip() for ln in tail)
    return run


def _spawn_options() -> dict[str, Any]:
    """`Popen` options that make the process tree killable as a unit."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}


def _pump_stdout(stream: IO[str] | None, out: queue.Queue[tuple[str, int] | None]) -> None:
    """Forward each stdout line with its arrival time; `None` marks end of stream."""
    try:
        if stream is not None:
            for line in iter(stream.readline, ""):
                out.put((line, time.time_ns()))
    except (OSError, ValueError):
        pass  # pipe closed by the kill
    finally:
        out.put(None)


def _pump_stderr(stream: IO[str] | None, out: list[str]) -> None:
    """Drain stderr so a chatty process cannot block on a full pipe."""
    try:
        if stream is not None:
            for line in iter(stream.readline, ""):
                out.append(line)
    except (OSError, ValueError):
        pass


def kill_process_tree(proc: _Process) -> None:
    """Stop `proc` and its descendants; a no-op when it already exited.

    POSIX: interrupt, then kill. Windows: `taskkill /F /T` (no graceful stop).
    """
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
                timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            _stop_posix_group(proc)
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("process tree kill failed: %s", exc)
    try:
        proc.kill()
        proc.wait(timeout=_JOIN_TIMEOUT_SEC)
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("process kill failed: %s", exc)


def _stop_posix_group(proc: _Process) -> None:
    """SIGINT the process group so the CLI leaves the DDS graph cleanly, then SIGKILL it.

    A subscriber that is killed outright stays matched on the publisher.
    Measured on Humble / Fast DDS: repeated hard kills slowed later
    subscribers from 1.5 s to 4 s and eventually stalled a large reliable
    topic (an `Image` publisher), while an interrupted CLI did not.
    """
    pgid = os.getpgid(proc.pid)
    os.killpg(pgid, signal.SIGINT)
    deadline = time.monotonic() + _GRACE_SEC
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break
        time.sleep(0.05)
    os.killpg(pgid, signal.SIGKILL)
