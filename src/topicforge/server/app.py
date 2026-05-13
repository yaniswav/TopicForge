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

    log.info(
        "topicforge %s ready (mode=%s, adapter=%s, telemetry=%s)",
        __version__,
        settings.effective_mode,
        adapter.name,
        "on" if telemetry.enabled else "off",
    )
    return mcp
