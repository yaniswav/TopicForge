"""Tool input/output schemas.

Models are deliberately small, frozen, and JSON-friendly so MCP clients
(particularly LLMs) can reason about them without ambiguity. `extra="forbid"`
keeps adapters honest — an accidental extra key fails fast in tests rather
than silently propagating to clients.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_CONFIG = ConfigDict(extra="forbid", frozen=True)

_MODE_EFFECTIVE_DESC = (
    "Runtime mode the adapter actually served this response in: `live` "
    "(real ROS2 introspection) or `mock` (deterministic fixtures). Always "
    "carried by the response so a downstream LLM can distinguish a real "
    "graph from a demo one without re-reading `health_check`."
)

# `mode_effective` is carried on `TopicInfo`, `SampleResult`, `BagAnalysis`,
# `ParticipantInfo`, and `MismatchReport` — every tool's primary response
# carrier — but **not** on `HealthReport` (which surfaces mode via its
# dedicated `mode`/`requested_mode` fields) or `MessageSample` (which
# nests inside `SampleResult`, whose envelope already carries the field).
# This asymmetry is deliberate ; do not add `mode_effective` to either
# without reviewing the wire contract.


class QosProfile(BaseModel):
    """DDS QoS profile snapshot for a single endpoint (reader or writer).

    MVP covers the four policies that explain over 80% of real-world
    "subscriber doesn't receive" cases. Vendor-specific extensions are
    intentionally ignored at MVP — `detect_qos_mismatches` compares against
    canonical DDS spec values only.
    """

    model_config = _CONFIG

    reliability: Literal["RELIABLE", "BEST_EFFORT"] = Field(
        description=(
            "DDS Reliability QoS. `RELIABLE` retries lost samples ; "
            "`BEST_EFFORT` does not. A `RELIABLE` reader cannot match a "
            "`BEST_EFFORT` writer."
        )
    )
    durability: Literal["VOLATILE", "TRANSIENT_LOCAL", "TRANSIENT", "PERSISTENT"] = Field(
        description=(
            "DDS Durability QoS. `VOLATILE` writers do not retain samples "
            "for late joiners ; `TRANSIENT_LOCAL` writers do. A "
            "`TRANSIENT_LOCAL` reader cannot match a `VOLATILE` writer."
        )
    )
    history: Literal["KEEP_LAST", "KEEP_ALL"] = Field(
        description=(
            "DDS History QoS. `KEEP_LAST` keeps a bounded ring buffer "
            "of size `history_depth` ; `KEEP_ALL` keeps every sample "
            "(memory permitting). Mixed `KEEP_ALL` reader with "
            "`KEEP_LAST` writer is risky but not strictly incompatible."
        )
    )
    history_depth: int | None = Field(
        default=None,
        ge=0,
        description="Depth for `KEEP_LAST`. `None` when policy is `KEEP_ALL`.",
    )
    deadline_ns: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Deadline QoS in nanoseconds. `None` means no deadline. "
            "A reader deadline tighter (smaller) than a writer deadline is "
            "incompatible — the writer cannot guarantee the reader's promise."
        ),
    )


class ParticipantInfo(BaseModel):
    """DDS participant discovered on the configured domain.

    v0.4.0 Phase 1 adds lifecycle fields (`first_seen_ns`, `last_seen_ns`,
    `status`, `seen_count`). All four are optional with safe defaults so
    v0.3.0 producers and fixtures keep working unchanged.
    """

    model_config = _CONFIG

    guid: str = Field(
        description=(
            "DDS Global Unique Identifier of the participant — hex string, "
            "stable across discovery events within a single deployment."
        )
    )
    vendor: Literal["cyclone", "fast", "rti", "mock", "unknown"] = Field(
        description=(
            "DDS implementation that announced this participant, decoded "
            "from the OMG-RTPS `vendor_id` field on the discovery sample. "
            "`cyclone` (Eclipse Foundation), `fast` (eProsima), `rti` "
            "(Real-Time Innovations). `mock` is reserved for synthetic "
            "fixtures ; `unknown` when the live adapter could not map "
            "the observed vendor_id to a known tag. Vendor-neutral: "
            "TopicForge observes every conformant DDS-RTPS participant "
            "on the bus via the OMG protocol guarantee — see "
            "`docs/dds-interop-matrix.md`."
        )
    )
    hostname: str | None = Field(
        default=None,
        description="Hostname announced by the participant, if available.",
    )
    domain_id: int = Field(
        ge=0,
        le=232,
        description="DDS domain id the participant is bound to.",
    )
    mode_effective: Literal["mock", "live"] = Field(description=_MODE_EFFECTIVE_DESC)
    first_seen_ns: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Wall-clock timestamp (nanoseconds since epoch) of the **first** "
            "discovery sample TopicForge observed for this participant. "
            "`None` when the adapter does not track lifecycle (v0.3.0 "
            "callers or mock fixtures missing the field). v0.4.0+ live "
            "adapters populate it."
        ),
    )
    last_seen_ns: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Wall-clock timestamp (nanoseconds since epoch) of the **most "
            "recent** discovery sample observed. Used by `participant_events` "
            "to distinguish currently-active participants from those that "
            "left the bus. `None` when the adapter does not track lifecycle."
        ),
    )
    status: Literal["active", "left", "unknown"] = Field(
        default="unknown",
        description=(
            "Lifecycle status. `active` means TopicForge has a recent "
            "discovery sample for this GUID and the bus has not signalled "
            "removal. `left` means the participant was observed earlier "
            "but has since disappeared (a Fast DDS `REMOVED` callback or a "
            "Cyclone polling delta). `unknown` is the safe default for "
            "v0.3.0 callers and fixtures missing the field."
        ),
    )
    seen_count: int = Field(
        default=1,
        ge=1,
        description=(
            "Number of distinct discovery samples observed for this "
            "participant across all calls to `list_participants` during "
            "this server's lifetime. `1` is the safe default ; v0.4.0+ "
            "live adapters increment on each observation."
        ),
    )


class ParticipantEvent(BaseModel):
    """A single lifecycle event for a DDS participant — discovered or lost.

    Distinct from `ParticipantInfo` because events carry intrinsic time
    + type semantics (point-in-time facts), while `ParticipantInfo` is a
    snapshot of current state. Returned by the `participant_events`
    MCP tool added in v0.4.0 Phase 1.
    """

    model_config = _CONFIG

    guid: str = Field(description="DDS Global Unique Identifier of the participant involved.")
    event_type: Literal["discovered", "lost"] = Field(
        description=(
            "`discovered` when the participant first joined the bus (or "
            "rejoined after leaving). `lost` when TopicForge observed a "
            "`REMOVED` callback (Fast DDS) or a polling delta where the "
            "GUID no longer appears in the DCPSParticipant builtin reader "
            "snapshot (Cyclone). The transition is reported once per "
            "state change."
        )
    )
    vendor: Literal["cyclone", "fast", "rti", "mock", "unknown"] = Field(
        description=(
            "DDS implementation tag for the participant involved, decoded "
            "the same way as `ParticipantInfo.vendor`."
        )
    )
    timestamp_ns: int = Field(
        ge=0,
        description=(
            "Wall-clock timestamp (nanoseconds since epoch) when TopicForge "
            "captured the event. For Fast DDS this is when the listener "
            "callback ran ; for Cyclone this is when the polling delta was "
            "computed ; for mock fixtures this is a deterministic anchor."
        ),
    )
    hostname: str | None = Field(
        default=None,
        description="Hostname announced by the participant when known, else `None`.",
    )
    domain_id: int = Field(
        ge=0,
        le=232,
        description="DDS domain id the event occurred on.",
    )
    mode_effective: Literal["mock", "live"] = Field(description=_MODE_EFFECTIVE_DESC)


class TopicMetrics(BaseModel):
    """Temporal metrics for a single DDS topic over a recent window.

    Added in v0.4.0 Phase 2 alongside the `topic_metrics` MCP tool.
    Built from samples that flow through the adapter's existing
    `peek_dds_samples` path — the buffer is **opportunistic**, not
    push-based, because neither `cyclonedds` nor `fastdds` Python
    bindings expose reliable at-sample-receive callbacks. Same
    caveat shape as Cyclone participant lifecycle in Phase 1: a
    sample bursting between two tool calls is invisible.

    Every numeric field is `None`-tolerant — fields collapse to
    `None` (or `0` for the integer-typed `sequence_gaps_count`)
    when the underlying data is unavailable: no samples observed,
    no source timestamps to compute latency, no sequence number
    embedded in the payload, etc. `samples_observed=0` is a valid
    response shape ; it means the tool ran successfully but the
    buffer had nothing to report for the requested window.
    """

    model_config = _CONFIG

    topic: str = Field(description="Topic the metrics were computed for.")
    window_seconds: int = Field(
        ge=1,
        le=3600,
        description=(
            "Requested window in seconds (1..3600). Echoed back from "
            "the tool call so the LLM can correlate the request."
        ),
    )
    window_seconds_actual: float = Field(
        ge=0,
        description=(
            "Actual elapsed seconds within the window. May be smaller "
            "than `window_seconds` when the adapter buffered samples "
            "for less time than the requested window (e.g., the server "
            "just started). `0.0` when `samples_observed=0`."
        ),
    )
    samples_observed: int = Field(
        ge=0,
        description=(
            "Number of samples in the buffer matching `topic` within "
            "the window. `0` means TopicForge has not seen any sample "
            "on this topic recently — it does NOT mean the topic has "
            "no publisher, only that no `peek_dds_samples` call "
            "captured one in the window."
        ),
    )
    frequency_hz_observed: float | None = Field(
        default=None,
        description=(
            "`samples_observed / window_seconds_actual`. `None` when "
            "fewer than 2 samples were observed (a single sample does "
            "not define a frequency)."
        ),
    )
    frequency_hz_declared: float | None = Field(
        default=None,
        description=(
            "Declared frequency extracted from the topic's QoS Deadline "
            "policy when the adapter resolved it (Deadline period → "
            "1 / period_seconds). `None` when the QoS profile does not "
            "include Deadline or the adapter could not resolve it. Use "
            "with `frequency_hz_observed` to diagnose a publisher that "
            "is failing its declared deadline."
        ),
    )
    sequence_gaps_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of missing sequence numbers detected in the "
            "buffered samples. `0` either means no gaps observed OR "
            "the sample type did not expose a sequence number (check "
            "`sequence_numbers_available` to disambiguate)."
        ),
    )
    sequence_numbers_available: bool = Field(
        default=False,
        description=(
            "True when the adapter successfully extracted sequence "
            "numbers from at least one sample. Sequence number support "
            "depends on the message type — `Header`-stamped messages "
            "with a `seq` field expose it ; primitives like "
            "`std_msgs/String` do not."
        ),
    )
    latency_ns_p50: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Median publish-to-receive latency in nanoseconds, "
            "computed only when the sample type exposes a publish "
            "timestamp (typically via `header.stamp` on "
            "`Header`-stamped messages). `None` when "
            "`latency_available=False`."
        ),
    )
    latency_ns_p95: int | None = Field(
        default=None,
        ge=0,
        description="95th-percentile publish-to-receive latency (ns).",
    )
    latency_ns_p99: int | None = Field(
        default=None,
        ge=0,
        description="99th-percentile publish-to-receive latency (ns).",
    )
    latency_available: bool = Field(
        default=False,
        description=(
            "True when at least one sample in the window carried both "
            "a publish timestamp and a receive timestamp. The percentile "
            "fields are `None` when this is False."
        ),
    )
    mode_effective: Literal["mock", "live"] = Field(description=_MODE_EFFECTIVE_DESC)


class MismatchReport(BaseModel):
    """A single reader/writer QoS incompatibility detected on a topic."""

    model_config = _CONFIG

    topic: str = Field(description="Topic name where the mismatch was detected.")
    reader_guid: str | None = Field(
        default=None,
        description="GUID of the reader endpoint involved in the mismatch, if known.",
    )
    writer_guid: str | None = Field(
        default=None,
        description="GUID of the writer endpoint involved in the mismatch, if known.",
    )
    incompatible_policies: list[str] = Field(
        description=(
            "Names of the QoS policies that block communication or risk "
            "degradation. Drawn from the MVP set: `Reliability`, "
            "`Durability`, `History`, `Deadline`."
        )
    )
    severity: Literal["incompatible", "risky"] = Field(
        description=(
            "`incompatible` means communication is definitely blocked ; "
            "`risky` means it may degrade but is not strictly blocked by "
            "the DDS spec. Useful for an LLM to triage user-facing advice."
        )
    )
    mode_effective: Literal["mock", "live"] = Field(description=_MODE_EFFECTIVE_DESC)


class TopicInfo(BaseModel):
    """Description of a single ROS2 topic.

    Carries optional DDS-side enrichment fields when the active middleware
    backend can resolve them (CycloneDDS / RTI). The ROS2 CLI adapter and
    the mock ROS2 path leave them `None`.
    """

    model_config = _CONFIG

    name: str = Field(description="Fully qualified topic name, e.g. `/cmd_vel`.")
    message_type: str = Field(description="ROS2 message type, e.g. `geometry_msgs/msg/Twist`.")
    publisher_count: int = Field(ge=0, description="Publishers known to the graph.")
    subscriber_count: int = Field(ge=0, description="Subscribers known to the graph.")
    qos_reliability: str | None = Field(
        default=None,
        description="QoS reliability policy if known: `reliable` or `best_effort`.",
    )
    reader_count: int | None = Field(
        default=None,
        ge=0,
        description=(
            "DDS reader-endpoint count when the active backend can resolve "
            "endpoint-level info (Cyclone / RTI). `None` from the ROS2 CLI "
            "adapter or when the DDS module is inactive."
        ),
    )
    writer_count: int | None = Field(
        default=None,
        ge=0,
        description=(
            "DDS writer-endpoint count when the active backend can resolve "
            "endpoint-level info. `None` from the ROS2 CLI adapter or when "
            "the DDS module is inactive."
        ),
    )
    qos_profile: QosProfile | None = Field(
        default=None,
        description=(
            "Effective DDS QoS profile for this topic when resolvable. "
            "`None` from the ROS2 CLI adapter or when the DDS module is "
            "inactive. The DDS module populates this on a best-effort basis "
            "(picks one representative endpoint if reader/writer QoS differ)."
        ),
    )
    mode_effective: Literal["mock", "live"] = Field(description=_MODE_EFFECTIVE_DESC)


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
    """Structured summary of a ROS2 bag.

    v0.4.0 Phase 3 enriches this model with four **additive optional**
    fields (`bag_format`, `samples_decoded_count`, `recording_duration_ns`,
    `participants_recorded`) populated when the new `rosbags`-backed
    bag service runs. The v0.3.0 `ros2 bag info`-text-parsed path
    leaves them at their safe defaults so every existing consumer
    keeps working unchanged.
    """

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
    mode_effective: Literal["mock", "live"] = Field(description=_MODE_EFFECTIVE_DESC)
    bag_format: Literal["mcap", "db3", "bag", "unknown"] | None = Field(
        default=None,
        description=(
            "Concrete bag container format detected by the reader — `mcap` "
            "(Foxglove MCAP), `db3` (ROS2 rosbag2 SQLite), `bag` (ROS1 "
            "legacy chunked), or `unknown` when the reader could not "
            "classify. `None` for the v0.3.0 `ros2 bag info`-text-parsed "
            "code path that has no format awareness. Added in v0.4.0 "
            "Phase 3 alongside the `rosbags`-backed bag service."
        ),
    )
    samples_decoded_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Total decoded sample count across all topics produced by the "
            "bag reader. `0` when the reader only parsed metadata (the "
            "v0.3.0 text-parsed path) or when `rosbags` is not installed "
            "on the host. Use `peek_bag_samples` to pull the actual "
            "sample payloads for a specific topic."
        ),
    )
    recording_duration_ns: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Recording duration in nanoseconds when readable from the "
            "bag's index. `None` when the v0.3.0 text-parsed path runs ; "
            "`duration_seconds` (float) is the always-populated fallback "
            "that downstream LLM consumers should prefer when this is "
            "`None`."
        ),
    )
    participants_recorded: list[ParticipantInfo] = Field(
        default_factory=list,
        description=(
            "DDS participants recorded in the bag when the container "
            "format embeds participant metadata. MCAP can carry it via "
            "channel metadata records ; ROS2 `.db3` and ROS1 `.bag` "
            "generally do not. Empty list when not available — the "
            "common case at v0.4.0 Phase 3."
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
    mode_effective: Literal["mock", "live"] = Field(description=_MODE_EFFECTIVE_DESC)


class HealthReport(BaseModel):
    """Result of `health_check`. Always succeeds, even when the host is unhealthy."""

    model_config = _CONFIG

    mode: str = Field(description="Effective runtime mode: `mock` or `live`.")
    requested_mode: str = Field(description="Mode requested via configuration (may be `auto`).")
    ros2_available: bool = Field(description="Whether a `ros2` CLI is on PATH.")
    ros2_distro: str | None = Field(
        default=None,
        description=(
            "Value of `ROS_DISTRO` if set in the environment. **Env "
            "disclosure, by design** — under the local-trust threat model "
            "(see README 'Security model'), the MCP client is a trusted "
            "agent on a machine the user controls, and exposing the ROS2 "
            "distro lets it adapt to e.g. `humble`/`jazzy` differences. "
            "For a hosted multi-tenant TopicForge endpoint this field "
            "would be scrubbed ; see the security audit roadmap."
        ),
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
    dds_backend: Literal[
        "mock",
        "cyclone",
        "fast",
        "rti",
        "opensplice",
        "coredx",
        "intercom",
        "opendds",
        "dust",
        "none",
    ] = Field(
        default="none",
        description=(
            "Active DDS module backend. `none` when the DDS module is not "
            "active (default for ROS2-only installs). `mock` for synthetic "
            "fixtures. **OSS tier** — `cyclone` requires "
            "`pip install topicforge[dds-cyclone]` (Eclipse CycloneDDS) ; "
            "`fast` requires `pip install topicforge[dds-fast]` (eProsima "
            "Fast DDS) ; `opendds` and `dust` are stub adapters in v0.4.0 "
            "Phase 1.5 (no maintained Python binding on PyPI yet — install "
            "`pip install topicforge[dds-opendds]` / `[dds-dust]` to "
            "exercise the auto-detect hook). **Pro tier** — `rti`, "
            "`opensplice`, `coredx`, `intercom` require the `topicforge-pro` "
            "package and a valid `TOPICFORGE_LICENSE_KEY`."
        ),
    )
    dds_domain_id: int | None = Field(
        default=None,
        ge=0,
        le=232,
        description="DDS domain id observed when the DDS module is active.",
    )
    middleware_available: bool = Field(
        default=False,
        description=(
            "Whether the configured DDS backend is importable. False when "
            "the DDS module is inactive (`dds_backend == 'none'`) or when "
            "the backend's Python bindings are not installed."
        ),
    )
    ros_backend: Literal["mock", "ros2_cli", "none"] = Field(
        default="none",
        description=(
            "Active ROS2 backend. `ros2_cli` when the `ros2` CLI is on "
            "PATH and live mode resolves to a Ros2CliAdapter (alone or "
            "as the ROS half of a composite). `mock` when MockAdapter "
            "serves the ROS surface. `none` when no ROS2 backend is "
            "active (e.g. DDS-only live install with no `ros2` CLI). "
            "Added in v0.4.0 Phase 1 alongside the composite adapter so "
            "clients can distinguish the ROS2 and DDS halves of a "
            "composed runtime."
        ),
    )
