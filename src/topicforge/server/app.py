"""MCP server bootstrap.

Wires settings → adapter → services → tools → FastMCP. This module is the
only place that knows the full dependency graph; everything else stays
narrowly scoped.
"""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

from topicforge import __version__
from topicforge.config import Settings, load_settings
from topicforge.services import HealthService, Inspector, build_adapter
from topicforge.telemetry import TelemetryClient, Transport, build_telemetry_client
from topicforge.tools import register_tools

log = logging.getLogger(__name__)


def build_app(
    settings: Settings | None = None,
    *,
    telemetry: TelemetryClient | None = None,
    telemetry_transport: Transport | None = None,
) -> FastMCP:
    """Construct a fully wired FastMCP application.

    `settings` is optional so tests can build the app with deterministic
    configuration; production callers (`python -m topicforge`) pass nothing
    and pick up settings from the environment.

    `telemetry` lets tests inject a pre-built client (e.g. one wired to a
    spy transport). When omitted, the client is built from settings and
    `telemetry_transport` is used as the transport (falling back to the
    default structured-log transport).
    """
    settings = settings or load_settings()
    adapter = build_adapter(settings)
    inspector = Inspector(adapter)
    health = HealthService(settings)

    telemetry = telemetry or build_telemetry_client(
        enabled=settings.telemetry_enabled,
        mode=settings.effective_mode,
        version=__version__,
        transport=telemetry_transport,
    )

    mcp = FastMCP("topicforge")
    register_tools(mcp, inspector, health, telemetry)

    pro_enabled = _try_register_pro(mcp)

    log.info(
        "topicforge %s ready (mode=%s, adapter=%s, telemetry=%s, pro=%s)",
        __version__,
        settings.effective_mode,
        adapter.name,
        "on" if telemetry.enabled else "off",
        "on" if pro_enabled else "off",
    )
    return mcp


def _try_register_pro(mcp: FastMCP) -> bool:
    """Auto-detect `topicforge_pro` and let it register its tools.

    The pro package is an optional paid add-on distributed separately. Its
    `register` entrypoint is itself license-gated, so calling it with no
    valid `TOPICFORGE_LICENSE_KEY` is a logged no-op. Returns True when
    the package was found and called, False when not installed.
    """
    try:
        import topicforge_pro
    except ImportError:
        return False

    try:
        topicforge_pro.register(mcp)
    except Exception:
        # A failure inside the pro plugin must never break the free MVP.
        log.exception("topicforge-pro registration failed; continuing with core only")
        return False
    return True
