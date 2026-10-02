"""Opt-in, anonymous usage telemetry.

Off by default. With `TOPICFORGE_TELEMETRY=on`, each tool call emits the tool
name, latency, runtime mode, server version, success flag and a per-process
anonymous session id, and nothing else. The default transport logs a line.
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
