"""Runtime settings, resolved from environment variables.

Settings are immutable and built once at startup. `auto` is resolved in
`Settings.effective_mode`, the only place that decision is made.
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
# Vendor identifiers of the retired Pro tier. They get a dedicated error so an
# existing deployment learns why its configuration stopped working.
_REMOVED_DDS_BACKENDS: frozenset[str] = frozenset({"rti", "opensplice", "coredx", "intercom"})
_VALID_LOG_LEVELS: tuple[str, ...] = ("DEBUG", "INFO", "WARNING", "ERROR")
# Telemetry is opt-in: only the on-values enable it, and unknown values raise.
_TELEMETRY_ON_VALUES: frozenset[str] = frozenset({"on", "1", "true", "yes", "enabled"})
_TELEMETRY_OFF_VALUES: frozenset[str] = frozenset({"", "off", "0", "false", "no", "disabled"})

_DDS_DOMAIN_MIN = 0
_DDS_DOMAIN_MAX = 232

# Vendor -> Python module, shared by `auto` resolution and `HealthService`.
_DDS_BACKEND_MODULES: dict[str, str] = {
    "opendds": "pyopendds",
    "fast": "fastdds",
    "cyclone": "cyclonedds",
    "dust": "dust_dds_python",
}

# Priority for `dds_backend == "auto"`: the first importable module wins, else
# mock. Only backends with a working adapter belong here: `opendds` and `dust`
# are stubs, and `pyopendds` is on PyPI, so listing it would pick the stub and
# never try Cyclone. They stay selectable explicitly.
_DDS_AUTO_DETECT_ORDER: tuple[str, ...] = ("fast", "cyclone")


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable runtime configuration."""

    mode: Mode
    log_level: str
    ros2_executable: str
    telemetry_enabled: bool
    dds_backend: DdsBackend = "mock"
    dds_domain_id: int = 0

    @property
    def effective_mode(self) -> ResolvedMode:
        """Resolve `auto`: `live` when the ROS2 executable is on PATH, else `mock`.

        `live` and `mock` pass through. This only predicts; the fallback for a
        live adapter that cannot start is in `services/factory.py:build_adapter`.
        """
        if self.mode == "auto":
            return "live" if shutil.which(self.ros2_executable) else "mock"
        return self.mode

    @property
    def effective_dds_backend(self) -> ResolvedDdsBackend:
        """Resolve the DDS backend.

        - `TOPICFORGE_MODE=mock` forces `mock`.
        - An explicit backend is returned as-is, with or without `ros2` on
          PATH: serving fixtures instead would hide the misconfiguration.
        - `auto` walks `_DDS_AUTO_DETECT_ORDER` and returns the first backend
          whose module is importable, else `mock`. The default is `mock`, so
          `auto` is always an explicit request for DDS.

        This only predicts; the factory may still fall back to mock if the
        backend cannot start.
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
    """True when `find_spec(module)` finds the module.

    `ModuleNotFoundError` (missing parent package) and `ValueError`
    (half-initialised module) count as not importable.
    """
    try:
        return importlib.util.find_spec(module) is not None
    except (ModuleNotFoundError, ValueError):
        return False


def load_settings(env: dict[str, str] | os._Environ[str] | None = None) -> Settings:
    """Build `Settings` from `env` (default `os.environ`); `env` is injectable for tests."""
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
