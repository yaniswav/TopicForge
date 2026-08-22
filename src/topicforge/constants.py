"""Package-wide constants shared across tools, services, and adapters.

Layer-neutral home for values consumed across the whole package : tool
handlers cap arguments here, services read them when constructing
clamped slices, adapters reference them when sizing their internal
buffers. Keeping the home at the root level (rather than under
`services/`) avoids reverse-layer imports (`adapters/` would otherwise
have to reach into `services.constants` and trigger a circular load
through `services/__init__.py`).

History : v0.2.0 moved `MAX_SAMPLE_COUNT` out of `services.inspector`
into `services.constants` (audit-2026-05-14 item A4). v0.5.x relocated
to this root module so the mock adapter and bag service can reuse it
without crossing the services->adapters layer in reverse.

If a constant graduates to runtime-configurable, move it onto
`Settings` and update callers accordingly.
"""

from __future__ import annotations

# Server-side cap on `sample_messages`, `peek_dds_samples`, and
# `peek_bag_samples` count parameters. Surfaced to clients via
# `HealthReport.max_sample_count`. Requests above this value are
# silently clamped to keep tool output bounded ; clients sizing their
# requests proactively should read the cap from the health endpoint
# rather than hardcoding it.
MAX_SAMPLE_COUNT = 50
