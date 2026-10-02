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

# `sample_messages` array handling. `ros2 topic echo` cuts arrays at 128
# elements unless told otherwise; `max_array_length` accepts 1..65536 or null
# (no cut).
DEFAULT_MAX_ARRAY_LENGTH = 128
MAX_ARRAY_LENGTH = 65536

# Cap on the serialized size of one sampled message; a call returns at most
# `MAX_SAMPLE_CALL_FACTOR` times that. Configurable with
# `TOPICFORGE_MAX_SAMPLE_BYTES` (`Settings.max_sample_bytes`).
DEFAULT_MAX_SAMPLE_BYTES = 1024 * 1024
MAX_SAMPLE_CALL_FACTOR = 4
