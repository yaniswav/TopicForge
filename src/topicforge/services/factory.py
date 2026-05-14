"""Adapter selection.

This is the only place that knows how to map a `Settings` to a concrete
adapter, and where graceful degradation (`live` → `mock`, `cyclone` /
`fast` → `ros2_cli`) happens.

v0.4.0 Phase 1 introduces the **composite path**: when
`TOPICFORGE_MODE=live` and a DDS backend is configured, the factory
instantiates both a `Ros2CliAdapter` and the chosen DDS adapter and
wraps them in a `CompositeAdapter`. The composite is itself a
`MiddlewareAdapter`, so the rest of the codebase (services, handlers,
tests) stays oblivious.

The decision tree, in order:

  1. `effective_mode == "mock"` → `MockAdapter` alone. The mock natively
     serves all 8 tools via deterministic fixtures, so no composition
     is needed.
  2. `effective_mode == "live"` and `effective_dds_backend in
     ("cyclone", "fast")` → try to instantiate both adapters. If both
     come up, return `CompositeAdapter(ros, dds)`. Otherwise return
     whichever side is available (preserves v0.3.0 backward behavior).
  3. `effective_mode == "live"` and `effective_dds_backend == "mock"`
     → `Ros2CliAdapter` alone. Default ROS2-only path, unchanged from
     v0.3.0.
  4. `effective_mode == "live"` and `effective_dds_backend == "rti"`
     → log a Pro-tier warning and fall through to ROS2 CLI alone.
  5. Final fallback when the ROS2 CLI is not on PATH → `MockAdapter`.
"""

from __future__ import annotations

import logging

from topicforge.adapters.base import MiddlewareAdapter
from topicforge.adapters.composite import CompositeAdapter
from topicforge.adapters.ros2_live import Ros2CliAdapter
from topicforge.adapters.ros2_mock import MockAdapter
from topicforge.config import Settings

log = logging.getLogger(__name__)


def build_adapter(settings: Settings) -> MiddlewareAdapter:
    """Return the adapter matching the effective runtime mode + DDS backend.

    See module docstring for the full decision tree. The function never
    raises ; every failure path degrades to a logged warning plus the
    next-best backend, ending at `MockAdapter` which is always available.

    Predictive resolution (`auto`) lives in
    `config/settings.py:Settings.effective_mode` and
    `Settings.effective_dds_backend` (Fast > Cyclone > Mock priority).
    """
    if settings.effective_mode == "mock":
        return MockAdapter()

    ros_adapter = _try_build_ros2_cli(settings)
    dds_adapter = _try_build_dds(settings)

    if ros_adapter is not None and dds_adapter is not None:
        log.info(
            "composite adapter active: ros=%s dds=%s domain=%s",
            ros_adapter.name,
            dds_adapter.name,
            settings.dds_domain_id,
        )
        return CompositeAdapter(ros_adapter, dds_adapter)

    if dds_adapter is not None:
        # ROS2 CLI not available but a DDS backend is — DDS-only live.
        log.info("DDS-only live adapter active: %s", dds_adapter.name)
        return dds_adapter

    if ros_adapter is not None:
        # ROS2 CLI available, DDS backend either not configured or not
        # importable. v0.3.0 behavior preserved exactly.
        return ros_adapter

    log.warning(
        "live mode requested but neither %r nor a DDS backend is available; falling back to mock",
        settings.ros2_executable,
    )
    return MockAdapter()


def _try_build_ros2_cli(settings: Settings) -> MiddlewareAdapter | None:
    """Return a usable `Ros2CliAdapter` or `None` when the CLI is missing."""
    cli_adapter = Ros2CliAdapter(executable=settings.ros2_executable)
    if not cli_adapter.is_available():
        return None
    return cli_adapter


def _try_build_dds(settings: Settings) -> MiddlewareAdapter | None:
    """Best-effort DDS adapter per the resolved backend.

    Returns `None` when the backend is `mock`, when the backend is
    `rti` (Pro tier, v0.4.0+ roadmap), or when the chosen SDK is not
    importable. Logged warnings explain the fallback.
    """
    dds_backend = settings.effective_dds_backend
    if dds_backend == "mock":
        return None
    if dds_backend == "fast":
        return _try_build_fast(settings)
    if dds_backend == "cyclone":
        return _try_build_cyclone(settings)
    if dds_backend == "rti":
        log.warning(
            "TOPICFORGE_DDS_BACKEND=rti requires the Pro tier and a valid "
            "license, not shipped in v0.3.0 (v0.4.0+ roadmap); "
            "falling back to ROS2 CLI alone."
        )
        return None
    return None


def _try_build_cyclone(settings: Settings) -> MiddlewareAdapter | None:
    """Best-effort instantiate `CycloneDdsAdapter`. Returns None on failure.

    Lazy import: this is the only call site that pulls in `cyclonedds`.
    Mock-only, Fast-only, and ROS2-only installs never load the module.
    """
    try:
        from topicforge.adapters.dds_cyclone import CycloneDdsAdapter
    except ImportError:
        log.warning(
            "TOPICFORGE_DDS_BACKEND=cyclone but the `cyclonedds` Python "
            "bindings are not installed. Install with "
            "`pip install topicforge[dds-cyclone]` (or `[dds]` for both "
            "OSS backends). Falling back to ROS2 CLI alone."
        )
        return None

    adapter = CycloneDdsAdapter(domain_id=settings.dds_domain_id)
    if not adapter.is_available():
        log.warning("CycloneDdsAdapter reports not available; falling back to ROS2 CLI alone.")
        return None
    return adapter


def _try_build_fast(settings: Settings) -> MiddlewareAdapter | None:
    """Best-effort instantiate `FastDdsAdapter`. Returns None on failure.

    Lazy import: this is the only call site that pulls in `fastdds`.
    Mock-only, Cyclone-only, and ROS2-only installs never load the module.
    """
    try:
        from topicforge.adapters.dds_fast import FastDdsAdapter
    except ImportError:
        log.warning(
            "TOPICFORGE_DDS_BACKEND=fast but the `fastdds` Python "
            "bindings are not installed. Install with "
            "`pip install topicforge[dds-fast]` (or `[dds]` for both "
            "OSS backends). Falling back to ROS2 CLI alone."
        )
        return None

    adapter = FastDdsAdapter(domain_id=settings.dds_domain_id)
    if not adapter.is_available():
        log.warning("FastDdsAdapter reports not available; falling back to ROS2 CLI alone.")
        return None
    return adapter
