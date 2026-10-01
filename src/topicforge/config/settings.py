"""Runtime settings, resolved from environment variables.

Settings are immutable and constructed once at startup. The `auto` mode is
resolved against the current environment by `Settings.effective_mode`:
keeping that decision in one place avoids drift between callers.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
from dataclasses import dataclass
from typing import Literal

Mode = Literal["mock", "live", "auto"]
ResolvedMode = Literal["mock", "live"]

DdsBackend = Literal["mock", "cyclone", "fast", "opendds", "dust", "auto"]
ResolvedDdsBackend = Literal["mock", "cyclone", "fast", "opendds", "dust"]

_VALID_MODES: tuple[Mode, ...] = ("mock", "live", "auto")
_VALID_DDS_BACKENDS: tuple[DdsBackend, ...] = (
    "mock",
    "cyclone",
    "fast",
    "opendds",
    "dust",
    "auto",
)
# Commercial vendor identifiers that 0.5.2 and earlier accepted for the Pro
# tier. The tier is retired; they are rejected with a dedicated message
# instead of the generic "invalid value" one so an existing deployment learns
# why its configuration stopped working.
_REMOVED_DDS_BACKENDS: frozenset[str] = frozenset({"rti", "opensplice", "coredx", "intercom"})
_VALID_LOG_LEVELS: tuple[str, ...] = ("DEBUG", "INFO", "WARNING", "ERROR")
# Telemetry is strict opt-in: any value other than the explicit on-set
# resolves to off. We accept the common affirmatives so users can flip the
# flag without consulting the docs, but anything ambiguous stays off.
_TELEMETRY_ON_VALUES: frozenset[str] = frozenset({"on", "1", "true", "yes", "enabled"})
_TELEMETRY_OFF_VALUES: frozenset[str] = frozenset({"", "off", "0", "false", "no", "disabled"})

_DDS_DOMAIN_MIN = 0
_DDS_DOMAIN_MAX = 232

# Canonical vendor -> Python module mapping used by both `auto` resolution
# (this file) and `HealthService` (`services/health.py`). Defined here so
# the two callers cannot drift.
_DDS_BACKEND_MODULES: dict[str, str] = {
    "opendds": "pyopendds",
    "fast": "fastdds",
    "cyclone": "cyclonedds",
    "dust": "dust_dds_python",
}

# Auto-detect priority order, evaluated by `effective_dds_backend` when
# `dds_backend == "auto"`. First entry whose module is importable wins ; the
# chain terminates at `"mock"` which is always available.
#
# Only backends with a working adapter belong here. `opendds` and `dust` are
# permanent stubs whose `is_available()` is always False: `pyopendds` exists
# on PyPI, so an auto chain that listed it would pick the stub, find it
# unavailable and never try Cyclone. They stay selectable explicitly.
# Fast > Cyclone preserves the v0.3.0 order.
_DDS_AUTO_DETECT_ORDER: tuple[str, ...] = ("fast", "cyclone")


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable runtime configuration."""

    mode: Mode
    log_level: str
    ros2_executable: str
    telemetry_enabled: bool
    # DDS module knobs: added in v0.2.0. Defaults keep backward-compat
    # with code constructing `Settings(...)` positionally before v0.2.0.
    dds_backend: DdsBackend = "mock"
    dds_domain_id: int = 0

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

    @property
    def effective_dds_backend(self) -> ResolvedDdsBackend:
        """Resolve the DDS backend against the current environment.

        - Explicit `TOPICFORGE_MODE=mock` forces the DDS backend to `mock`:
          mock global mode means no live access of any kind.
        - Explicit backends (`mock`, `cyclone`, `fast`, `opendds`, `dust`)
          are returned as-is otherwise, including in `auto` mode when `ros2`
          is not on PATH: a user who names a DDS backend wants it with or
          without a ROS2 install, and silently serving fixtures instead
          would hide the misconfiguration.
        - `auto` walks `_DDS_AUTO_DETECT_ORDER` in priority order and
          returns the first vendor whose Python module is importable on
          this host, ending at `mock`. `auto` is never the default (the
          default is `mock`), so it always expresses an explicit request
          for DDS and the chain is walked whether or not `ros2` is on PATH.

        Predictive resolution only. The factory may still fall back to
        mock if the chosen backend cannot actually instantiate.
        """
        if self.mode == "mock":
            return "mock"
        if self.dds_backend != "auto":
            return self.dds_backend
        for vendor in _DDS_AUTO_DETECT_ORDER:
            if _module_is_importable(_DDS_BACKEND_MODULES[vendor]):
                return vendor  # type: ignore[return-value]
        return "mock"


def _module_is_importable(module: str) -> bool:
    """True iff `find_spec(module)` finds the module without raising.

    Wraps `importlib.util.find_spec` to swallow `ModuleNotFoundError`
    raised when the *parent* package of a dotted module path is not
    installed (find_spec implicitly imports parents) and `ValueError`
    raised for a half-initialised module.
    """
    try:
        return importlib.util.find_spec(module) is not None
    except (ModuleNotFoundError, ValueError):
        return False


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

    raw_telemetry = src.get("TOPICFORGE_TELEMETRY", "off").strip().lower()
    if raw_telemetry in _TELEMETRY_ON_VALUES:
        telemetry_enabled = True
    elif raw_telemetry in _TELEMETRY_OFF_VALUES:
        telemetry_enabled = False
    else:
        raise ValueError(
            f"Invalid TOPICFORGE_TELEMETRY={raw_telemetry!r}; expected on/off (default off)"
        )

    raw_dds_backend = src.get("TOPICFORGE_DDS_BACKEND", "mock").strip().lower()
    if raw_dds_backend in _REMOVED_DDS_BACKENDS:
        raise ValueError(
            f"TOPICFORGE_DDS_BACKEND={raw_dds_backend!r} was removed in 0.5.3 together "
            f"with the Pro tier; expected one of {_VALID_DDS_BACKENDS}. The free tier "
            "still observes RTI, CoreDX and OpenSplice participants through standard "
            "RTPS discovery with `cyclone`. See docs/pro.md."
        )
    if raw_dds_backend not in _VALID_DDS_BACKENDS:
        raise ValueError(
            f"Invalid TOPICFORGE_DDS_BACKEND={raw_dds_backend!r}; "
            f"expected one of {_VALID_DDS_BACKENDS}"
        )

    raw_dds_domain = src.get("TOPICFORGE_DDS_DOMAIN_ID", "0").strip()
    try:
        dds_domain = int(raw_dds_domain)
    except ValueError as exc:
        raise ValueError(
            f"Invalid TOPICFORGE_DDS_DOMAIN_ID={raw_dds_domain!r}; expected integer"
        ) from exc
    if dds_domain < _DDS_DOMAIN_MIN or dds_domain > _DDS_DOMAIN_MAX:
        raise ValueError(
            f"Invalid TOPICFORGE_DDS_DOMAIN_ID={dds_domain}; "
            f"expected {_DDS_DOMAIN_MIN}..{_DDS_DOMAIN_MAX}"
        )

    return Settings(
        mode=raw_mode,
        log_level=raw_log,
        ros2_executable=ros2_exe,
        telemetry_enabled=telemetry_enabled,
        dds_backend=raw_dds_backend,
        dds_domain_id=dds_domain,
    )
