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
    KIND_ARRIVAL,
    KIND_DROPPED,
    KIND_HELLO,
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
# `EchoRun.exit_code` values no process returned: output that is not frames, and a helper that
# never said it subscribed.
EXIT_BAD_FRAMES = 70
EXIT_STALLED = 71
_NOT_PROCESS_EXITS = frozenset({EXIT_BAD_FRAMES, EXIT_STALLED})
# Time a call that can still fall back gives the helper to subscribe: normally 0.2-0.4 s.
_HELLO_MIN_SEC = 1.0
_HELLO_MAX_SEC = 5.0
_HELLO_SHARE = 1 / 3
# Told to whoever reads why the helper is not used: the check does not run again.
_CHECKED_ONCE = " Checked once at startup: restart the server to check again."

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


def hello_bound(deadline_s: float, *, can_fall_back: bool) -> float:
    """Seconds the helper gets to say it subscribed.

    With a fallback, a share of the budget, so the CLI still has time. Without one there is
    nothing to save time for: the whole budget.
    """
    if not can_fall_back:
        return deadline_s
    share = max(_HELLO_MIN_SEC, deadline_s * _HELLO_SHARE)
    return min(deadline_s, _HELLO_MAX_SEC, share)


def failure_text(run: EchoRun) -> str:
    """What went wrong with a helper run, for a message or a note."""
    how = "" if run.exit_code in _NOT_PROCESS_EXITS else f" (exit {run.exit_code})"
    return f"{run.tool} failed{how}: {run.stderr_tail or 'no stderr'}"


def stream_helper(
    cmd: list[str],
    *,
    count: int,
    deadline_s: float,
    hello_s: float | None = None,
    window: bool = False,
    popen: PopenFactory = subprocess.Popen,
) -> EchoRun:
    """Run `cmd` and return up to `count` messages within `deadline_s` seconds.

    With `window` the helper runs `--window` and `count` does not stop the read: the run ends
    when the helper does, holds a bounded evenly spread subset (see `WindowStride` in the helper)
    and the arrival of every message, and its span starts when the helper subscribed.

    A helper that has not said it subscribed within `hello_s` (default the whole budget) is
    stalled, not a silent topic: `exit_code` is `EXIT_STALLED` and `stderr_tail` says so.
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

    run = EchoRun(started_ns=time.time_ns(), tool=HELPER_LABEL, window=window)
    try:
        _collect(run, proc, frames, count, deadline_s, deadline_s if hello_s is None else hello_s)
        run.ended_ns = time.time_ns()
        if run.hello_mono_ns:
            run.ended_ns = run.started_ns + time.monotonic_ns() - run.hello_mono_ns
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
    hello_s: float,
) -> None:
    """Fill `run` from the frames until `count` documents, the budget, or the end of output.

    Until the hello frame the wait is `hello_s`, and nothing but the hello tells a helper that
    is waiting on a silent topic from one that hangs in `rclpy`.
    """
    started = time.monotonic()
    end = started + budget_s
    hello_end = started + min(hello_s, budget_s)
    greeted = False
    while run.window or len(run.documents) < count:
        remaining = (end if greeted else hello_end) - time.monotonic()
        try:
            if remaining <= 0:
                raise queue.Empty
            item = frames.get(timeout=remaining)
        except queue.Empty:
            if not greeted:
                _mark_stalled(run, time.monotonic() - started)
            return
        if item is None:
            with contextlib.suppress(subprocess.TimeoutExpired):
                code = proc.wait(timeout=_JOIN_TIMEOUT_SEC)
                run.exit_code = None if code in _NORMAL_EXITS else code
            if run.exit_code is None and not greeted:
                _mark_stalled(run, time.monotonic() - started)
            return
        if isinstance(item, FrameError):
            run.exit_code = EXIT_BAD_FRAMES
            run.stderr_tail = f"bad output: {item}"
            return
        greeted = greeted or item.kind == KIND_HELLO
        _add_frame(run, item)


def _mark_stalled(run: EchoRun, waited_s: float) -> None:
    """No hello: the topic was never subscribed to, so silence says nothing about it."""
    run.exit_code = EXIT_STALLED
    run.stderr_tail = (
        f"stalled before subscribing, no sign of life after {waited_s:.1f} s "
        "(`rclpy` hanging in its import or init, or a very slow start)"
    )


def _add_frame(run: EchoRun, frame: Frame) -> None:
    """Turn one frame into what the echo stream collects."""
    if frame.kind == KIND_HELLO and run.window and frame.wall_ns:
        run.started_ns = frame.wall_ns
        run.hello_mono_ns = time.monotonic_ns()
    elif frame.kind == KIND_ARRIVAL:
        run.unkept_received_ns.append(frame.wall_ns or time.time_ns())
    elif frame.kind == KIND_DROPPED:
        run.oversized += 1
        run.oversized_received_ns.append(frame.wall_ns or time.time_ns())
    elif frame.kind == KIND_MESSAGE and frame.encoding == ENCODING_YAML:
        text = frame.body.decode("utf-8", errors="replace")
        run.documents.append(EchoDocument(text, frame.wall_ns or time.time_ns(), frame.index))
        if frame.stride != run.stride:
            run.stride = frame.stride
            _keep_stride(run)


def _keep_stride(run: EchoRun) -> None:
    """Drop the documents the window's stride no longer selects; their arrivals still count."""
    kept: list[EchoDocument] = []
    for doc in run.documents:
        if doc.index % run.stride:
            run.unkept_received_ns.append(doc.received_ns)
        else:
            kept.append(doc)
    run.documents = kept


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
        return SamplingBackend("cli", note=f"rclpy sampler not usable: {reason}.{_CHECKED_ONCE}")
    return SamplingBackend(
        "unavailable",
        note=(
            f"TOPICFORGE_ROS_SAMPLER=rclpy, but the rclpy sampler is not usable: {reason}."
            f"{_CHECKED_ONCE}"
        ),
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
    "EXIT_STALLED",
    "HELPER_LABEL",
    "HELPER_PATH",
    "SamplingBackend",
    "choose_backend",
    "failure_text",
    "hello_bound",
    "helper_command",
    "stop_helper",
    "stream_helper",
]
