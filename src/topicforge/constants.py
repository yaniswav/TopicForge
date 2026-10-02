"""Package-wide constants shared across tools, services, and adapters.

Lives at the package root so adapters can import it without reaching into
`services/` (a reverse-layer import and a circular load). A constant that
becomes configurable moves onto `Settings`.
"""

from __future__ import annotations

# Cap on the `count` parameter of `sample_messages`, `peek_dds_samples` and
# `peek_bag_samples`. Larger requests are silently clamped. Exposed to
# clients as `HealthReport.max_sample_count`.
MAX_SAMPLE_COUNT = 50
