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

# `sample_messages` wall deadline in seconds, from the start of the call (topic
# lookup and CLI start-up included). `timeout_s` accepts MIN..MAX. The maximum
# stays under the 60 s request timeout some MCP clients apply, which must also
# cover stopping the CLI and decoding (about 2 s more).
DEFAULT_SAMPLE_TIMEOUT_S = 10.0
MIN_SAMPLE_TIMEOUT_S = 1.0
MAX_SAMPLE_TIMEOUT_S = 45.0

# Reserved payload key listing the dotted paths of fields `sample_messages`
# cut at `max_array_length` (arrays, strings, bytes).
TRUNCATED_FIELDS_KEY = "_truncated_fields"
