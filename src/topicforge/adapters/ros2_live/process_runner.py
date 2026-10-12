"""Run a short-lived CLI command under a hard deadline, with bounded output.

This is the one place the `ros2` adapter starts a process to completion
(`stream_echo` is the streaming sibling). `subprocess.run(timeout=...)` is not
enough: the stock `ros2` launcher on Windows leaves an orphan that keeps the
output pipes open, so the call can block for ever after the timeout fires. The
runner starts the process in its own kill unit (POSIX process group, Windows
Job Object, see `process_tree`), reads both pipes in threads, and on the
deadline kills the whole tree before returning. Output beyond a byte cap is
drained and discarded so a runaway child can neither fill memory nor block on
a full pipe.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import IO

from topicforge.adapters.ros2_live.process_tree import (
    JobObject,
    kill_process_tree,
    spawn_options,
)

log = logging.getLogger(__name__)

# Default cap per stream: far above any `ros2` listing, small enough to be harmless.
DEFAULT_MAX_OUTPUT_BYTES = 8 * 1024 * 1024
_CHUNK = 65536
_JOIN_AFTER_KILL_SEC = 2.0


@dataclass(frozen=True)
class ProcessResult:
    """Outcome of one run. `returncode` is `None` when the deadline killed the process."""

    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool
    truncated: bool


class _Capture:
    """Reads one pipe in a thread, keeping at most `cap` bytes and draining the rest."""

    def __init__(self, stream: IO[bytes] | None, cap: int) -> None:
        self._stream = stream
        self._cap = cap
        self._chunks: list[bytes] = []
        self._size = 0
        self.truncated = False
        self.thread = threading.Thread(target=self._pump, daemon=True)

    def _pump(self) -> None:
        stream = self._stream
        if stream is None:
            return
        try:
            while True:
                chunk = stream.read1(_CHUNK)  # type: ignore[attr-defined]
                if not chunk:
                    return
                room = self._cap - self._size
                if room > 0:
                    self._chunks.append(chunk[:room])
                    self._size += min(len(chunk), room)
                if len(chunk) > room:
                    self.truncated = True
        except (OSError, ValueError):
            pass  # pipe closed by the kill

    def text(self) -> str:
        return b"".join(self._chunks).decode("utf-8", errors="replace")


def run_process(
    cmd: list[str],
    *,
    deadline_s: float,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> ProcessResult:
    """Run `cmd` to completion or kill it (and its children) after `deadline_s` seconds.

    Raises `OSError` (`FileNotFoundError`, `PermissionError`, ...) when the
    process cannot be started; the caller maps it to a user-facing message.
    The deadline covers the process and the closing of its pipes: a launcher
    that exited while an orphan still holds a pipe counts as timed out, and the
    orphan is killed. On a clean exit the process tree is left alone (the
    `ros2` CLI may have started its daemon, which must survive).
    """
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"},
        **spawn_options(),
    )
    job = JobObject.attach(proc.pid)
    out = _Capture(proc.stdout, max_output_bytes)
    err = _Capture(proc.stderr, max_output_bytes)
    out.thread.start()
    err.thread.start()

    end = time.monotonic() + max(deadline_s, 0.0)
    timed_out = False
    try:
        try:
            proc.wait(timeout=max(end - time.monotonic(), 0.0))
        except subprocess.TimeoutExpired:
            timed_out = True
        if not timed_out:
            for capture in (out, err):
                capture.thread.join(timeout=max(end - time.monotonic(), 0.0))
            timed_out = out.thread.is_alive() or err.thread.is_alive()
    finally:
        if timed_out or proc.poll() is None:
            kill_process_tree(proc, job)
        elif job is not None:
            job.release()
        for capture in (out, err):
            capture.thread.join(timeout=_JOIN_AFTER_KILL_SEC)
    if out.thread.is_alive() or err.thread.is_alive():
        log.warning("process output pipes still open after the kill: a child process survived")

    return ProcessResult(
        returncode=None if timed_out else proc.returncode,
        stdout=out.text(),
        stderr=err.text(),
        timed_out=timed_out,
        truncated=out.truncated or err.truncated,
    )


__all__ = ["DEFAULT_MAX_OUTPUT_BYTES", "ProcessResult", "run_process"]
