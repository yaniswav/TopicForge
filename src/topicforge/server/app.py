"""MCP server bootstrap: wires settings -> adapter -> services -> tools -> FastMCP."""

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
    """Build the FastMCP application.

    `settings` defaults to the environment. `telemetry` lets tests inject a
    client; otherwise one is built from settings with `telemetry_transport`
    (default: the logging transport).
    """
    settings = settings or load_settings()
    adapter = build_adapter(settings)
    inspector = Inspector(adapter, max_sample_bytes=settings.max_sample_bytes)
    health = HealthService(settings, adapter)

    telemetry = telemetry or build_telemetry_client(
        enabled=settings.telemetry_enabled,
        mode=adapter.effective_mode,
        version=__version__,
        transport=telemetry_transport,
    )

    mcp = FastMCP("topicforge")
    register_tools(mcp, inspector, health, telemetry)

    log.info(
        "topicforge %s ready (mode=%s, requested_mode=%s, adapter=%s, telemetry=%s)",
        __version__,
        adapter.effective_mode,
        settings.mode,
        adapter.name,
        "on" if telemetry.enabled else "off",
    )
    return mcp
