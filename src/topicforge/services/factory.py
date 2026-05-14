"""Adapter selection.

This is the only place that knows how to map a `Settings` to a concrete
adapter, and where graceful degradation (`live` → `mock`, any DDS
vendor → `ros2_cli`) happens.

v0.4.0 Phase 1.5 widens the DDS vendor matrix from 2 (Cyclone, Fast)
to 8 (Cyclone, Fast, OpenDDS, Dust + the Pro-tier RTI, OpenSplice,
CoreDX, InterCOM). Pro-tier vendors are loaded lazily via the
`topicforge_pro.adapters.<vendor>` namespace ; OSS vendors keep
their historical direct import. The composite-adapter top-level
decision tree is unchanged from Phase 1: when a ROS2 CLI adapter
and a DDS adapter both come up, they're wrapped in a `CompositeAdapter`.
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
    `Settings.effective_dds_backend` (8-vendor auto-detect chain).
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

    Returns `None` when the backend is `mock`, when the SDK is not
    importable, or when the adapter reports unavailable at construction.
    Logged warnings explain each fallback.

    OSS vendors (`cyclone`, `fast`, `opendds`, `dust`) lazy-import their
    adapter from `topicforge.adapters.dds_<vendor>` ; Pro vendors (`rti`,
    `opensplice`, `coredx`, `intercom`) lazy-import from
    `topicforge_pro.adapters.<vendor>`. The OSS core never directly
    imports a Pro adapter.
    """
    dds_backend = settings.effective_dds_backend
    if dds_backend == "mock":
        return None
    if dds_backend == "fast":
        return _try_build_fast(settings)
    if dds_backend == "cyclone":
        return _try_build_cyclone(settings)
    if dds_backend == "opendds":
        return _try_build_opendds(settings)
    if dds_backend == "dust":
        return _try_build_dust(settings)
    if dds_backend in ("rti", "opensplice", "coredx", "intercom"):
        return _try_build_pro_vendor(settings, dds_backend)
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


def _try_build_opendds(settings: Settings) -> MiddlewareAdapter | None:
    """Best-effort instantiate `OpenDdsAdapter` (v0.4.0 Phase 1.5 stub).

    `pyopendds` is not yet maintained on PyPI as of 2026-05-14 ; the
    stub adapter raises on all 8 protocol methods. The factory still
    routes here so users running `TOPICFORGE_DDS_BACKEND=opendds`
    explicitly get a clear error rather than a silent mock fallback.
    """
    try:
        from topicforge.adapters.dds_opendds import OpenDdsAdapter
    except ImportError:
        log.warning(
            "TOPICFORGE_DDS_BACKEND=opendds but the OpenDDS adapter "
            "module is not available. Falling back to ROS2 CLI alone."
        )
        return None

    adapter = OpenDdsAdapter(domain_id=settings.dds_domain_id)
    if not adapter.is_available():
        log.warning(
            "OpenDdsAdapter reports not available — `pyopendds` Python "
            "bindings are not installed on this host (no maintained PyPI "
            "package at v0.4.0). Falling back to ROS2 CLI alone."
        )
        return None
    return adapter


def _try_build_dust(settings: Settings) -> MiddlewareAdapter | None:
    """Best-effort instantiate `DustDdsAdapter` (v0.4.0 Phase 1.5 stub).

    `dust-dds-python` is not yet maintained on PyPI as of 2026-05-14.
    The stub adapter's `is_available()` always returns False ; the
    factory falls back transparently.
    """
    try:
        from topicforge.adapters.dds_dust import DustDdsAdapter
    except ImportError:
        log.warning(
            "TOPICFORGE_DDS_BACKEND=dust but the Dust DDS adapter "
            "module is not available. Falling back to ROS2 CLI alone."
        )
        return None

    adapter = DustDdsAdapter(domain_id=settings.dds_domain_id)
    if not adapter.is_available():
        log.warning(
            "DustDdsAdapter reports not available — no maintained "
            "Python binding for Dust DDS at v0.4.0. Falling back to "
            "ROS2 CLI alone."
        )
        return None
    return adapter


def _try_build_pro_vendor(settings: Settings, vendor: str) -> MiddlewareAdapter | None:
    """Best-effort load a Pro-tier adapter from `topicforge_pro.adapters.<vendor>`.

    The Pro package is a separate pip install ; the OSS core never
    bundles or directly imports a Pro adapter. Each Pro adapter
    exposes a class named `<Vendor>Adapter` (e.g. `RtiConnextAdapter`,
    `OpenSpliceAdapter`) constructible with `domain_id`.

    Returns `None` when the Pro package is not installed, the vendor
    module is missing, or the adapter reports unavailable.
    """
    pro_class_names = {
        "rti": "RtiConnextAdapter",
        "opensplice": "OpenSpliceAdapter",
        "coredx": "CoreDxAdapter",
        "intercom": "InterComAdapter",
    }
    class_name = pro_class_names.get(vendor)
    if class_name is None:
        log.warning("Unknown Pro vendor %r ; falling back to ROS2 CLI alone.", vendor)
        return None

    module_path = f"topicforge_pro.adapters.{vendor if vendor != 'rti' else 'rti_connext'}"
    try:
        import importlib

        module = importlib.import_module(module_path)
    except ImportError:
        log.warning(
            "TOPICFORGE_DDS_BACKEND=%s requires the Pro tier package "
            "(`pip install topicforge-pro`) and a valid TOPICFORGE_LICENSE_KEY. "
            "Falling back to ROS2 CLI alone.",
            vendor,
        )
        return None

    adapter_cls = getattr(module, class_name, None)
    if adapter_cls is None:
        log.warning(
            "Pro package present but %s.%s is missing ; falling back to ROS2 CLI alone.",
            module_path,
            class_name,
        )
        return None

    adapter = adapter_cls(domain_id=settings.dds_domain_id)
    if not adapter.is_available():
        log.warning(
            "%s reports not available (license missing or binding misconfigured); "
            "falling back to ROS2 CLI alone.",
            class_name,
        )
        return None
    return adapter
