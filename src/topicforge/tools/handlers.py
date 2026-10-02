"""MCP tool handlers.

Handlers are deliberately thin: they delegate to services and let FastMCP
serialize the returned Pydantic models. They never touch ROS2 directly.

`AdapterError` (and any other unexpected exception) is allowed to bubble up.
FastMCP translates it into an MCP-native error response (`isError: true`
in the JSON-RPC payload), which every compliant MCP client knows how to
handle. We deliberately do **not** wrap errors in a custom envelope: that
pattern masks failures as successful tool results and forces the client to
parse the body to discover something went wrong.

Future tools (URDF inspection, bag anomaly detection, dataset export) plug
in here behind the same shape:

    @mcp.tool(description="...")
    def my_tool(...) -> MyResult:
        return service.do_thing(...)
"""

from __future__ import annotations

from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from topicforge.models import (
    BagAnalysis,
    EndpointListing,
    HealthReport,
    MismatchReport,
    ParticipantEvent,
    ParticipantInfo,
    SampleResult,
    TopicInfo,
    TopicMetrics,
)
from topicforge.services import HealthService, Inspector
from topicforge.telemetry import TelemetryClient, instrument

_TOPIC_PARAM_DESC = (
    "Fully qualified ROS2 topic name starting with `/`, e.g. `/cmd_vel` or "
    "`/camera/image_raw`. Each `/`-separated segment must start with a "
    "letter or underscore and contain only letters, digits, and "
    "underscores; everything else (whitespace, quotes, shell "
    "metacharacters, `//`, trailing `/`) is rejected before reaching the "
    "`ros2` CLI."
)

_DDS_TOPIC_PARAM_DESC = (
    "DDS topic name. Accepts ROS2-style names (`/rt/chatter`) and the "
    "builtin discovery topics `DCPSParticipant`, `DCPSSubscription`, "
    "`DCPSPublication`, which have no leading `/`. Letters, digits, `_`, "
    "`/` and `::` are allowed; anything else is rejected before reaching "
    "the DDS layer."
)

_COUNT_PARAM_DESC = (
    "Maximum number of recent messages to return. Defaults to 5; silently "
    "clamped to 50 (the hard cap that keeps tool output bounded; read it "
    "from `health_check.max_sample_count`). Negative values raise an error. "
    "The returned `SampleResult.count` reflects the actual number of "
    "samples produced: it can be lower than the request (empty topic, "
    "timeout, mock fixture shorter than requested)."
)

_PATH_PARAM_DESC = (
    "Path to a ROS2 bag: a file ending in `.mcap`, `.db3`, or `.bag`, or a "
    "`rosbag2_*` directory. Leading/trailing whitespace is stripped. Null "
    "bytes and otherwise malformed filesystem paths are rejected. Existence "
    "and bag format are validated by the live adapter (mock mode accepts "
    "any well-formed path)."
)


