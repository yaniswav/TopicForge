"""Run the sampler helper: start it, read its frames, stop it so its reader leaves the graph.

Sibling of `echo_stream` for the `rclpy` backend of `sample_messages`, and it returns the same
`EchoRun`, so decoding, notes and rates are the same code on both backends. The stop differs. A
reader that is killed stays matched on its writers, and the next start on that topic gets slower
(about 2 s on Fast DDS). So the normal stop closes the helper's stdin and waits for it to leave;
the tree kill is the last resort.

The helper runs under the distro's interpreter, not the server's (`common/ros_python.py`), and is
started by path, so `rclpy` is never imported here.
"""

# Deliberate exception to "subprocess goes through `_exec`/`_run`", like `stream_echo`: the pipes
# stay open while they are read. It stays bounded: the read stops at `count` messages or
# `deadline_s`, the helper stops itself at its own deadline or on stdin EOF, and the `finally`
# below always ends the process.

from __future__ import annotations

import contextlib
import logging
import os
import queue
import shutil
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Literal, Protocol

from topicforge.adapters.base import AdapterError
from topicforge.adapters.common.ros_python import choose_ros_python
from topicforge.adapters.common.sampler_frames import (
    ENCODING_YAML,
    KIND_DROPPED,
    KIND_MESSAGE,
    Frame,
    FrameError,
    read_frame,
)
from topicforge.adapters.common.spawned_pids import record_spawn
from topicforge.adapters.ros2_live.echo_stream import EchoDocument, EchoRun
from topicforge.adapters.ros2_live.process_runner import run_process
from topicforge.adapters.ros2_live.process_tree import (
    JobObject,
    kill_process_tree,
    spawn_options,
)

log = logging.getLogger(__name__)

HELPER_PATH = Path(__file__).with_name("_sampler_helper.py")
HELPER_LABEL = "the sampling helper"

# Above the helper's own grace (0.8 s), so a stuck `rclpy` ends itself first.
_STOP_WAIT_SEC = 1.2
_JOIN_TIMEOUT_SEC = 2.0
_PROBE_TIMEOUT_SEC = 5.0
_STDERR_TAIL_LINES = 5
_STDERR_KEPT_LINES = 50
_REASON_MAX_CHARS = 300
# The helper's exits on `count`, on its deadline and on stdin EOF: it ended as asked.
_NORMAL_EXITS = frozenset({0, 3, 4})
# `EchoRun.exit_code` when the output is not frames (no process returned it).
EXIT_BAD_FRAMES = 70

SamplerKind = Literal["cli", "rclpy", "unavailable"]


class _Process(Protocol):
    """The part of `subprocess.Popen` the runner uses."""

    pid: int
    stdin: IO[bytes] | None
    stdout: IO[bytes] | None
    stderr: IO[bytes] | None

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def kill(self) -> None: ...


PopenFactory = Callable[..., _Process]


@dataclass(frozen=True)
class SamplingBackend:
    """What `sample_messages` runs on; chosen once by the factory.

    `cli` is `ros2 topic echo`; `rclpy` is the helper under `python`, and `fallback` lets a call
    whose helper failed retry on the CLI (`auto`); `unavailable` is `rclpy` requested and
    unusable: `sample_messages` raises `note` rather than hide it behind the CLI.
    """

    kind: SamplerKind = "cli"
    python: str | None = None
    fallback: bool = False
    note: str | None = None


def helper_command(python: str, topic: str, message_type: str, flags: list[str]) -> list[str]:
    """The helper's command line. No `-I`: the distro's packages come from `PYTHONPATH`."""
    return [python, str(HELPER_PATH), "--type", message_type, *flags, topic]


def stream_helper(
    cmd: list[str],
    *,
    count: int,
    deadline_s: float,
    popen: PopenFactory = subprocess.Popen,
) -> EchoRun:
    """Run `cmd` and return up to `count` messages within `deadline_s` seconds.

    The helper is always gone before returning. Raises `AdapterError` when it cannot start.
    `popen` is injectable for tests.
    """
    try:
        proc = popen(
            cmd,
            stdin=subprocess.PIPE,  # held open: EOF is the stop signal
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"},
            **spawn_options(),
        )
    except OSError as exc:
        raise AdapterError(
            f"could not start {HELPER_LABEL} with `{os.path.basename(cmd[0])}`: "
            f"{exc.strerror or type(exc).__name__}"
        ) from exc
    record_spawn(proc.pid)
    job = JobObject.attach(proc.pid)
    frames: queue.Queue[Frame | FrameError | None] = queue.Queue()
    stderr_lines: deque[str] = deque(maxlen=_STDERR_KEPT_LINES)
    threads = [
        threading.Thread(target=_pump_frames, args=(proc.stdout, frames), daemon=True),
        threading.Thread(target=_pump_stderr, args=(proc.stderr, stderr_lines), daemon=True),
    ]
    for thread in threads:
        thread.start()

    run = EchoRun(started_ns=time.time_ns(), tool=HELPER_LABEL)
    try:
        _collect(run, proc, frames, count, deadline_s)
        run.ended_ns = time.time_ns()
    finally:
        stop_helper(proc, job)
        for thread in threads:
            thread.join(timeout=_JOIN_TIMEOUT_SEC)
        if any(thread.is_alive() for thread in threads):
            log.warning("sampling helper pipes still open after it stopped")
    tail = [ln for ln in stderr_lines if ln.strip()][-_STDERR_TAIL_LINES:]
    run.stderr_tail = " | ".join(filter(None, [run.stderr_tail, *(ln.strip() for ln in tail)]))
    return run


