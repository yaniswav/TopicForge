"""Health service: environment & mode introspection."""

from __future__ import annotations

import os
import shutil
from typing import Literal

from topicforge import __version__
from topicforge.adapters.base import MiddlewareAdapter
from topicforge.config import Settings
from topicforge.config.settings import _DDS_BACKEND_MODULES, _module_is_importable
from topicforge.constants import MAX_SAMPLE_COUNT
from topicforge.models import HealthReport

RosBackendTag = Literal["mock", "ros2_cli", "none"]
DdsBackendTag = Literal["mock", "cyclone", "fast", "opendds", "dust", "none"]

_DDS_TAGS: frozenset[str] = frozenset({"mock", "cyclone", "fast", "opendds", "dust"})


class HealthService:
    """Reports the runtime that was actually built, not the one requested.

    The adapter produced by `services.factory.build_adapter` is the single
    source of truth for `mode`, `ros_backend` and `dds_backend`: re-deriving
    them from `Settings` would drift from the factory's fallbacks (e.g.
    `TOPICFORGE_MODE=live` without `ros2` ends up on the mock adapter).
    """

    def __init__(self, settings: Settings, adapter: MiddlewareAdapter) -> None:
        self._settings = settings
        self._adapter = adapter

    def report(self) -> HealthReport:
        """Build a HealthReport for the current environment.

        Never raises. `health_check` is the tool a user will reach for when
        things look broken, so it must always answer.
        """
        ros_backend, dds_backend = _backends_from_adapter_name(self._adapter.name)
        return HealthReport(
            mode=self._adapter.effective_mode,
            requested_mode=self._settings.mode,
            ros2_available=shutil.which(self._settings.ros2_executable) is not None,
            ros2_distro=os.environ.get("ROS_DISTRO"),
            server_version=__version__,
            max_sample_count=MAX_SAMPLE_COUNT,
            dds_backend=dds_backend,
            dds_domain_id=self._settings.dds_domain_id,
            middleware_available=_middleware_available(dds_backend, self._settings),
            ros_backend=ros_backend,
        )


def _backends_from_adapter_name(name: str) -> tuple[RosBackendTag, DdsBackendTag]:
    """Split an adapter tag into its ROS2 and DDS halves.

    `mock` serves both halves, `ros2_cli` is ROS2 only, a bare DDS tag
    (`cyclone`, `fast`, ...) is DDS only, and `ros2_cli+<dds>` is a
    composite. A half the adapter does not serve reports `"none"`.
    """
    if name == "mock":
        return "mock", "mock"
    parts = name.split("+")
    ros_backend: RosBackendTag = "ros2_cli" if "ros2_cli" in parts else "none"
    dds_parts = [part for part in parts if part in _DDS_TAGS]
    dds_backend: DdsBackendTag = dds_parts[0] if dds_parts else "none"  # type: ignore[assignment]
    return ros_backend, dds_backend


def _middleware_available(dds_backend: str, settings: Settings) -> bool:
    """True if the DDS backend's Python bindings are importable on this host.

    When a DDS backend is serving (`dds_backend != "none"`) its bindings are
    loaded by construction. When none is, probe the *requested* backend's
    module so a configured-but-missing binding stays visible.
    """
    if dds_backend != "none":
        return True
    module = _DDS_BACKEND_MODULES.get(settings.dds_backend)
    return module is not None and _module_is_importable(module)
