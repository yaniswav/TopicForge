"""Health service: environment and mode introspection."""

from __future__ import annotations

import os
import shutil
import time
from typing import Any, Literal

from topicforge import __version__
from topicforge.adapters.base import MiddlewareAdapter
from topicforge.config import Settings
from topicforge.config.settings import DDS_BACKEND_MODULES, module_is_importable
from topicforge.constants import MAX_SAMPLE_COUNT
from topicforge.models import HealthReport

RosBackendTag = Literal["mock", "ros2_cli", "none"]
DdsBackendTag = Literal["mock", "cyclone", "fast", "opendds", "dust", "none"]

_DDS_TAGS: frozenset[str] = frozenset({"mock", "cyclone", "fast", "opendds", "dust"})


class HealthService:
    """Reports the runtime that was built, not the one requested.

    `mode`, `ros_backend` and `dds_backend` come from the adapter that
    `build_adapter` produced; deriving them from `Settings` would miss its
    fallbacks (`TOPICFORGE_MODE=live` without `ros2` ends up on the mock adapter).
    """

    def __init__(self, settings: Settings, adapter: MiddlewareAdapter) -> None:
        self._settings = settings
        self._adapter = adapter

    def report(self) -> HealthReport:
        """Build a `HealthReport`. Never raises: `health_check` is used when things are broken."""
        ros_backend, dds_backend = _backends_from_adapter_name(self._adapter.name)
        observer = _observer_status(self._adapter)
        return HealthReport(
            now_ns=time.time_ns(),
            observer_started_ns=observer.get("observer_started_ns"),
            tracker_running=observer.get("running"),
            tracker_passes=observer.get("passes"),
            tracker_errors=observer.get("errors"),
            tracker_last_pass_ns=observer.get("last_pass_ns"),
            tracker_cache_evictions=observer.get("cache_evictions"),
            mode=self._adapter.effective_mode,
            requested_mode=self._settings.mode,
            ros2_available=shutil.which(self._settings.ros2_executable) is not None,
            ros2_distro=os.environ.get("ROS_DISTRO"),
            server_version=__version__,
            max_sample_count=MAX_SAMPLE_COUNT,
            dds_backend=dds_backend,
            dds_inactive_reason=(
                getattr(self._adapter, "dds_inactive_reason", None)
                if dds_backend == "none"
                else None
            ),
            dds_domain_id=self._settings.dds_domain_id,
            observed_domain_note=(
                f"Only DDS domain {self._settings.dds_domain_id} (joined at startup) is "
                "observed: a program running on another domain is invisible here. "
                "Change it with TOPICFORGE_DDS_DOMAIN_ID and a restart."
                if dds_backend != "none"
                else None
            ),
            middleware_available=_middleware_available(dds_backend, self._settings),
            ros_backend=ros_backend,
            ros_tools_available=ros_backend != "none",
        )


def _observer_status(adapter: MiddlewareAdapter) -> dict[str, Any]:
    """Observer start time and tracker counters if the adapter has them (Cyclone), else `{}`.

    Not part of the protocol, and a failure must not break `health_check`.
    """
    status = getattr(adapter, "observer_status", None)
    if not callable(status):
        return {}
    try:
        return dict(status() or {})
    except Exception:
        return {}


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
    """True if the DDS bindings are importable.

    A serving backend has them loaded. Otherwise the requested backend's module
    is probed, so a configured but missing binding stays visible.
    """
    if dds_backend != "none":
        return True
    module = DDS_BACKEND_MODULES.get(settings.dds_backend)
    return module is not None and module_is_importable(module)
