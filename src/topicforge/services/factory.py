"""Adapter selection.

This is the only place that knows how to map a `Settings` to a concrete
adapter, and where graceful degradation (`live` -> `mock`, any DDS
backend -> `ros2_cli`) happens.

Decision tree: explicit `mock` mode returns `MockAdapter`. Otherwise the
ROS2 CLI adapter and the DDS adapter are each built best-effort; when both
come up they are wrapped in a `CompositeAdapter`, when only one does it is
returned alone, and when neither does the factory falls back to
`MockAdapter`. A DDS backend named explicitly is honored even when `ros2`
is not installed (`auto` mode included), so DDS-only users get their bus
rather than fixtures.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from topicforge.adapters.base import AdapterError, MiddlewareAdapter
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
    `Settings.effective_dds_backend`.
    """
    if settings.effective_mode == "mock" and settings.effective_dds_backend == "mock":
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
        # ROS2 CLI not available but a DDS backend is: DDS-only live.
        log.info("DDS-only live adapter active: %s", dds_adapter.name)
        return dds_adapter

    if ros_adapter is not None:
        # ROS2 CLI available, DDS backend either not configured or not
        # importable. v0.3.0 behavior preserved exactly.
        return ros_adapter

    log.warning(
        "live backend requested but neither %r nor a DDS backend (%s) is available; "
        "falling back to mock",
        settings.ros2_executable,
        settings.effective_dds_backend,
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
    importable, or when the adapter fails or reports unavailable at
    construction. Logged warnings explain each fallback.

    Vendors lazy-import their adapter from `topicforge.adapters.dds_<vendor>`.
    """
    dds_backend = settings.effective_dds_backend
    if dds_backend == "fast":
        return _try_build_fast(settings)
    if dds_backend == "cyclone":
        return _try_build_cyclone(settings)
    if dds_backend == "opendds":
        return _try_build_opendds(settings)
    if dds_backend == "dust":
        return _try_build_dust(settings)
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
            '`pip install "topicforge[dds-cyclone]"`. The DDS module is '
            "unavailable."
        )
        return None
    except Exception as exc:  # a native library can fail to load (OSError)
        log.warning(
            "DDS binding failed to load (%s: %s); the DDS module is unavailable.",
            type(exc).__name__,
            exc,
        )
        return None

    return _instantiate(
        "CycloneDdsAdapter", lambda: CycloneDdsAdapter(domain_id=settings.dds_domain_id)
    )


def _try_build_fast(settings: Settings) -> MiddlewareAdapter | None:
    """Best-effort instantiate `FastDdsAdapter`. Returns None on failure.

    Lazy import: this is the only call site that pulls in `fastdds`.
    Mock-only, Cyclone-only, and ROS2-only installs never load the module.
    """
    try:
        from topicforge.adapters.dds_fast import FastDdsAdapter
    except ImportError:
        log.warning(
            "TOPICFORGE_DDS_BACKEND=fast but the `fastdds` Python binding is "
            "not installed. It is not published on PyPI: build it from "
            "eProsima's Fast-DDS-python sources (see docs/DDS_QUICKSTART.md). "
            "The DDS module is unavailable."
        )
        return None
    except Exception as exc:  # a native library can fail to load (OSError)
        log.warning(
            "DDS binding failed to load (%s: %s); the DDS module is unavailable.",
            type(exc).__name__,
            exc,
        )
        return None

    return _instantiate("FastDdsAdapter", lambda: FastDdsAdapter(domain_id=settings.dds_domain_id))


def _try_build_opendds(settings: Settings) -> MiddlewareAdapter | None:
    """Best-effort instantiate `OpenDdsAdapter` (permanent stub).

    The stub adapter's `is_available()` always returns False. The factory
    still routes here so users running `TOPICFORGE_DDS_BACKEND=opendds`
    explicitly get a clear warning rather than a silent mock fallback.
    """
    try:
        from topicforge.adapters.dds_opendds import OpenDdsAdapter
    except ImportError:
        log.warning(
            "TOPICFORGE_DDS_BACKEND=opendds but the OpenDDS adapter "
            "module is not available. The DDS module is unavailable."
        )
        return None
    except Exception as exc:  # a native library can fail to load (OSError)
        log.warning(
            "DDS binding failed to load (%s: %s); the DDS module is unavailable.",
            type(exc).__name__,
            exc,
        )
        return None

    return _instantiate("OpenDdsAdapter", lambda: OpenDdsAdapter(domain_id=settings.dds_domain_id))


def _try_build_dust(settings: Settings) -> MiddlewareAdapter | None:
    """Best-effort instantiate `DustDdsAdapter` (permanent stub).

    The stub adapter's `is_available()` always returns False ; the
    factory falls back transparently.
    """
    try:
        from topicforge.adapters.dds_dust import DustDdsAdapter
    except ImportError:
        log.warning(
            "TOPICFORGE_DDS_BACKEND=dust but the Dust DDS adapter "
            "module is not available. The DDS module is unavailable."
        )
        return None
    except Exception as exc:  # a native library can fail to load (OSError)
        log.warning(
            "DDS binding failed to load (%s: %s); the DDS module is unavailable.",
            type(exc).__name__,
            exc,
        )
        return None

    return _instantiate("DustDdsAdapter", lambda: DustDdsAdapter(domain_id=settings.dds_domain_id))


def _instantiate(label: str, build: Callable[[], MiddlewareAdapter]) -> MiddlewareAdapter | None:
    """Construct a DDS adapter without ever letting construction escape.

    Vendor constructors join the DDS domain and raise `AdapterError` when
    that fails (e.g. an invalid `CYCLONEDDS_URI`). Any other exception is
    logged with its cause as a last resort: `build_adapter` documents that
    it never raises, and a crash at startup is worse than a mock fallback.
    Returns `None` on failure or when the adapter reports unavailable.
    """
    try:
        adapter = build()
        available = adapter.is_available()
    except AdapterError as exc:
        log.warning("%s could not start: %s. The DDS module is unavailable.", label, exc)
        return None
    except Exception as exc:
        log.warning(
            "%s failed with an unexpected error (%s: %s). The DDS module is unavailable.",
            label,
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return None
    if not available:
        log.warning("%s reports not available. The DDS module is unavailable.", label)
        return None
    return adapter