def register_tools(
    mcp: FastMCP,
    inspector: Inspector,
    health: HealthService,
    telemetry: TelemetryClient,
) -> None:
    """Register the MVP tool set on `mcp`.

    `telemetry` is required but inert by default: when disabled (the
    default), `instrument(...)` returns the handler unchanged, so opt-out
    means zero overhead and zero network code paths in the call stack.
    """

    @mcp.tool(
        description=(
            "Report TopicForge environment state: effective runtime mode "
            '(`"live"` or `"mock"`), whether the `ros2` CLI is on PATH, '
            "`ROS_DISTRO`, the server version, the server-side sample cap, "
            "the active DDS backend (`mock`/`cyclone`/`fast`/`none`), and "
            "the observed DDS domain id when applicable. Returns a "
            "`HealthReport`. **Always succeeds**. Call this first when "
            "something looks wrong, since every other tool may raise. "
            "Read-only ; no side effects."
        )
    )
    @instrument(telemetry, "health_check")
    def health_check() -> HealthReport:
        return health.report()

    @mcp.tool(
        description=(
            "List every ROS2 topic on the current graph (or the deterministic "
            "mock graph in mock mode). Returns `list[TopicInfo]`: each entry "
            "carries `name`, `message_type`, `publisher_count`, "
            '`subscriber_count`, `qos_reliability`, and `mode_effective` (`"live"` '
            'or `"mock"`) so a downstream LLM can distinguish a real graph from '
            "demo fixtures. **Empty list** when the graph has no topics or "
            "when live discovery times out. Read-only ; no side effects."
        )
    )
    @instrument(telemetry, "list_topics")
    def list_topics() -> list[TopicInfo]:
        return inspector.list_topics()

    @mcp.tool(
        description=(
            "Return detailed info for a single ROS2 topic. `topic` must be a "
            "fully qualified topic name, e.g. `/cmd_vel`. Returns a `TopicInfo` "
            'carrying `mode_effective` (`"live"` or `"mock"`) so callers can '
            "distinguish a real-graph hit from a mock fixture. **Raises an MCP "
            "error** (isError=true) if the topic name is malformed or the topic "
            "is unknown to the active graph. Read-only ; no side effects."
        )
    )
    @instrument(telemetry, "get_topic_info")
    def get_topic_info(
        topic: Annotated[str, Field(description=_TOPIC_PARAM_DESC)],
    ) -> TopicInfo:
        return inspector.get_topic_info(topic)

    @mcp.tool(
        description=(
            "Peek up to `count` recent ROS2 messages from `topic`, sampled "
            "from the runtime graph. `topic` must be a fully qualified name "
            "(see the `topic` parameter description). `count` defaults to 5 "
            "and is silently clamped to 50: request more and you receive at "
            "most 50 without warning. Returns a `SampleResult` envelope "
            "`{topic, count, samples, mode_effective}` where `count` is the "
            "actual number of samples returned (may be 0) and `mode_effective` "
            'is `"live"` or `"mock"`. '
            "**Live mode** shells out to `ros2 topic echo --csv --once` with "
            "a short timeout, so the result is empty when no publisher is "
            "currently active. `samples[i].timestamp_ns` is the message's "
            "`header.stamp` (publish time) when the message is `Header`-stamped, "
            "and 0 for headerless types (e.g. `std_msgs/String`). The live "
            "parser exposes fields as positional CSV columns under "
            "`samples[i].payload` keys `col_0`, `col_1`, ..., with the verbatim "
            "CSV row under the reserved `_raw_text` key. **Mock mode** returns "
            "deterministic samples with monotonically increasing timestamps "
            "for the fictional demo robot (and no `_raw_text` key, since the "
            "payload is already structured). "
            "Read-only ; never publishes to the bus. **Distinct from "
            "`peek_dds_samples`**: that tool reads the raw DDS layer ; this "
            "one reads the ROS2 graph."
        )
    )
    @instrument(telemetry, "sample_messages")
    def sample_messages(
        topic: Annotated[str, Field(description=_TOPIC_PARAM_DESC)],
        count: Annotated[int, Field(description=_COUNT_PARAM_DESC, ge=0)] = 5,
    ) -> SampleResult:
        return inspector.sample_messages(topic, count)

    @mcp.tool(
        description=(
            "Summarize a ROS2 bag at `path`. Returns a `BagAnalysis` carrying "
            "storage format, total duration, message count, per-topic stats, "
            "detected anomalies, and `mode_effective` "
            '(`"live"` or `"mock"`) so callers can tell a real bag analysis '
            "from a mock fixture. **Live mode** shells out to `ros2 bag info` "
            "and accepts `.mcap`, `.db3`, and `.bag` files plus `rosbag2_*` "
            "directories ; **mock mode** returns rich fixture data regardless "
            "of path suffix (except blatantly non-bag extensions). "
            "**Raises an MCP error** if the path is malformed, missing in live "
            "mode, or unparseable. Deep anomaly detection is mock-only at MVP. "
            "Read-only ; no side effects."
        )
    )
    @instrument(telemetry, "analyze_bag")
    def analyze_bag(
        path: Annotated[str, Field(description=_PATH_PARAM_DESC, min_length=1)],
    ) -> BagAnalysis:
        return inspector.analyze_bag(path)

    # ----- DDS module tools (v0.2.0) -----
    # The 3 tools below address the bare-DDS layer, distinct from the
    # ROS2-graph tools above. They are active when TOPICFORGE_DDS_BACKEND
    # is `cyclone`, `fast`, or `mock`. With the `ros2_cli` adapter (default
    # for ROS2-only installs), they raise AdapterError pointing at the
    # `pip install topicforge[dds]` remediation path.

    @mcp.tool(
        description=(
            "List DDS participants observed on a domain. Returns "
            "`list[ParticipantInfo]`: each entry carries `guid`, `vendor` "
            "(`cyclone`/`fast`/`rti`/`rti_micro`/`opensplice`/`opendds`/"
            "`coredx`/`intercom`/`dust`/`mock`/`unknown`, the vendor observed "
            "on the bus), optional `name` (announced EntityName QoS, e.g. "
            "`lidar_driver`), optional `hostname` (announced in discovery), "
            '`domain_id`, and `mode_effective` (`"live"`/`"mock"`). '
            "**Distinct from ROS2 graph nodes**: operates at the raw DDS "
            "layer beneath ROS, useful for non-ROS DDS stacks or for "
            "diagnosing why a participant isn't seen by the ROS graph. "
            "**Read-only by architecture**: the underlying "
            "`MiddlewareAdapter` protocol does not expose a write method, "
            "so this tool cannot publish, modify QoS, or alter the bus. "
            "**Raises an MCP error** when no DDS module is active "
            "(install `pip install topicforge[dds]` and set "
            "`TOPICFORGE_DDS_BACKEND=cyclone`). The mock backend "
            "returns deterministic fixtures."
        )
    )
    @instrument(telemetry, "list_participants")
    def list_participants(
        domain_id: Annotated[
            int,
            Field(
                description=(
                    "DDS domain id to observe (0..232). Defaults to 0: "
                    "the same default used by `cyclonedds` and the "
                    "implicit default of most ROS2 setups."
                ),
                ge=0,
                le=232,
            ),
        ] = 0,
    ) -> list[ParticipantInfo]:
        return inspector.list_participants(domain_id)

    @mcp.tool(
        description=(
            "Detect DDS QoS incompatibilities between reader and writer "
            "endpoints on the bus. Returns `list[MismatchReport]`: one "
            "entry per incompatible (reader, writer) pair, listing the "
            "policies that block or risk degrading communication "
            "(Reliability, Durability, History, Deadline at MVP). Each "
            "report carries `severity` (`incompatible` strictly blocks "
            "communication per the DDS spec ; `risky` may degrade but "
            'is not strictly blocked) and `mode_effective` (`"live"`/'
            '`"mock"`). Pass `topic` to scope to a single topic ; omit '
            "for an exhaustive scan. **Use this when** an LLM is "
            "debugging why a subscriber doesn't receive. **Read-only "
            "by architecture**: the analyzer compares observed QoS "
            "profiles ; no method on this tool can rewrite QoS or "
            "alter the bus. **Raises an MCP error** when no DDS module "
            "is active ; the mock backend returns deterministic "
            "fixtures."
        )
    )
    @instrument(telemetry, "detect_qos_mismatches")
    def detect_qos_mismatches(
        topic: Annotated[
            str | None,
            Field(
                description=(
                    "Optional topic name to scope the scan to "
                    "(`/foo/bar` shape). `None` (default) returns all "
                    "mismatches across all topics."
                ),
                default=None,
            ),
        ] = None,
    ) -> list[MismatchReport]:
        return inspector.detect_qos_mismatches(topic)

    @mcp.tool(
        description=(
            "Peek up to `count` recent samples on a raw DDS topic. "
            "**Distinct from `sample_messages`**: `sample_messages` "
            "operates on the ROS2 graph via `ros2 topic echo` ; this "
            "tool reads directly from the DDS layer (Cyclone / Fast / "
            "mock). Use this for non-ROS DDS topics or when the "
            "ROS2 CLI is not available. Returns a `SampleResult` "
            "envelope `{topic, count, samples, mode_effective}`: "
            "identical shape to `sample_messages`. `count` defaults to "
            "5 and is silently clamped to 50. "
            "**Topic categories**: "
            "(a) The 3 builtin DCPS topics (`DCPSParticipant`, "
            "`DCPSSubscription`, `DCPSPublication`) return structured "
            "discovery payloads. "
            "(b) User-defined topics: payload decoding is DISABLED in "
            "this release on every backend. The tool confirms the topic "
            "is announced on the bus and returns one placeholder sample "
            'with `_decode_status="raw"`, an explanatory `_decode_note` '
            "and an empty `_raw_bytes_hex`. It does NOT return message "
            "content for user topics: do not infer field values from it. "
            "**Read-only by architecture**: the "
            "`MiddlewareAdapter` protocol does not expose a write "
            "method. **Raises an MCP error** when no DDS module is "
            "active OR when the topic is not announced on the bus."
        )
    )
    @instrument(telemetry, "peek_dds_samples")
    def peek_dds_samples(
        topic: Annotated[str, Field(description=_DDS_TOPIC_PARAM_DESC)],
        count: Annotated[int, Field(description=_COUNT_PARAM_DESC, ge=0)] = 5,
    ) -> SampleResult:
        return inspector.peek_dds_samples(topic, count)

    @mcp.tool(
        description=(
            "Return DDS participant lifecycle events (`discovered` / `lost`) "
            "captured over a recent window. Use this when an LLM needs to "
            "answer *'who was on the bus 5 minutes ago and left?'* or "
            '*"when did this participant first appear?"*. Returns '
            "`list[ParticipantEvent]`: each entry carries `guid`, "
            "`event_type`, `vendor`, `timestamp_ns` (wall-clock ns since "
            "epoch), optional `name` (the participant's announced DDS name), "
            "optional `hostname`, `domain_id`, and `mode_effective` "
            '(`"live"`/`"mock"`). Sorted newest-first. Hard cap at 200 '
            "events (silent truncation, mirrors `sample_messages`'s 50 cap "
            "; reduce `lookback_seconds` if you hit it). "
            "**Read-only by architecture**: the underlying "
            "`MiddlewareAdapter` protocol does not expose a write method. "
            "**Backend caveats**: Fast DDS captures arrivals AND removals "
            "via listener callbacks ; Cyclone tracks lifecycle only across "
            "`list_participants` calls (a participant that joined and left "
            "between two polls is invisible) ; mock returns a deterministic "
            "fixture timeline. **Raises an MCP error** when no DDS module "
            "is active (install `pip install topicforge[dds]` and set "
            "`TOPICFORGE_DDS_BACKEND=cyclone|fast`)."
        )
    )
    @instrument(telemetry, "participant_events")
    def participant_events(
        domain_id: Annotated[
            int,
            Field(
                description=(
                    "DDS domain id to filter events on (0..232). Defaults "
                    "to 0: the same default used by `cyclonedds` and "
                    "most ROS2 setups."
                ),
                ge=0,
                le=232,
            ),
        ] = 0,
        lookback_seconds: Annotated[
            int,
            Field(
                description=(
                    "Window (in seconds) over which to return events. "
                    "Defaults to 300 (5 minutes). Range: 1..86400 (1 second "
                    "to 24 hours). Larger windows may hit the 200-event "
                    "cap: narrow the window or filter on `domain_id` "
                    "when that happens."
                ),
                ge=1,
                le=86400,
            ),
        ] = 300,
    ) -> list[ParticipantEvent]:
        return inspector.participant_events(domain_id, lookback_seconds)

    @mcp.tool(
        description=(
            "Return temporal metrics (frequency, sequence gaps, latency "
            "percentiles) for a DDS topic over a recent time window. Use "
            "this when an LLM needs to diagnose *'is this topic actually "
            "publishing at the rate its QoS Deadline declares?'*, *'are "
            "there missing sequence numbers?'*, or *'what is the p99 "
            "latency on this topic?'*. Returns a `TopicMetrics` payload "
            "carrying `samples_observed`, `frequency_hz_observed`, "
            "`frequency_hz_declared` (from QoS Deadline when known), "
            "`sequence_gaps_count`, `latency_ns_p50/p95/p99`, and "
            "boolean availability flags. **Limits, read before "
            "concluding anything**: the buffer is filled only when "
            "`peek_dds_samples` runs on the topic, and only by samples "
            "actually received, which today means the builtin DCPS "
            "topics. A user topic always returns `samples_observed=0` "
            "because its payload is not decoded. `frequency_hz_observed` "
            "reflects how often `peek_dds_samples` was called, not the "
            "real publish rate, and `frequency_hz_declared` is not "
            "populated. Treat the result as a coarse presence signal, "
            "not a rate measurement. **Read-only by architecture**: no "
            "method on this tool writes to the bus. **Raises an MCP "
            "error** when no DDS module is active or `window_seconds` "
            "is out of range (1..3600)."
        )
    )
    @instrument(telemetry, "topic_metrics")
    def topic_metrics(
        topic: Annotated[str, Field(description=_DDS_TOPIC_PARAM_DESC)],
        window_seconds: Annotated[
            int,
            Field(
                description=(
                    "Window in seconds over which to compute metrics "
                    "(1..3600). Defaults to 60 seconds. Smaller windows "
                    "reflect more recent state ; larger windows smooth "
                    "transient anomalies."
                ),
                ge=1,
                le=3600,
            ),
        ] = 60,
        domain_id: Annotated[
            int,
            Field(
                description=(
                    "DDS domain id to scope the metrics to (0..232). "
                    "Defaults to 0: the same default as the rest of "
                    "the DDS tools."
                ),
                ge=0,
                le=232,
            ),
        ] = 0,
    ) -> TopicMetrics:
        return inspector.topic_metrics(topic, window_seconds, domain_id)

    @mcp.tool(
        description=(
            "Peek up to `count` recent samples from a recorded bag file. "
            "**Distinct from `peek_dds_samples`** (live bus introspection) "
            "and `sample_messages` (ROS2 graph live peek): this tool "
            "operates on **offline bag content** for post-mortem analysis. "
            "Supported formats: MCAP (`.mcap`), ROS2 rosbag2 SQLite "
            "(`.db3`), ROS1 legacy chunked binary (`.bag`). The reader "
            "auto-detects format from the file extension. Returns a "
            "`SampleResult` envelope identical in shape to "
            "`peek_dds_samples`: each sample's `payload` carries the "
            "same `_decode_status` annotation (`full` / `partial` / "
            "`raw`) so an LLM consumer reads one shape across live and "
            "recorded sources. `count` defaults to 5 and is silently "
            "clamped to 50. **Requires the `rosbags` library** "
            "(`pip install topicforge[bags]`): without it, raises an "
            "`AdapterError` with the install command. The mock backend "
            "returns deterministic fixture samples on canned bag paths. "
            "**Read-only by architecture**: no method writes to the "
            "bag file. **Raises an MCP error** when the bag path does "
            "not exist, the topic is not present in the bag, or rosbags "
            "is not installed. Added in v0.4.0 Phase 3: the 11th MCP "
            "tool ; 8-tool ceiling break #3, acknowledged in CHANGELOG."
        )
    )
    @instrument(telemetry, "peek_bag_samples")
    def peek_bag_samples(
        path: Annotated[str, Field(description=_PATH_PARAM_DESC, min_length=1)],
        topic: Annotated[str, Field(description=_TOPIC_PARAM_DESC)],
        count: Annotated[int, Field(description=_COUNT_PARAM_DESC, ge=0)] = 5,
    ) -> SampleResult:
        return inspector.peek_bag_samples(path, topic, count)

    @mcp.tool(
        description=(
            "List every DDS endpoint (writer and reader) announced on the bus, "
            "already normalized: one `EndpointInfo` per endpoint with `role`, "
            "`topic`, `type_name`, `type_id`, the owning `participant_guid` "
            "joined with its `participant_name`, and a structured `qos` "
            "(reliability, durability, history, deadline, liveliness kind and "
            "lease, ownership kind and strength, partitions, latency budget, "
            "destination order, data representation). Use it instead of "
            "parsing `peek_dds_samples` output and joining GUID prefixes by "
            "hand. **Spotting orphans**: `by_topic` rolls the endpoints up per "
            "topic with `writer_count`, `reader_count` and `orphan` "
            '(`"no_reader"` = a writer nobody subscribes to, `"no_writer"` = '
            "a reader nobody publishes to), plus the union of partitions. "
            "**Reading `qos`**: a duration of `None` (`deadline_ns`, "
            "`liveliness_lease_ns`, `latency_budget_ns`) means infinite or "
            "not set ; a policy field of `None` means the endpoint did not "
            "announce it. `announced_ns` is the discovery announcement's "
            "source timestamp on the announcing side's clock, which can "
            "differ from this host's clock. **This lists discovery facts, not "
            "data flow**: it shows what endpoints exist and how they are "
            "configured, not whether samples are moving: `activity` is always "
            "`None` (see `activity_note`), because TopicForge holds no reader on "
            "user topics and cannot tell a silent or hung writer from a healthy "
            "one. Pair it with "
            "`detect_qos_mismatches` to see which pairs cannot match. "
            "TopicForge's own observer participant is excluded unless "
            "`include_observer` is true. Output is capped at 500 endpoints "
            "(`truncated`, `total_discovered`) ; `by_topic` still covers all "
            "matches. Read-only. **Raises an MCP error** when no DDS module "
            "is active. Mock mode returns a deterministic fixture matching "
            "the other mock DDS tools."
        )
    )
    @instrument(telemetry, "list_endpoints")
    def list_endpoints(
        topic: Annotated[
            str | None,
            Field(
                description=(
                    "Only endpoints on this DDS topic name (exact match). Omit to list every topic."
                )
            ),
        ] = None,
        participant_guid: Annotated[
            str | None,
            Field(
                description=(
                    "Only endpoints owned by this participant, as the guid "
                    "reported by `list_participants` or by an earlier "
                    "`list_endpoints`. Omit for all participants."
                )
            ),
        ] = None,
        include_observer: Annotated[
            bool,
            Field(
                description=(
                    "Include TopicForge's own observer participant's endpoints. Defaults to false."
                )
            ),
        ] = False,
        domain_id: Annotated[
            int,
            Field(
                description=(
                    "Accepted for uniformity with the other DDS tools (0..232). "
                    "The server observes only the domain it joined at startup "
                    "(`TOPICFORGE_DDS_DOMAIN_ID`) ; the response `domain_id` "
                    "says which one."
                ),
                ge=0,
                le=232,
            ),
        ] = 0,
    ) -> EndpointListing:
        return inspector.list_endpoints(topic, participant_guid, include_observer, domain_id)

    # TODO(roadmap): URDF tools: validate / inspect / generate URDF & xacro.
    # TODO(roadmap): bag anomaly detection: clock jumps, frame drops, TF gaps.
    # TODO(roadmap): dataset export: rosbag -> COCO / Hugging Face Datasets.
    # TODO(roadmap): synthetic data pipeline: Blender / Gazebo / Isaac control.
