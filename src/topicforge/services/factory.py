"""Adapter selection.

This is the only place that knows how to map a `Settings` to a concrete
adapter, and where graceful degradation from `live` → `mock` happens.
"""

from __future__ import annotations

import logging

from topicforge.adapters.base import RosAdapter
from topicforge.adapters.ros2_live import Ros2CliAdapter
from topicforge.adapters.ros2_mock import MockAdapter
from topicforge.config import Settings

log = logging.getLogger(__name__)


def build_adapter(settings: Settings) -> RosAdapter:
    """Return the adapter matching the effective runtime mode.

    If `live` is requested but the `ros2` CLI is missing, falls back to mock
    and logs a warning. This is intentional: it keeps the MCP server usable
    on a developer laptop without ROS2 installed.

    Predictive resolution (mode `auto` → live or mock based on PATH) lives in
    `config/settings.py:Settings.effective_mode`.
    """
    mode = settings.effective_mode
    if mode == "live":
        adapter = Ros2CliAdapter(executable=settings.ros2_executable)
        if not adapter.is_available():
            log.warning(
                "live mode requested but %r not on PATH; falling back to mock",
                settings.ros2_executable,
            )
            return MockAdapter()
        return adapter
    return MockAdapter()
