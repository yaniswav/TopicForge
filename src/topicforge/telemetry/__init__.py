"""Opt-in, anonymous usage telemetry.

Disabled by default. When enabled via `TOPICFORGE_TELEMETRY=on`, each MCP
tool call emits a small event describing only the tool name, latency,
runtime mode, server version, success flag, and a per-process anonymous
session id. No user payload — topic names, message bodies, bag paths,
hostnames, environment variables — ever leaves this module.

The transport is pluggable. The default is a structured log line; a future
HTTP transport will be wired here without touching tool handlers.
"""

from topicforge.telemetry.client import (
    TelemetryClient,
    TelemetryEvent,
    Transport,
    build_telemetry_client,
    instrument,
)

__all__ = [
    "TelemetryClient",
    "TelemetryEvent",
    "Transport",
    "build_telemetry_client",
    "instrument",
]