def stop_helper(proc: _Process, job: JobObject | None) -> bool:
    """Close stdin and wait for a clean exit; `False` when it took the tree kill.

    Only the clean exit removes the reader from the writers at once.
    """
    if proc.stdin is not None:
        with contextlib.suppress(OSError, ValueError):
            proc.stdin.close()
    try:
        proc.wait(timeout=_STOP_WAIT_SEC)
    except subprocess.TimeoutExpired:
        log.warning("sampling helper ignored stdin EOF: killing it, its reader may linger")
        kill_process_tree(proc, job)
        return False
    if job is not None:
        job.release()
    return True


def _collect(
    run: EchoRun,
    proc: _Process,
    frames: queue.Queue[Frame | FrameError | None],
    count: int,
    budget_s: float,
) -> None:
    """Fill `run` from the frames until `count` documents, the budget, or the end of output."""
    end = time.monotonic() + budget_s
    while len(run.documents) < count:
        remaining = end - time.monotonic()
        if remaining <= 0:
            return
        try:
            item = frames.get(timeout=remaining)
        except queue.Empty:
            return
        if item is None:
            with contextlib.suppress(subprocess.TimeoutExpired):
                code = proc.wait(timeout=_JOIN_TIMEOUT_SEC)
                run.exit_code = None if code in _NORMAL_EXITS else code
            return
        if isinstance(item, FrameError):
            run.exit_code = EXIT_BAD_FRAMES
            run.stderr_tail = f"bad output: {item}"
            return
        _add_frame(run, item)


def _add_frame(run: EchoRun, frame: Frame) -> None:
    """Turn one frame into what the echo stream collects."""
    if frame.kind == KIND_DROPPED:
        run.oversized += 1
        run.oversized_received_ns.append(frame.wall_ns or time.time_ns())
    elif frame.kind == KIND_MESSAGE and frame.encoding == ENCODING_YAML:
        text = frame.body.decode("utf-8", errors="replace")
        run.documents.append(EchoDocument(text, frame.wall_ns or time.time_ns()))


def _pump_frames(stream: IO[bytes] | None, out: queue.Queue[Frame | FrameError | None]) -> None:
    """Forward each frame; `None` marks the end of the stream."""
    try:
        while stream is not None:
            frame = read_frame(stream)
            if frame is None:
                return
            out.put(frame)
    except FrameError as exc:
        out.put(exc)
        # Keep draining: a helper blocked on a full pipe could not exit.
        with contextlib.suppress(OSError, ValueError):
            while stream is not None and stream.read(65536):
                pass
    except (OSError, ValueError):
        pass  # pipe closed by the kill
    finally:
        out.put(None)


def _pump_stderr(stream: IO[bytes] | None, out: deque[str]) -> None:
    """Drain stderr so a chatty process cannot block on a full pipe."""
    try:
        if stream is not None:
            for raw in iter(stream.readline, b""):
                out.append(raw.decode("utf-8", errors="replace"))
    except (OSError, ValueError):
        pass


def choose_backend(requested: str, executable: str, override: str | None) -> SamplingBackend:
    """Resolve `TOPICFORGE_ROS_SAMPLER`, running the helper's `--probe` once for `rclpy` and `auto`.

    `auto` falls back to the CLI with the reason in `note`; `rclpy` becomes `unavailable`, so a
    silent fallback cannot pass for the helper. Never raises: the factory calls this at startup.
    """
    if requested == "cli":
        return SamplingBackend("cli", note="TOPICFORGE_ROS_SAMPLER=cli")
    python, how = choose_ros_python(override, shutil.which(executable), which=shutil.which)
    reason = how if python is None else _probe(python, how)
    if reason is None:
        return SamplingBackend("rclpy", python=python, fallback=requested == "auto")
    if requested == "auto":
        return SamplingBackend("cli", note=f"rclpy sampler not usable: {reason}")
    return SamplingBackend(
        "unavailable",
        note=f"TOPICFORGE_ROS_SAMPLER=rclpy, but the rclpy sampler is not usable: {reason}",
    )


def _probe(python: str, how: str) -> str | None:
    """Why the helper cannot run under `python`, `None` when it can."""
    try:
        result = run_process(
            [python, str(HELPER_PATH), "--probe"],
            deadline_s=_PROBE_TIMEOUT_SEC,
        )
    except OSError as exc:
        return f"{python} ({how}): {exc.strerror or type(exc).__name__}"
    if result.timed_out:
        return f"{python} ({how}): no answer within {_PROBE_TIMEOUT_SEC:g} s"
    if result.returncode == 0:
        return None
    lines = [ln.strip() for ln in result.stderr.splitlines() if ln.strip()]
    tail = lines[-1] if lines else f"exit {result.returncode}"
    return f"{python} ({how}): {tail}"[:_REASON_MAX_CHARS]


__all__ = [
    "EXIT_BAD_FRAMES",
    "HELPER_LABEL",
    "HELPER_PATH",
    "SamplingBackend",
    "choose_backend",
    "helper_command",
    "stop_helper",
    "stream_helper",
]
