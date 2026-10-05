"""Adapter selection: maps `Settings` to a concrete adapter and degrades gracefully.

Explicit `mock` mode returns `MockAdapter`. Otherwise the ROS2 CLI adapter
and the DDS adapter are each built best-effort. Both up gives a
`CompositeAdapter`, one gives that adapter alone, neither falls back to
`MockAdapter`. A DDS backend named explicitly is honored even without
`ros2` installed, so DDS-only users get their bus rather than fixtures.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from topicforge.adapters.base import AdapterError, MiddlewareAdapter
from topicforge.adapters.composite import CompositeAdapter
from topicforge.adapters.ros2_live import Ros2CliAdapter
from topicforge.adapters.ros2_mock import MockAdapter
from topicforge.config import Settings
from topicforge.config.settings import DDS_BACKEND_MODULES, module_is_importable

log = logging.getLogger(__name__)


def build_adapter(settings: Settings) -> MiddlewareAdapter:
    """Return the adapter for the effective mode and DDS backend.

    Never raises: each failure logs a warning and falls to the next-best
    backend, ending at `MockAdapter`. `auto` is resolved in `Settings`.
    """
    if settings.effective_mode == "mock" and settings.effective_dds_backend == "mock":
        return MockAdapter()

    dds_adapter = _try_build_dds(settings)
    ros_adapter = _try_build_ros2_cli(
        settings, dds_inactive_reason=None if dds_adapter else _dds_inactive_reason(settings)
    )

    if ros_adapter is not None and dds_adapter is not None:
        log.info(
            "composite adapter active: ros=%s dds=%s domain=%s",
            ros_adapter.name,
            dds_adapter.name,
            settings.dds_domain_id,
        )
        return CompositeAdapter(ros_adapter, dds_adapter)

    if dds_adapter is not None:
        log.info("DDS-only live adapter active: %s", dds_adapter.name)
        return dds_adapter

    if ros_adapter is not None:
        # DDS backend not configured or not importable.
        return ros_adapter

    log.warning(
        "live backend requested but neither %r nor a DDS backend (%s) is available; "
        "falling back to mock",
        settings.ros2_executable,
        settings.effective_dds_backend,
    )
    return MockAdapter()


def _try_build_ros2_cli(
    settings: Settings, *, dds_inactive_reason: str | None = None
) -> MiddlewareAdapter | None:
    """Return a usable `Ros2CliAdapter` or `None` when the CLI is missing.

    `dds_inactive_reason` is what the adapter tells a client who calls a DDS
    tool while no DDS backend serves.
    """
    cli_adapter = Ros2CliAdapter(
        executable=settings.ros2_executable,
        dds_inactive_reason=dds_inactive_reason,
        max_message_chars=settings.max_sample_bytes,
    )
    if not cli_adapter.is_available():
        return None
    return cli_adapter


_CYCLONE_HINT = (
    'Install it with `pip install "topicforge[dds-cyclone]"` and set '
    "`TOPICFORGE_DDS_BACKEND=cyclone`."
)


def _dds_inactive_reason(settings: Settings) -> str:
    """Why no DDS backend is serving, for the three distinct causes.

    The backend was not selected, its binding is not installed, or the
    binding is installed but the adapter failed to load or start (the server
    log has the cause).
    """
    backend = settings.effective_dds_backend
    if backend == "mock":
        if settings.dds_backend == "auto":
            return (
                "`TOPICFORGE_DDS_BACKEND=auto` found no DDS binding (`fastdds` or "
                f"`cyclonedds`) installed. {_CYCLONE_HINT}"
            )
        if module_is_importable(DDS_BACKEND_MODULES["cyclone"]):
            return (
                "no DDS backend is selected: `TOPICFORGE_DDS_BACKEND` is unset or `mock`. "
                "The `cyclonedds` binding is installed; set `TOPICFORGE_DDS_BACKEND=cyclone` "
                "and restart to enable the DDS tools."
            )
        return f"no DDS backend is selected and no DDS binding is installed. {_CYCLONE_HINT}"
    module = DDS_BACKEND_MODULES.get(backend)
    if backend in ("opendds", "dust"):
        return f"`TOPICFORGE_DDS_BACKEND={backend}` is a stub that never serves; use `cyclone`."
    if module is not None and not module_is_importable(module):
        return (
            f"`TOPICFORGE_DDS_BACKEND={backend}` is set but its `{module}` Python binding is "
            f"not installed. {_CYCLONE_HINT if backend == 'cyclone' else ''}".rstrip()
        )
    return (
        f"`TOPICFORGE_DDS_BACKEND={backend}` is set and its binding is installed, but the "
        "DDS adapter failed to load or start; the server log has the cause."
    )


def _try_build_dds(settings: Settings) -> MiddlewareAdapter | None:
    """The DDS adapter for the resolved backend, or `None` with a logged warning.

    `None` when the backend is `mock`, the SDK is not importable, or the
    adapter fails or reports unavailable at construction. Adapters are
    imported lazily from `topicforge.adapters.dds_<vendor>`.
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
    """Instantiate `CycloneDdsAdapter`, or `None` on failure.

    The only place that imports `cyclonedds`, so other installs never load it.
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
    """Instantiate `FastDdsAdapter`, or `None` on failure.

    The only place that imports `fastdds`, so other installs never load it.
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
    """Instantiate the `OpenDdsAdapter` stub, which is never available.

    Routing here makes an explicit `TOPICFORGE_DDS_BACKEND=opendds` log a
    warning instead of falling back to mock silently.
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
    """Instantiate the `DustDdsAdapter` stub, which is never available."""
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
    """Construct a DDS adapter; any failure or unavailability returns `None`.

    Vendor constructors join the domain and raise `AdapterError` when that
    fails (e.g. an invalid `CYCLONEDDS_URI`). Other exceptions are logged
    too, because a crash at startup is worse than a mock fallback.
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
