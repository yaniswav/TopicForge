"""Run `ros2 topic echo` as a stream: collect messages until a count or a deadline.

`echo` has no `--times` option on Humble or Rolling and prints until killed,
so the runner reads its stdout in a thread, stops at the first of N messages
or the wall deadline, and kills the whole process tree (see `process_tree`).
A message longer than a character cap is dropped while it streams, so a huge
`Image` is never held in memory or handed to the YAML parser.
"""

from __future__ import annotations

import contextlib
import logging
import os
import queue
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import IO, Protocol

from topicforge.adapters.base import AdapterError
from topicforge.adapters.ros2_live.echo_parser import ECHO_DOCUMENT_SEPARATOR, join_document_lines
from topicforge.adapters.ros2_live.process_tree import (
    JobObject,
    kill_process_tree,
    spawn_options,
)

__all__ = ["EchoDocument", "EchoRun", "kill_process_tree", "stream_echo"]

log = logging.getLogger(__name__)

_JOIN_TIMEOUT_SEC = 2.0
_STDERR_TAIL_LINES = 5
_STDERR_KEPT_LINES = 50


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
    run was stopped, `None` when the runner stopped it. `oversized` counts the
    messages dropped for exceeding the character cap.
    """

    documents: list[EchoDocument] = field(default_factory=list)
    exit_code: int | None = None
    stderr_tail: str = ""
    oversized: int = 0
    # Wall-clock arrival of each dropped message, so a rate can still count it.
    oversized_received_ns: list[int] = field(default_factory=list)
    # Wall clock when collection began and when it stopped (before the kill).
    started_ns: int = 0
    ended_ns: int = 0


def stream_echo(
    cmd: list[str],
    *,
    count: int,
    deadline_s: float,
    max_document_chars: int | None = None,
    popen: PopenFactory = subprocess.Popen,
) -> EchoRun:
    """Run `cmd` and return up to `count` messages, within `deadline_s` seconds.

    A message over `max_document_chars` is dropped and counted in
    `EchoRun.oversized`. The process is always stopped (with its children)
    before returning; stopping takes up to about two more seconds.
    `popen` is injectable for tests. Raises `AdapterError` when
    the process cannot be started.
    """
    try:
        proc = popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"},
            **spawn_options(),
        )
    except OSError as exc:
        raise AdapterError(
            f"could not start `{os.path.basename(cmd[0])}`: {exc.strerror or type(exc).__name__}"
        ) from exc
    job = JobObject.attach(proc.pid)
    lines: queue.Queue[tuple[str, int] | None] = queue.Queue()
    stderr_lines: deque[str] = deque(maxlen=_STDERR_KEPT_LINES)
    threads = [
        threading.Thread(target=_pump_stdout, args=(proc.stdout, lines), daemon=True),
        threading.Thread(target=_pump_stderr, args=(proc.stderr, stderr_lines), daemon=True),
    ]
    for thread in threads:
        thread.start()

    run = EchoRun(started_ns=time.time_ns())
    try:
        _collect(run, proc, lines, count, deadline_s, max_document_chars)
        run.ended_ns = time.time_ns()
    finally:
        kill_process_tree(proc, job)
        for thread in threads:
            thread.join(timeout=_JOIN_TIMEOUT_SEC)
        if any(thread.is_alive() for thread in threads):
            log.warning("echo output pipes still open after the kill: a child process survived")
    tail = [ln for ln in stderr_lines if ln.strip()][-_STDERR_TAIL_LINES:]
    run.stderr_tail = " | ".join(ln.strip() for ln in tail)
    return run


def _collect(
    run: EchoRun,
    proc: _Process,
    lines: queue.Queue[tuple[str, int] | None],
    count: int,
    budget_s: float,
    max_chars: int | None,
) -> None:
    """Fill `run` from the line queue until `count` documents, the budget, or end of output."""
    end = time.monotonic() + budget_s
    current: list[str] = []
    size = 0
    dropping = False
    while len(run.documents) < count:
        remaining = end - time.monotonic()
        if remaining <= 0:
            return
        try:
            item = lines.get(timeout=remaining)
        except queue.Empty:
            return
        if item is None:
            # A process that closed its output but still runs is killed afterwards.
            with contextlib.suppress(subprocess.TimeoutExpired):
                run.exit_code = proc.wait(timeout=_JOIN_TIMEOUT_SEC)
            return
        line, received_ns = item
        if line.rstrip() == ECHO_DOCUMENT_SEPARATOR:
            if not dropping:
                run.documents.append(EchoDocument(join_document_lines(current), received_ns))
            else:
                run.oversized_received_ns.append(received_ns)
            current, size, dropping = [], 0, False
        elif not dropping:
            size += len(line)
            if max_chars is not None and size > max_chars:
                run.oversized += 1
                current, dropping = [], True
            else:
                current.append(line.rstrip("\r\n"))


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


def _pump_stderr(stream: IO[str] | None, out: deque[str]) -> None:
    """Drain stderr so a chatty process cannot block on a full pipe."""
    try:
        if stream is not None:
            for line in iter(stream.readline, ""):
                out.append(line)
    except (OSError, ValueError):
        pass
