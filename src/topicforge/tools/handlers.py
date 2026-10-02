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
    MismatchScan,
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
    "DDS topic name. Bare DDS names such as `scan` are valid, as are ROS 2 "
    "mangled names such as `rt/scan` and the builtin discovery topics "
    "`DCPSParticipant`, `DCPSSubscription`, `DCPSPublication`. Letters, "
    "digits, `_`, `/` and `::` are allowed; anything else is rejected."
)

_DOMAIN_PARAM_DESC = (
    "Accepted for compatibility (0..232). TopicForge observes the domain it "
    "joined at startup (TOPICFORGE_DDS_DOMAIN_ID); this argument does not "
    "switch domains, and the response `domain_id` says which one was observed."
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
    """Register the tool set on `mcp`.

    `telemetry` is required but inert by default: when disabled (the
    default), `instrument(...)` returns the handler unchanged, so opt-out
    means zero overhead and zero network code paths in the call stack.
    """

    @mcp.tool(
        description=(
            "Report TopicForge environment state. Returns a `HealthReport`: "
            "effective runtime `mode` (`live` or `mock`), `ros_backend` and "
            "`dds_backend`, `ros_tools_available`, `ros2_available`, "
            "`ros2_distro` (fed by the `ROS_DISTRO` env var), "
            "`dds_domain_id` and `observed_domain_note` (only that one DDS "
            "domain, joined at startup, is observed: programs on other domains "
            "are invisible), the server "
            "version and the server-side sample cap. "
            "**Reading `mode`**: `live` with `ros_backend` `none` means the DDS"
            " tools are live and the ROS 2 tools are not available (a DDS-only "
            "setup: use `list_endpoints` for topics and wiring). "
            "`payload_decoding` is `disabled`: DDS user-topic payloads are not "
            "decoded. `dds_security` is `not_supported`: on a secured domain "
            "participants show up but protected endpoints and data do not. For "
            "a live DDS backend it also reports `dds_domain_id`, "
            "`observer_started_ns` and `now_ns` (how long TopicForge has been "
            "watching: nothing before `observer_started_ns` was observed) and "
            "the discovery tracker status `tracker_running` / `tracker_passes` "
            "/ `tracker_errors` / `tracker_last_pass_ns` (errors above 0 or a "
            "stale last pass mean lifecycle data has gaps). **Always "
            "succeeds**: call it first when something looks wrong. Read-only; "
            "no side effects."
        )
    )
    @instrument(telemetry, "health_check")
    def health_check() -> HealthReport:
        return health.report()

    @mcp.tool(
        description=(
            "ROS 2 graph only; on a DDS-only setup use `list_endpoints`. List "
            "every ROS 2 topic on the current graph (or the deterministic mock "
            "graph in mock mode). Returns `list[TopicInfo]`: each entry carries"
            " `name`, `message_type`, `publisher_count`, `subscriber_count`, "
            "`qos_reliability`, and `mode_effective` (`live` or `mock`) so a "
            "caller can tell a real graph from demo fixtures. **Empty list** "
            "when the graph has no topics or when live discovery times out. "
            "**Raises an MCP error** when no `ros2` CLI is available (DDS-only "
            "setup). Read-only; no side effects."
        )
    )
    @instrument(telemetry, "list_topics")
    def list_topics() -> list[TopicInfo]:
        return inspector.list_topics()

    @mcp.tool(
        description=(
            "ROS 2 graph only; on a DDS-only setup use `list_endpoints`. Return"
            " detailed info for a single ROS 2 topic. `topic` must be a fully "
            "qualified topic name, e.g. `/cmd_vel`. Returns a `TopicInfo` "
            "carrying `mode_effective` (`live` or `mock`) so callers can tell a"
            " real-graph hit from a mock fixture. **Raises an MCP error** "
            "(isError=true) if the topic name is malformed, the topic is "
            "unknown to the active graph, or no `ros2` CLI is available. Read-"
            "only; no side effects."
        )
    )
    @instrument(telemetry, "get_topic_info")
    def get_topic_info(
        topic: Annotated[str, Field(description=_TOPIC_PARAM_DESC)],
    ) -> TopicInfo:
        return inspector.get_topic_info(topic)

    @mcp.tool(
        description=(
            "ROS 2 graph only; on a DDS-only setup use `list_endpoints` (topics"
            " and wiring) or `peek_dds_samples` on `DCPSPublication` / "
            "`DCPSSubscription` (raw discovery records). Peek up to `count` "
            "recent ROS 2 messages from `topic`. `topic` must be a fully "
            "qualified name (see the `topic` parameter description). `count` "
            "defaults to 5 and is silently clamped to 50. Returns a "
            "`SampleResult` `{topic, count, samples, mode_effective}` where "
            "`count` is the actual number of samples returned (may be 0) and "
            "`mode_effective` is `live` or `mock`. **Live mode** shells out to "
            "`ros2 topic echo --csv --once` with a short timeout, so the result"
            " is empty when no publisher is currently active. "
            "`samples[i].timestamp_ns` is the message's `header.stamp` (publish"
            " time) when the message is `Header`-stamped, and 0 for headerless "
            "types (e.g. `std_msgs/String`). The live parser exposes fields as "
            "positional CSV columns under `samples[i].payload` keys `col_0`, "
            "`col_1`, ..., with the verbatim CSV row under the reserved "
            "`_raw_text` key. **Mock mode** returns deterministic, structured "
            "samples for the fictional demo robot. **Raises an MCP error** when"
            " no `ros2` CLI is available. Read-only; never publishes to the "
            "bus. Distinct from `peek_dds_samples`, which reads the raw DDS "
            "layer."
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
            "Summarize a ROS 2 bag at `path`. Returns a `BagAnalysis` carrying "
            "storage format, total duration, message count, per-topic stats, "
            "detected anomalies, and `mode_effective` (`live` or `mock`) so "
            "callers can tell a real bag analysis from a mock fixture. **Live "
            "mode** shells out to `ros2 bag info` and accepts `.mcap`, `.db3`, "
            "and `.bag` files plus `rosbag2_*` directories ; **mock mode** "
            "returns rich fixture data regardless of path suffix (except "
            "blatantly non-bag extensions). **Raises an MCP error** if the path"
            " is malformed, missing in live mode, or unparseable, or if no "
            "`ros2` CLI is available. Anomaly detection is available in mock "
            "mode only. Read-only; no side effects."
        )
    )
    @instrument(telemetry, "analyze_bag")
    def analyze_bag(
        path: Annotated[str, Field(description=_PATH_PARAM_DESC, min_length=1)],
    ) -> BagAnalysis:
        return inspector.analyze_bag(path)

    # ----- DDS tools -----
    # The tools below address the bare-DDS layer, distinct from the
    # ROS2-graph tools above. They are active when TOPICFORGE_DDS_BACKEND
    # is `cyclone`, `fast`, or `mock`. With the `ros2_cli` adapter (default
    # for ROS2-only installs), they raise AdapterError pointing at the
    # `pip install topicforge[dds]` remediation path.

    @mcp.tool(
        description=(
            "List DDS participants observed on the bus. Returns "
            "`list[ParticipantInfo]`: each entry carries `guid`, `vendor` "
            "(`cyclone`/`fast`/`rti`/`rti_micro`/`opensplice`/`opendds`/`coredx`/`intercom`/`dust`/`mock`/`unknown`)"
            " with `vendor_source`, optional `name` (announced EntityName QoS, "
            "e.g. `lidar_driver`), optional `hostname`, `domain_id`, "
            "`is_observer` and `mode_effective` (`live`/`mock`). **Why `vendor`"
            " can be `unknown`**: TopicForge reads the vendor from the vendor "
            "prefix of the participant GUID (`vendor_source` `guid_prefix`; "
            "`none` when unknown). Some vendors, e.g. Dust DDS and RTI Connext,"
            " do not put their vendor id there, and the Cyclone Python binding "
            "does not expose the RTPS header vendor id, so those participants "
            "are still listed but as `unknown`. **`is_observer`** is true for "
            "TopicForge's own read-only participant, which is listed like any "
            "other. Lifecycle fields: `status` (`active`/`left`), "
            "`first_seen_ns` / `last_seen_ns` (TopicForge's local clock), "
            "`seen_count`, `announced_ns` (DDS source timestamp of the "
            "announcement), and once left `lost_ns` + `lost_time_source`. "
            "`lost_ns` is an upper bound of when the participant died: exact "
            "after a clean shutdown, the lease expiry after a crash (the two "
            "cannot be told apart), so a crashed process died up to one lease "
            "before it (10 s Cyclone default, 20 s Fast DDS, 100 s RTI; the "
            "dead participant's lease, not ours). Cyclone tracks discovery "
            "continuously in the background, so these stay correct between "
            "calls; right after server start the call waits up to 3 s for "
            "discovery to warm up. **Only the domain joined at startup is "
            "observed** (see `health_check` `dds_domain_id`): a participant "
            "running on another DDS domain is INVISIBLE here, so a missing "
            "participant may simply be on a different domain; `domain_id` does "
            "not switch domains (restart with `TOPICFORGE_DDS_DOMAIN_ID`). Operates at the raw "
            "DDS layer beneath ROS, so it also sees non-ROS participants. "
            "**Read-only by architecture**: it cannot publish, modify QoS, or "
            "alter the bus. **Raises an MCP error** when no DDS module is "
            "active (install `pip install topicforge[dds]` and set "
            "`TOPICFORGE_DDS_BACKEND=cyclone`). The mock backend returns "
            "deterministic fixtures."
        )
    )
    @instrument(telemetry, "list_participants")
    def list_participants(
        domain_id: Annotated[
            int,
            Field(
                description=_DOMAIN_PARAM_DESC,
                ge=0,
                le=232,
            ),
        ] = 0,
    ) -> list[ParticipantInfo]:
        return inspector.list_participants(domain_id)

    @mcp.tool(
        description=(
            "Explain why DDS readers and writers on the same topic do not "
            "talk, and who will. Pairs every reader with every writer per "
            "topic and returns a `MismatchScan`: `matched` (the pairs DDS "
            "will connect given the announced QoS; actual data flow is not "
            "observed), `reports` (QoS incompatible or "
            "risky pairs, each with the participant names, requested vs "
            "offered values and the failed rule in `details`), `not_matched` "
            "(pairs DDS never matches: different partitions or type names; "
            "the QoS rules are NOT evaluated for them, so a partition split "
            "is not blamed on Reliability; `latent_incompatible_policies` lists "
            "the RxO policies that would ALSO be incompatible once the "
            "partition/type issue is fixed), `hints` (orphan topics with a "
            "near-identical name, i.e. probable typos, and type id notes), "
            "plus `pairs_checked`, `topics_scanned`, `policies_checked` and "
            "`policies_unchecked`. Checked: Partition (with `*` and `?` "
            "wildcards), type name, Reliability, Durability, Deadline, "
            "Liveliness, LatencyBudget, Ownership, DestinationOrder, "
            "DataRepresentation, History (risky only). Not checked: see "
            "`policies_unchecked`. **An empty `reports` with a non-empty "
            "`not_matched` still means no data flows**, and an all-empty "
            "result does not prove the bus healthy: discovery shows the "
            "QoS endpoints DECLARED, not runtime behavior: a reader logging "
            "'deadline missed' while the QoS is compatible means the "
            "writer's real period exceeds the deadline at runtime, which "
            "TopicForge cannot observe. Pass `topic` to "
            "scope to one topic ; omit for an exhaustive scan. "
            "**Read-only by architecture**. **Raises an MCP error** when no "
            "DDS module is active ; the mock backend returns deterministic "
            "fixtures."
        )
    )
    @instrument(telemetry, "detect_qos_mismatches")
    def detect_qos_mismatches(
        topic: Annotated[
            str | None,
            Field(
                description=(
                    "Optional DDS topic name to scope the scan to: a bare name "
                    "such as `scan` or a ROS 2 mangled name such as `rt/scan`. "
                    "Omit to scan all topics."
                ),
                default=None,
            ),
        ] = None,
    ) -> MismatchScan:
        return inspector.detect_qos_mismatches(topic)

    @mcp.tool(
        description=(
            "Peek recent samples on a raw DDS topic. Distinct from "
            "`sample_messages`, which reads the ROS 2 graph through the `ros2` "
            "CLI ; this tool reads the DDS layer directly and works without ROS"
            " 2. Returns a `SampleResult` `{topic, count, samples, "
            "mode_effective, note}`, the same shape as `sample_messages`. "
            "`count` defaults to 5 and is silently clamped to 50. **Topic "
            "categories**: (a) The 3 builtin discovery topics "
            "(`DCPSParticipant`, `DCPSSubscription`, `DCPSPublication`) return "
            "structured discovery payloads: on Cyclone the CURRENT discovery "
            "state (one record per live participant or endpoint), not a stream "
            "of recent events ; use `participant_events` for history. "
            "`DCPSPublication` and `DCPSSubscription` are the raw writers and "
            "readers behind `list_endpoints`. (b) User-defined topics: payload "
            "decoding is DISABLED on every backend. The call returns `count` 0,"
            " `samples` empty and a `note` saying so ; that does NOT mean the "
            "topic is silent. Use `list_endpoints` for the topic's presence, "
            "writers, readers and QoS. A user topic that is not announced on "
            "the bus raises an error. Right after server start the call waits "
            "up to 3 s for discovery to warm up. **Read-only by architecture**:"
            " it cannot publish. **Raises an MCP error** when no DDS module is "
            "active or the topic is not announced on the bus."
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
            "captured over a recent window. Use it to answer 'who was on the "
            "bus 5 minutes ago and left?' or 'when did this participant first "
            "appear?'. Returns `list[ParticipantEvent]`: each entry carries "
            "`guid`, `event_type`, `vendor`, `timestamp_ns` (wall-clock ns "
            "since epoch), `time_source`, `observed_ns`, optional `name` (the "
            "participant's announced DDS name), optional `hostname`, "
            "`domain_id`, and `mode_effective` (`live`/`mock`). `time_source` "
            "says what `timestamp_ns` is: `dds_source_timestamp` (the DDS "
            "timestamp of the announcement or dispose) or `observed_local` "
            "(when TopicForge noticed, weakest). `observed_ns` is when "
            "TopicForge noticed, always at or after a DDS-derived "
            "`timestamp_ns`. **Crash caveat**: a `lost` timestamp is an upper "
            "bound of the death. After a clean shutdown it is exact; after a "
            "crash it is when the lease expired, so the process died between "
            "`timestamp_ns` minus the dead participant's lease and "
            "`timestamp_ns` (10 s Cyclone default, 20 s Fast DDS, 100 s RTI), "
            "and the two cases cannot be told apart. A restarted node is a new "
            "participant: expect one `lost` and one `discovered` per restart, "
            "with different `guid`s and the same `name`. Sorted newest-first. "
            "Hard cap at 200 events (silent truncation ; reduce "
            "`lookback_seconds` if you hit it). TopicForge only knows what "
            "happened since it started watching (see "
            "`health_check.observer_started_ns`). **Backend caveats**: Fast DDS"
            " captures arrivals and removals via listener callbacks ; Cyclone "
            "tracks discovery continuously in the background (a pass every 0.5 "
            "s, independent of tool calls), so restarts and crashes are "
            "recorded as they happen, but a participant cycle faster than the "
            "discovery reader's history depth between two passes can still be "
            "missed ; mock returns a deterministic fixture timeline. Right "
            "after server start the call waits up to 3 s for discovery to warm "
            "up. **Read-only by architecture**. **Raises an MCP error** when no"
            " DDS module is active (install `pip install topicforge[dds]` and "
            "set `TOPICFORGE_DDS_BACKEND=cyclone|fast`)."
        )
    )
    @instrument(telemetry, "participant_events")
    def participant_events(
        domain_id: Annotated[
            int,
            Field(
                description=_DOMAIN_PARAM_DESC,
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
            "percentiles) for a DDS topic over a recent time window. Returns a "
            "`TopicMetrics` payload carrying `status`, `samples_observed`, "
            "`frequency_hz_observed`, `frequency_hz_declared`, "
            "`sequence_gaps_count`, `latency_ns_p50/p95/p99`, and boolean "
            "availability flags. **Read `status` first**: "
            "`unsupported_user_topic` means the topic is a user topic, whose "
            "payload is not decoded, so there are no metrics: every number is "
            "null or 0 and none of it is a measurement. `no_samples_yet` means "
            "a builtin topic with nothing buffered in the window. `ok` means "
            "metrics were computed. **Limits**: the buffer is filled only when "
            "`peek_dds_samples` runs on the topic, so `frequency_hz_observed` "
            "reflects how often it was called, not the real publish rate ; "
            "treat it as a coarse presence signal. `frequency_hz_declared` is "
            "declared, not measured: `1 / deadline` of the shortest QoS "
            "Deadline a writer on the topic announced in discovery, null when "
            "none announced one. **Read-only by architecture**. **Raises an MCP"
            " error** when no DDS module is active or `window_seconds` is out "
            "of range (1..3600). Right after server start the call waits up to "
            "3 s for discovery to warm up."
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
                description=_DOMAIN_PARAM_DESC,
                ge=0,
                le=232,
            ),
        ] = 0,
    ) -> TopicMetrics:
        return inspector.topic_metrics(topic, window_seconds, domain_id)

    @mcp.tool(
        description=(
            "Peek up to `count` recent samples from a recorded bag file. "
            "Distinct from `peek_dds_samples` (live DDS layer) and "
            "`sample_messages` (live ROS 2 graph): this tool reads **offline "
            "bag content** for post-mortem analysis. Supported formats: MCAP "
            "(`.mcap`), ROS 2 rosbag2 SQLite (`.db3`), ROS 1 legacy chunked "
            "binary (`.bag`), detected from the file extension. Returns a "
            "`SampleResult` in the same shape as `peek_dds_samples`: each "
            "sample's `payload` carries a `_decode_status` annotation (`full` /"
            " `partial` / `raw`). `count` defaults to 5 and is silently clamped"
            " to 50. **Requires the `rosbags` library** (`pip install "
            "topicforge[bags]`) and the ROS 2 side of the runtime: on a DDS-"
            "only setup it raises an error. The mock backend returns "
            "deterministic fixture samples on canned bag paths. **Read-only by "
            "architecture**: nothing writes to the bag file. **Raises an MCP "
            "error** when the bag path does not exist, the topic is not present"
            " in the bag, or `rosbags` is not installed."
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
            "announce it. **Ownership**: among EXCLUSIVE writers the live one with "
            "the highest `ownership_strength` delivers to a reader; which writer "
            "currently owns an instance is reader-side runtime state TopicForge "
            "cannot observe. **Departed endpoints**: when a participant leaves, "
            "its endpoints are remembered (last 200, 1 h) and shown in `by_topic` "
            "as `departed_writers` / `departed_readers` (participant name and "
            "`gone_ns`), so a topic that lost its only writer is explained in "
            "one call; they are listed in `endpoints` only with "
            "`include_departed`. **Topic filter**: `rt/scan` and `scan` match "
            "each other (exact name first; `note` says which form matched), and "
            "a filter that matches nothing returns a `note` with the closest "
            "known topics. `announced_ns` is the discovery announcement's "
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
                    "Only endpoints on this DDS topic name: a bare name such as `scan` or a ROS 2 mangled name such as `rt/scan` (exact name first, then the alternate form). Omit to list every topic."
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
                description=_DOMAIN_PARAM_DESC,
                ge=0,
                le=232,
            ),
        ] = 0,
        include_departed: Annotated[
            bool,
            Field(
                description=(
                    "Also list endpoints whose participant left the bus (flagged "
                    "with `gone_ns`). Defaults to false; `by_topic` reports them "
                    "as `departed_writers` / `departed_readers` either way."
                )
            ),
        ] = False,
    ) -> EndpointListing:
        return inspector.list_endpoints(
            topic, participant_guid, include_observer, domain_id, include_departed
        )

    # TODO(roadmap): URDF tools: validate / inspect / generate URDF & xacro.
    # TODO(roadmap): bag anomaly detection: clock jumps, frame drops, TF gaps.
    # TODO(roadmap): dataset export: rosbag -> COCO / Hugging Face Datasets.
    # TODO(roadmap): synthetic data pipeline: Blender / Gazebo / Isaac control.
