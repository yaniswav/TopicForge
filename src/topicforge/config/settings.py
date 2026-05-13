"""Runtime settings, resolved from environment variables.

Settings are immutable and constructed once at startup. The `auto` mode is
resolved against the current environment by `Settings.effective_mode` —
keeping that decision in one place avoids drift between callers.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from typing import Literal

Mode = Literal["mock", "live", "auto"]
ResolvedMode = Literal["mock", "live"]

_VALID_MODES: tuple[Mode, ...] = ("mock", "live", "auto")
_VALID_LOG_LEVELS: tuple[str, ...] = ("DEBUG", "INFO", "WARNING", "ERROR")


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable runtime configuration."""

    mode: Mode
    log_level: str
    ros2_executable: str

    @property
    def effective_mode(self) -> ResolvedMode:
        """Resolve `auto` against the current environment.

        `live` and `mock` are returned as-is; `auto` becomes `live` when the
        configured ROS2 executable is on PATH, otherwise `mock`. The factory
        in `services/factory.py` is responsible for final fallback if a live
        adapter cannot actually start.

        Predictive resolution only. Final operational fallback (when the live
        adapter is instantiable but cannot actually start) lives in
        `services/factory.py:build_adapter`.
        """
        if self.mode == "auto":
            return "live" if shutil.which(self.ros2_executable) else "mock"
        return self.mode


def load_settings(env: dict[str, str] | os._Environ[str] | None = None) -> Settings:
    """Build a Settings from the given environment (defaults to `os.environ`).

    The `env` parameter is injectable so tests can avoid leaking process
    state and pin behavior deterministically.
    """
    src = env if env is not None else os.environ

    raw_mode = src.get("TOPICFORGE_MODE", "auto").strip().lower()
    if raw_mode not in _VALID_MODES:
        raise ValueError(f"Invalid TOPICFORGE_MODE={raw_mode!r}; expected one of {_VALID_MODES}")

    raw_log = src.get("TOPICFORGE_LOG_LEVEL", "INFO").strip().upper()
    if raw_log not in _VALID_LOG_LEVELS:
        raise ValueError(
            f"Invalid TOPICFORGE_LOG_LEVEL={raw_log!r}; expected one of {_VALID_LOG_LEVELS}"
        )

    ros2_exe = src.get("TOPICFORGE_ROS2_BIN", "ros2").strip() or "ros2"

    return Settings(mode=raw_mode, log_level=raw_log, ros2_executable=ros2_exe)
