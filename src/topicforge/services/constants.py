"""Domain constants shared across services and tools.

This module hosts values that multiple service-layer modules read but
that don't conceptually belong to any single one. Keeping them here
avoids cross-service imports (e.g. `services.health` reaching into
`services.inspector` for `MAX_SAMPLE_COUNT`) — flagged by the v0.1.2
architecture audit as a smell that compounds once DDS adds its own
per-tool caps.

If a constant graduates to runtime-configurable, move it onto `Settings`
and update callers accordingly.
"""

from __future__ import annotations

# Server-side cap on `sample_messages` and `peek_dds_samples` count
# parameters. Surfaced to clients via `HealthReport.max_sample_count`.
# Requests above this value are silently clamped to keep tool output
# bounded ; clients sizing their requests proactively should read the
# cap from the health endpoint rather than hardcoding it.
MAX_SAMPLE_COUNT = 50
