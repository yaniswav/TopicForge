"""Start a process so its whole tree can be stopped, and stop it.

The `ros2` entry point is a launcher: killing only the launcher can leave the
real process running, and an orphan keeps the output pipes open. POSIX uses a
process group of its own (`start_new_session`); Windows puts the process in a
Job Object that kills every member when terminated, which also reaches
children whose parent has already exited (`taskkill /T` cannot).
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import subprocess
import sys
import time
from typing import Any, Protocol

log = logging.getLogger(__name__)

GRACE_SEC = 1.5
_WAIT_SEC = 2.0
_GROUP_POLL_SEC = 0.05


class Killable(Protocol):
    """The part of `subprocess.Popen` the tree kill uses."""

    pid: int

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def kill(self) -> None: ...


def spawn_options() -> dict[str, Any]:
    """`Popen` options that make the process tree killable as a unit."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}


class JobObject:
    """A Windows Job Object that kills all its processes when terminated or closed."""

    def __init__(self, handle: int, kernel32: Any, clear_kill_on_close: Any = None) -> None:
        self._handle = handle
        self._k32 = kernel32
        self._clear_kill_on_close = clear_kill_on_close

    @classmethod
    def attach(cls, pid: int) -> JobObject | None:
        """Put process `pid` in a new kill-on-close job; `None` when that is not possible."""
        if sys.platform != "win32":
            return None
        try:
            return _create_job(pid)
        except (OSError, AttributeError, ValueError) as exc:
            log.warning("could not attach a Windows job object (%s); orphans may survive", exc)
            return None

    def terminate(self) -> None:
        """Kill every process of the job, then release the job."""
        if self._handle:
            self._k32.TerminateJobObject(self._handle, 1)
            self._k32.CloseHandle(self._handle)
            self._handle = 0

    def release(self) -> None:
        """Close the job without killing its members (a clean exit leaves a daemon alone)."""
        if self._handle:
            if self._clear_kill_on_close is not None:
                self._clear_kill_on_close(self._handle)
            self._k32.CloseHandle(self._handle)
            self._handle = 0


def _create_job(pid: int) -> JobObject | None:
    import ctypes
    from ctypes import wintypes

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ("r", "w", "o", "rb", "wb", "ob")]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.OpenProcess.restype = wintypes.HANDLE
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
    limits = _ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = kernel32.SetInformationJobObject(
        wintypes.HANDLE(job), 9, ctypes.byref(limits), ctypes.sizeof(limits)
    )
    process = kernel32.OpenProcess(0x0101, False, pid)  # SET_QUOTA | TERMINATE
    assigned = bool(ok and process) and kernel32.AssignProcessToJobObject(
        wintypes.HANDLE(job), wintypes.HANDLE(process)
    )
    if process:
        kernel32.CloseHandle(wintypes.HANDLE(process))
    if not assigned:
        kernel32.CloseHandle(wintypes.HANDLE(job))
        return None

    def clear_kill_on_close(handle: int) -> None:
        cleared = _ExtendedLimits()
        kernel32.SetInformationJobObject(
            wintypes.HANDLE(handle), 9, ctypes.byref(cleared), ctypes.sizeof(cleared)
        )

    return JobObject(job, kernel32, clear_kill_on_close)


def kill_process_tree(proc: Killable, job: JobObject | None = None) -> None:
    """Stop `proc` and every descendant, even when `proc` itself already exited.

    POSIX: SIGINT the process group, then SIGKILL it. Windows: terminate the
    job, or `taskkill /F /T` when there is none.
    """
    try:
        if sys.platform == "win32":
            _kill_windows(proc, job)
        else:
            _stop_posix_group(proc)
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("process tree kill failed: %s", exc)
    try:
        proc.kill()
        proc.wait(timeout=_WAIT_SEC)
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("process kill failed: %s", exc)


def _kill_windows(proc: Killable, job: JobObject | None) -> None:
    if job is not None:
        job.terminate()
        return
    if proc.poll() is not None:
        log.warning("launcher already exited and no job object: its children may survive")
    subprocess.run(
        ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
        capture_output=True,
        check=False,
        timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def _stop_posix_group(proc: Killable) -> None:
    """SIGINT the process group so the CLI leaves the DDS graph cleanly, then SIGKILL it.

    A subscriber that is killed outright stays matched on the publisher.
    Measured on Humble / Fast DDS: repeated hard kills slowed later
    subscribers from 1.5 s to 4 s and eventually stalled a large reliable
    topic (an `Image` publisher), while an interrupted CLI did not. The group
    id is the process id (`start_new_session`); the group is signalled even
    when the launcher is gone, since its children may not be.
    """
    pgid = proc.pid
    if pgid <= 1 or pgid == os.getpgrp():
        # Never signal init's group or our own: only a child started with
        # `start_new_session` leads a group we may stop.
        log.warning("refusing to signal process group %s", pgid)
        return
    try:
        os.killpg(pgid, signal.SIGINT)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + GRACE_SEC
    while time.monotonic() < deadline:
        proc.poll()  # reaps the launcher so the group can empty
        if not _group_alive(pgid):
            return
        time.sleep(_GROUP_POLL_SEC)
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pgid, signal.SIGKILL)


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
