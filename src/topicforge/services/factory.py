"""Adapter selection.

This is the only place that knows how to map a `Settings` to a concrete
adapter, and where graceful degradation (`live` → `mock`, `cyclone` /
`fast` → `ros2_cli`) happens.

v0.3.0 model: a single adapter at a time. When `TOPICFORGE_DDS_BACKEND`
selects a DDS backend (`cyclone`, `fast`, `rti`), that adapter serves
the 3 DDS tools natively and raises on the 5 ROS2 tools. The default
(`mock`) keeps the historical ROS2-first behavior. A composite adapter
that delegates per-tool category is a v0.3.x roadmap item.
"""

from __future__ import annotations

import logging

from topicforge.adapters.base import MiddlewareAdapter
from topicforge.adapters.ros2_live import Ros2CliAdapter
from topicforge.adapters.ros2_mock import MockAdapter
from topicforge.config import Settings

log = logging.getLogger(__name__)


def build_adapter(settings: Settings) -> MiddlewareAdapter:
    """Return the adapter matching the effective runtime mode + DDS backend.

    Decision tree:
      * `TOPICFORGE_MODE=mock` (or `auto` with no ROS2 on PATH) →
        `MockAdapter`. The mock exposes all 8 tools via deterministic
        fixtures.
      * `TOPICFORGE_MODE=live` with `TOPICFORGE_DDS_BACKEND=fast` (or
        `auto` resolving to fast) → `FastDdsAdapter`. The 3 DDS tools
        work ; the 5 ROS2 tools raise `AdapterError`.
      * `TOPICFORGE_MODE=live` with `TOPICFORGE_DDS_BACKEND=cyclone`
        (or `auto` resolving to cyclone) → `CycloneDdsAdapter`. Same
        shape as Fast.
      * `TOPICFORGE_MODE=live` with default `TOPICFORGE_DDS_BACKEND=mock`
        → `Ros2CliAdapter`. The 5 ROS2 tools work ; the 3 DDS tools
        raise `AdapterError` pointing at `pip install topicforge[dds]`.

    Fallbacks (logged warnings, never crashes):
      * `fast` requested but `fastdds` not importable → ROS2 CLI.
      * `cyclone` requested but `cyclonedds` not importable → ROS2 CLI.
      * `rti` requested → ROS2 CLI (Pro tier v0.4.0+ roadmap, not
        shipped in v0.3.0).
      * `live` requested but `ros2` CLI not on PATH → mock.

    Predictive resolution (`auto`) lives in
    `config/settings.py:Settings.effective_mode` and
    `Settings.effective_dds_backend` (Fast > Cyclone > Mock priority).
    """
    if settings.effective_mode == "mock":
        return MockAdapter()

    dds_backend = settings.effective_dds_backend
    if dds_backend == "fast":
        adapter = _try_build_fast(settings)
        if adapter is not None:
            return adapter
    elif dds_backend == "cyclone":
        adapter = _try_build_cyclone(settings)
        if adapter is not None:
            return adapter
    elif dds_backend == "rti":
        log.warning(
            "TOPICFORGE_DDS_BACKEND=rti requires the Pro tier and a valid "
            "license, not shipped in v0.3.0 (v0.4.0+ roadmap); "
            "falling back to ROS2 CLI."
        )

    # Default live path: ROS2 CLI adapter.
    cli_adapter = Ros2CliAdapter(executable=settings.ros2_executable)
    if not cli_adapter.is_available():
        log.warning(
            "live mode requested but %r not on PATH; falling back to mock",
            settings.ros2_executable,
        )
        return MockAdapter()
    return cli_adapter


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
            "OSS backends). Falling back to ROS2 CLI."
        )
        return None

    adapter = CycloneDdsAdapter(domain_id=settings.dds_domain_id)
    if not adapter.is_available():
        log.warning("CycloneDdsAdapter reports not available; falling back to ROS2 CLI.")
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
            "OSS backends). Falling back to ROS2 CLI."
        )
        return None

    adapter = FastDdsAdapter(domain_id=settings.dds_domain_id)
    if not adapter.is_available():
        log.warning("FastDdsAdapter reports not available; falling back to ROS2 CLI.")
        return None
    return adapter
