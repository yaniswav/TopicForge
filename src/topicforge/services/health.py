"""Health service — environment & mode introspection."""

from __future__ import annotations

import importlib.util
import os
import shutil
from typing import Literal

from topicforge import __version__
from topicforge.config import Settings

# Map effective DDS backend → the Python module the adapter imports lazily.
# Used to decide `middleware_available` without actually constructing the
# adapter — a separate concern from the factory's instantiation fallback.
#
# Imports the canonical table from `config.settings` so the lookup cannot
# drift between the auto-detect chain and the health report. The health
# check uses the SAME module name a `find_spec` probe targets — for Pro
# vendors that means the `topicforge_pro.adapters.<vendor>` plugin, not
# the underlying commercial SDK module (which the OSS core never imports
# directly). For RTI specifically, we also probe the upstream
# `rti.connextdds` module so a user who has RTI installed but no
# `topicforge-pro` package still sees `middleware_available=True` —
# they just need the Pro package to actually use it.
from topicforge.config.settings import _DDS_BACKEND_MODULES
from topicforge.models import HealthReport
from topicforge.services.constants import MAX_SAMPLE_COUNT

_HEALTH_FALLBACK_MODULES: dict[str, str] = {
    "rti": "rti.connextdds",
}


class HealthService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def report(self) -> HealthReport:
        """Build a HealthReport for the current environment.

        Never raises. `health_check` is the tool a user will reach for when
        things look broken, so it must always answer. DDS fields
        (`dds_backend`, `dds_domain_id`, `middleware_available`) are
        populated in v0.3.0+ ; the `ros_backend` field is populated in
        v0.4.0 Phase 1 alongside the composite adapter so clients can
        distinguish the ROS2 and DDS halves of a composed runtime.
        """
        ros2_path = shutil.which(self._settings.ros2_executable)
        dds_backend = self._settings.effective_dds_backend
        return HealthReport(
            mode=self._settings.effective_mode,
            requested_mode=self._settings.mode,
            ros2_available=ros2_path is not None,
            ros2_distro=os.environ.get("ROS_DISTRO"),
            server_version=__version__,
            max_sample_count=MAX_SAMPLE_COUNT,
            dds_backend=dds_backend,
            dds_domain_id=self._settings.dds_domain_id,
            middleware_available=_middleware_available(dds_backend),
            ros_backend=_ros_backend(self._settings, ros2_path),
        )


def _middleware_available(backend: str) -> bool:
    """True if the DDS backend's Python bindings are importable on this host.

    Mock is always available (no middleware needed). Other backends are
    checked via `importlib.util.find_spec` against the canonical module
    table shared with the auto-detect chain (`_DDS_BACKEND_MODULES`).
    For Pro vendors, a fallback probe against the upstream SDK module
    (e.g. `rti.connextdds`) reports True when the SDK is installed but
    the `topicforge-pro` plugin is not — making the missing piece visible
    to the user.
    """
    if backend == "mock":
        return True
    candidates = [
        _DDS_BACKEND_MODULES.get(backend),
        _HEALTH_FALLBACK_MODULES.get(backend),
    ]
    return any(c is not None and importlib.util.find_spec(c) is not None for c in candidates)


def _ros_backend(settings: Settings, ros2_path: str | None) -> Literal["mock", "ros2_cli", "none"]:
    """Resolve the ROS2 half of the runtime to a wire tag.

    Mirrors the factory's decision tree: mock global mode → `"mock"` ;
    live with `ros2` on PATH → `"ros2_cli"` ; live without `ros2` →
    `"none"` (the factory falls back to DDS-only or mock).
    """
    if settings.effective_mode == "mock":
        return "mock"
    if ros2_path is not None:
        return "ros2_cli"
    return "none"
