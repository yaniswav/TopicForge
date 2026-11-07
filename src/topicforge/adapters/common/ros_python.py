"""Which Python runs the sampler helper: `rclpy` is tied to the distro's interpreter. Pure.

The server may run under another minor version (`uvx --python 3.12` on Humble), so the helper
runs under `TOPICFORGE_ROS_PYTHON`, else the interpreter in the shebang of `ros2`, else `python3`.
"""

from __future__ import annotations

import ntpath
from collections.abc import Callable
from pathlib import Path

_MAX_SHEBANG_BYTES = 4096


def parse_shebang(first_line: str) -> str | None:
    """The Python a `#!` line names (a path, or a bare name after `env`), else `None`.

    `None` for no shebang, for a shell (the launcher then is not a Python script) and for an
    empty line. Handles `#!/usr/bin/env python3`, `env -S python3 -u`, interpreter flags and the
    quoted paths pip writes on Windows.
    """
    if not first_line.startswith("#!"):
        return None
    text = first_line[2:].strip()
    if not text:
        return None
    if text[0] == '"':
        program, _, _rest = text[1:].partition('"')
        words = [program]
    else:
        words = text.split()
    if _base_name(words[0]) == "env":
        words = [w for w in words[1:] if not w.startswith("-") and "=" not in w]
        if not words:
            return None
    name = _base_name(words[0])
    return words[0] if name.startswith(("python", "pypy")) else None


def _base_name(path: str) -> str:
    """Last component of a POSIX or Windows path, lowercase, whatever the running OS."""
    return ntpath.basename(path).lower()


def read_shebang(path: str) -> str | None:
    """The Python named by the first line of the file at `path`, `None` when there is none."""
    try:
        with Path(path).open("rb") as handle:
            head = handle.read(_MAX_SHEBANG_BYTES)
    except OSError:
        return None
    return parse_shebang(head.split(b"\n", 1)[0].decode("utf-8", "replace"))


def choose_ros_python(
    override: str | None,
    ros2_path: str | None,
    *,
    which: Callable[[str], str | None],
    shebang: Callable[[str], str | None] = read_shebang,
) -> tuple[str | None, str]:
    """`(interpreter, how it was found)`; the interpreter is `None` when none was found.

    An `override` that does not resolve is not replaced by a guess: it stays the answer, and
    starting it fails with a reason that names it.
    """
    if override:
        return which(override) or override, "TOPICFORGE_ROS_PYTHON"
    named = shebang(ros2_path) if ros2_path else None
    if named:
        resolved = which(named)
        if resolved:
            return resolved, "the shebang of `ros2`"
    for name in ("python3", "python"):
        resolved = which(name)
        if resolved:
            return resolved, f"`{name}` on PATH"
    return None, "no interpreter found: set TOPICFORGE_ROS_PYTHON"


__all__ = ["choose_ros_python", "parse_shebang", "read_shebang"]
