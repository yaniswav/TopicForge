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
from topicforge.tools import register_tools

log = logging.getLogger(__name__)


def build_app(settings: Settings | None = None) -> FastMCP:
    """Construct a fully wired FastMCP application.

    `settings` is optional so tests can build the app with deterministic
    configuration; production callers (`python -m topicforge`) pass nothing
    and pick up settings from the environment.
    """
    settings = settings or load_settings()
    adapter = build_adapter(settings)
    inspector = Inspector(adapter)
    health = HealthService(settings)

    mcp = FastMCP("topicforge")
    register_tools(mcp, inspector, health)

    log.info(
        "topicforge %s ready (mode=%s, adapter=%s)",
        __version__,
        settings.effective_mode,
        adapter.name,
    )
    return mcp
