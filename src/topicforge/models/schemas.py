"""Tool input/output schemas.

Models are deliberately small, frozen, and JSON-friendly so MCP clients
(particularly LLMs) can reason about them without ambiguity. `extra="forbid"`
keeps adapters honest — an accidental extra key fails fast in tests rather
than silently propagating to clients.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

_CONFIG = ConfigDict(extra="forbid", frozen=True)


class TopicInfo(BaseModel):
    """Description of a single ROS2 topic."""

    model_config = _CONFIG

    name: str = Field(description="Fully qualified topic name, e.g. `/cmd_vel`.")
    message_type: str = Field(description="ROS2 message type, e.g. `geometry_msgs/msg/Twist`.")
    publisher_count: int = Field(ge=0, description="Publishers known to the graph.")
    subscriber_count: int = Field(ge=0, description="Subscribers known to the graph.")
    qos_reliability: str | None = Field(
        default=None,
        description="QoS reliability policy if known: `reliable` or `best_effort`.",
    )


class MessageSample(BaseModel):
    """A single sampled message on a topic."""

    model_config = _CONFIG

    topic: str = Field(
        description="Fully qualified topic name the sample was taken from, e.g. `/cmd_vel`."
    )
    message_type: str = Field(
        description="ROS2 message type of this sample, e.g. `geometry_msgs/msg/Twist`."
    )
    timestamp_ns: int = Field(
        description=(
            "Timestamp in nanoseconds since epoch. In live mode this is the "
            "`header.stamp` of the sampled message when present — the live "
            "adapter invokes `ros2 topic echo --csv --once`, whose flattened "
            "CSV exposes `header.stamp.sec`/`nanosec` as the first two "
            "columns for any `Header`-stamped message. **Headerless message "
            "types** (e.g. `std_msgs/String`, `geometry_msgs/Twist`) carry "
            "no embedded timestamp, and `timestamp_ns` falls back to 0; an "
            "rclpy-backed adapter will eventually expose rmw receive times "
            "for those. Mock mode emits monotonically increasing values for "
            "deterministic ordering."
        )
    )
    payload: dict[str, object] = Field(
        default_factory=dict,
        description=(
            "Structured message payload. **In live mode the MVP parser "
            "exposes the message fields as positional CSV columns** keyed "
            "as `col_0`, `col_1`, ... (`header.stamp.sec`/`nanosec` are "
            "stripped out into `timestamp_ns` when detected). The raw CSV "
            "row is preserved verbatim under the reserved `_raw_text` key "
            "so clients can re-parse against the message's IDL when needed. "
            "In mock mode the payload is fully structured and `_raw_text` "
            "is absent. Large messages (e.g. images) may be summarized to "
            "keep tool output bounded."
        ),
    )


class BagTopicStats(BaseModel):
    """Per-topic statistics inside a bag analysis result."""

    model_config = _CONFIG

    name: str = Field(description="Fully qualified topic name as recorded in the bag.")
    message_type: str = Field(description="ROS2 message type recorded for this topic.")
    message_count: int = Field(
        ge=0, description="Number of messages recorded on this topic across the bag."
    )
    frequency_hz: float | None = Field(
        default=None,
        ge=0,
        description="Average rate (messages / bag duration) when computable, else `null`.",
    )


class BagAnalysis(BaseModel):
    """Structured summary of a ROS2 bag."""

    model_config = _CONFIG

    path: str = Field(
        description=(
            "Path to the analyzed bag, as supplied by the caller. May point to a "
            "file (`.mcap`, `.db3`, `.bag`) or to a `rosbag2_*` directory."
        )
    )
    storage_format: str | None = Field(
        default=None,
        description="`mcap`, `sqlite3`, or other storage identifier when known.",
    )
    duration_seconds: float = Field(
        ge=0,
        description="Total bag duration, in seconds (wall clock between first and last message).",
    )
    message_count: int = Field(
        ge=0, description="Total number of messages across all recorded topics."
    )
    topics: list[BagTopicStats] = Field(
        description="Per-topic statistics for every topic present in the bag."
    )
    anomalies: list[str] = Field(
        default_factory=list,
        description=(
            "Human-readable notes about gaps, clock jumps, or other oddities. "
            "MVP populates this in mock mode; live anomaly detection is roadmap."
        ),
    )


class SampleResult(BaseModel):
    """Envelope returned by the `sample_messages` tool."""

    model_config = _CONFIG

    topic: str = Field(description="Topic the samples were taken from, echoed from the request.")
    count: int = Field(
        ge=0,
        description=(
            "Number of samples actually returned. May be 0 (no publisher active "
            "in live mode, or empty mock fixture), less than the requested count "
            "(topic yielded fewer messages within the timeout), or capped by the "
            "MVP's silent maximum of 50 — request `count > 50` and you will "
            "receive at most 50 without warning."
        ),
    )
    samples: list[MessageSample] = Field(
        description="The sampled messages, ordered as received from the backend."
    )


class HealthReport(BaseModel):
    """Result of `health_check`. Always succeeds, even when the host is unhealthy."""

    model_config = _CONFIG

    mode: str = Field(description="Effective runtime mode: `mock` or `live`.")
    requested_mode: str = Field(description="Mode requested via configuration (may be `auto`).")
    ros2_available: bool = Field(description="Whether a `ros2` CLI is on PATH.")
    ros2_distro: str | None = Field(
        default=None, description="Value of `ROS_DISTRO` if set in the environment."
    )
    server_version: str = Field(
        description="TopicForge server version (matches the PyPI release of the `topicforge` package)."
    )
    max_sample_count: int = Field(
        ge=0,
        description=(
            "Server-side cap on the number of samples returned per "
            "`sample_messages` call. Requests above this limit are silently "
            "clamped; the value is exposed here so a client can size its "
            "requests proactively. Constant within a given server version."
        ),
    )
