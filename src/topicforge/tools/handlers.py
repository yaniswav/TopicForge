"""MCP tool handlers.

Handlers delegate to services and return Pydantic models for the SDK to
serialize. Exceptions propagate and become `isError: true` results (`guarded`
re-raises `AdapterError` as the SDK's `ToolError` so its text reaches the
client). A custom error envelope would report a failure as a successful call.

The `description` strings are read by LLM clients, so they state caveats,
limits and mock/live differences.
"""

from __future__ import annotations

from typing import Annotated

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from topicforge.constants import (
    DEFAULT_SAMPLE_TIMEOUT_S,
    MAX_SAMPLE_COUNT,
    MAX_SAMPLE_TIMEOUT_S,
    MIN_SAMPLE_TIMEOUT_S,
)
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
from topicforge.tools.annotations import read_only_annotations
from topicforge.tools.guard import guarded

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

_SAMPLE_COUNT_PARAM_DESC = (
    "Number of messages to wait for. Defaults to 5; at most 50 (a larger "
    "request is capped to 50 and `note` says so). 0 returns nothing. "
    "`SampleResult.count` is how many arrived: fewer than requested when the "
    "topic publishes slowly or the deadline (`timeout_s`) ran out."
)

_SAMPLE_TIMEOUT_PARAM_DESC = (
    "Seconds to wait for the messages, 1..40, default 10. Bounds the whole "
    "call, topic lookup and the `ros2` CLI start-up (a few seconds on a slow "
    "machine) included: the call returns within `timeout_s` plus a few "
    "seconds (stopping the CLI, decoding). Whatever arrived by then is "
    "returned with a `note`. Raise it for a topic that publishes slower than "
    "1 Hz. Calls that use the `ros2` CLI run one at a time: a long wait "
    "delays other `ros2`-backed calls, which fail with a `busy` error if "
    "they cannot start in time."
)

_MAX_ARRAY_LENGTH_PARAM_DESC = (
    "Longest array, string or bytes value to return in full, 1..65536; "
    "longer ones are cut after their first N elements or characters and the "
    "field's dotted path is listed in the sample's `_truncated_fields`. "
    "Defaults to 128, the `ros2 topic echo` default, which cuts a 541-beam "
    "`LaserScan` after 128 ranges. Pass null to return everything in full "
    "(large for images and point clouds; a message over the server's size "
    "cap, 1 MiB by default, is dropped with a note, and a very large message "
    "may not print before the deadline)."
)

_ARRAYS_SUMMARY_PARAM_DESC = (
    "When true, array fields are replaced by a short type and length summary "
    "instead of their elements. Use it to inspect the non-array fields of "
    "large messages (images, scans, point clouds). Defaults to false."
)

_PATH_PARAM_DESC = (
    "Path to a bag: a file ending in `.mcap` or `.db3`, or a `rosbag2_*` "
    "directory. `peek_bag_samples` also reads ROS 1 `.bag` files; "
    "`analyze_bag` does not (`ros2 bag info` cannot open them). "
    "Leading/trailing whitespace is stripped. Null "
    "bytes and otherwise malformed filesystem paths are rejected. Existence "
    "and bag format are validated by the live adapter (mock mode accepts "
    "any well-formed path)."
)


def register_tools(
    mcp: MCPServer,
    inspector: Inspector,
    health: HealthService,
    telemetry: TelemetryClient,
) -> None:
    """Register the tool set on `mcp`.

    With telemetry disabled (the default), `instrument(...)` returns the
    handler unchanged.
    """

    @mcp.tool(
        annotations=read_only_annotations("Health check", open_world=False),
        description=(
            "Report TopicForge environment state as a `HealthReport`: "
            "effective runtime `mode` (`live` or `mock`), `ros_backend` and "
            "`dds_backend`, `ros_tools_available`, `ros2_available`, "
            "`ros2_distro` (fed by the `ROS_DISTRO` env var), "
            "`dds_domain_id` and `observed_domain_note` (only the DDS domain "
            "joined at startup is observed; programs on other domains are "
            "invisible), the server version and the server-side sample cap. "
            "**Reading `mode`**: `live` with `ros_backend` `none` means the DDS"
            " tools are live and the ROS 2 tools are not available (a DDS-only "
            "setup: use `list_endpoints` for topics and wiring). With "
            "`dds_backend` `none`, `dds_inactive_reason` says why: backend not "
            "selected, binding not installed, or adapter failed to start. "
            "`payload_decoding` is `disabled`: DDS user-topic payloads are not "
            "decoded. `dds_security` is `not_supported`: on a secured domain "
            "participants show up but protected endpoints and data do not. For "
            "a live DDS backend it also reports `dds_domain_id`, "
            "`observer_started_ns` and `now_ns` (how long TopicForge has been "
            "watching: nothing before `observer_started_ns` was observed) and "
            "the discovery tracker status `tracker_running` / `tracker_passes` "
            "/ `tracker_errors` / `tracker_last_pass_ns` / `tracker_cache_evictions` "
            "(errors or evictions above 0, or a stale last pass, mean the "
            "discovery data has gaps). **Always succeeds**: call it first when "
            "something looks wrong. Read-only; no side effects."
        ),
    )
    @guarded(None)
    @instrument(telemetry, "health_check")
    def health_check() -> HealthReport:
        return health.report()

    @mcp.tool(
        annotations=read_only_annotations("List ROS 2 topics", open_world=True),
        description=(
            "ROS 2 graph only; on a DDS-only setup use `list_endpoints`. List "
            "every ROS 2 topic on the current graph (or the mock graph in mock "
            "mode). Returns `list[TopicInfo]`: each entry carries `name`, "
            "`message_type`, `publisher_count`, `subscriber_count`, and "
            "`mode_effective` (`live` or `mock`) to tell a real graph from "
            "fixtures. Live mode leaves `qos_reliability` and `qos_durability` "
            "null here: call `get_topic_info` for a topic's QoS. **Empty list** "
            "when the graph has no topics or when live discovery times out. "
            "**Raises an MCP error** when no `ros2` CLI is available (DDS-only "
            "setup). Read-only; no side effects."
        ),
    )
    @guarded("ros")
    @instrument(telemetry, "list_topics")
    def list_topics() -> list[TopicInfo]:
        return inspector.list_topics()

    @mcp.tool(
        annotations=read_only_annotations("Get topic info", open_world=True),
        description=(
            "ROS 2 graph only; on a DDS-only setup use `list_endpoints`. Return"
            " info for a single ROS 2 topic. `topic` must be a fully qualified "
            "name, e.g. `/cmd_vel`. Returns a `TopicInfo` with `mode_effective` "
            "(`live` or `mock`) and, in live mode, the publishers' "
            "`qos_reliability` (`reliable` / `best_effort` / `mixed`) and "
            "`qos_durability` (`volatile` / `transient_local` / `mixed`; "
            "`transient_local` marks a latched topic such as `/tf_static`). "
            "**Raises an MCP error** if the topic name is "
            "malformed, the topic is unknown to the active graph, or no `ros2` "
            "CLI is available. Read-only; no side effects."
        ),
    )
    @guarded("ros")
    @instrument(telemetry, "get_topic_info")
    def get_topic_info(
        topic: Annotated[str, Field(description=_TOPIC_PARAM_DESC)],
    ) -> TopicInfo:
        return inspector.get_topic_info(topic)

    @mcp.tool(
        annotations=read_only_annotations("Sample topic messages", open_world=True),
        description=(
            "ROS 2 graph only; on a DDS-only setup use `list_endpoints` (topics"
            " and wiring) or `peek_dds_samples` on `DCPSPublication` / "
            "`DCPSSubscription` (raw discovery records). Collect up to `count` "
            "live messages from a ROS 2 `topic`, waiting at most `timeout_s` "
            "seconds. Returns a `SampleResult` `{topic, count, samples, "
            "mode_effective, note}`. **Live mode** streams `ros2 topic echo`, "
            "matching the publishers' QoS so latched (transient_local) topics "
            "work, and returns whatever arrived by the deadline: `count` is the "
            "actual number and `note` says `N of M messages` and why it is short "
            "(no publisher, or a publisher that is silent, slow or too large to "
            "print in time; a latched topic usually holds only its last "
            "message, so use `count` 1). It never hides a timeout behind an empty list "
            "without a `note`. Each sample has the message fields as nested "
            "named values in `payload` (e.g. `payload.header.stamp.sec`), "
            "`timestamp_ns` from the message's `header.stamp` (the publisher's "
            "clock: sim time on a simulation, 0 for a headerless message such "
            "as `std_msgs/String`) with `stamp_source` `header` or `none`; `/clock`, "
            "`/tf` and `/rosout` carry it in the body and give `payload`; and "
            "`received_ns`, the wall clock when the CLI printed it. **Arrays**: "
            "by default arrays, strings and bytes are cut at 128 elements (a "
            "541-beam `LaserScan` loses beams 128 and up); the cut fields are "
            "listed in `payload._truncated_fields` and in `note`. Raise "
            "`max_array_length` (up to 65536, or null for no cut) to read more, "
            "or set `arrays_summary_only` to see only the non-array fields. "
            "`nan` and `inf` floats come back as strings. A message over the "
            "1 MiB size cap (`TOPICFORGE_MAX_SAMPLE_BYTES`) is dropped and "
            "`note` says so; it is dropped while it streams, so it costs no "
            "decoding time. **Mock mode** returns structured samples for the "
            "fictional demo robot, instantly. **Raises an MCP error** when no "
            "`ros2` CLI is available, the topic is unknown, or the CLI fails. "
            "Read-only; never publishes. Distinct from `peek_dds_samples`, "
            "which reads the raw DDS layer."
        ),
    )
    @guarded("ros")
    @instrument(telemetry, "sample_messages")
    def sample_messages(
        topic: Annotated[str, Field(description=_TOPIC_PARAM_DESC)],
        count: Annotated[
            int,
            Field(
                description=_SAMPLE_COUNT_PARAM_DESC,
                ge=0,
                json_schema_extra={"maximum": MAX_SAMPLE_COUNT},
            ),
        ] = 5,
        max_array_length: Annotated[
            int | None, Field(description=_MAX_ARRAY_LENGTH_PARAM_DESC, ge=1, le=65536)
        ] = 128,
        arrays_summary_only: Annotated[bool, Field(description=_ARRAYS_SUMMARY_PARAM_DESC)] = False,
        timeout_s: Annotated[
            float,
            Field(
                description=_SAMPLE_TIMEOUT_PARAM_DESC,
                ge=MIN_SAMPLE_TIMEOUT_S,
                le=MAX_SAMPLE_TIMEOUT_S,
            ),
        ] = DEFAULT_SAMPLE_TIMEOUT_S,
    ) -> SampleResult:
        return inspector.sample_messages(
            topic,
            count,
            max_array_length=max_array_length,
            arrays_summary_only=arrays_summary_only,
            timeout_s=timeout_s,
        )

    @mcp.tool(
        annotations=read_only_annotations("Analyze bag", open_world=False),
        description=(
            "Summarize a ROS 2 bag at `path`. Returns a `BagAnalysis` with "
            "storage format, duration, message count, per-topic stats, "
            "detected anomalies and `mode_effective` (`live` or `mock`). Per "
            "topic, `frequency_hz` is `(n - 1) / (last - first message time)` "
            "of that topic (`frequency_basis` `topic_span`), with "
            "`first_timestamp_ns`, `last_timestamp_ns` and `latched`; a "
            "`latched` topic whose messages all fall within 1 second (e.g. "
            "`/tf_static`, a start-up burst) has a null `frequency_hz`, while "
            "a latched topic published over a longer span keeps its rate. "
            "When the bag cannot be read locally, or is a large `.mcap` "
            "(over 200 MiB), the rate falls back to count / bag duration "
            "(`bag_duration`) and `note` says why. **Live "
            "mode** runs `ros2 bag info` and accepts `.mcap` and `.db3` "
            "files plus `rosbag2_*` directories (ROS 1 `.bag` files are not "
            "readable by `ros2 bag info`; use `peek_bag_samples` for those); **mock mode** returns fixture "
            "data for any path suffix except clearly non-bag ones. **Raises an "
            "MCP error** if the path"
            " is malformed, missing in live mode, or unparseable, or if no "
            "`ros2` CLI is available. Anomaly detection is available in mock "
            "mode only. Read-only; no side effects."
        ),
    )
    @guarded("ros")
    @instrument(telemetry, "analyze_bag")
    def analyze_bag(
        path: Annotated[str, Field(description=_PATH_PARAM_DESC, min_length=1)],
    ) -> BagAnalysis:
        return inspector.analyze_bag(path)

    # DDS tools: they need TOPICFORGE_DDS_BACKEND set to `cyclone`, `fast` or
    # `mock`; with the `ros2_cli` adapter alone they raise AdapterError.

    @mcp.tool(
        annotations=read_only_annotations("List DDS participants", open_world=True),
        description=(
            "List DDS participants observed on the bus. Returns "
            "`list[ParticipantInfo]`: each entry carries `guid`, `vendor` "
            "(`cyclone`/`fast`/`rti`/`rti_micro`/`opensplice`/`opendds`/`coredx`/`intercom`/`dust`/`mock`/`unknown`)"
            " with `vendor_source`, optional `name` (announced EntityName QoS, "
            "e.g. `lidar_driver`), optional `hostname`, `domain_id`, "
            "`is_observer` and `mode_effective` (`live`/`mock`). **Why `vendor`"
            " can be `unknown`**: the vendor is read from the participant GUID "
            "prefix (`vendor_source` `guid_prefix`; `none` when unknown). Some "
            "vendors, e.g. Dust DDS and RTI Connext, do not put their vendor id "
            "there, and the Cyclone Python binding does not expose the RTPS "
            "header vendor id, so those participants are listed as `unknown`. "
            "**`is_observer`** is true for TopicForge's own read-only "
            "participant, which is listed like any other. Lifecycle fields: `status` (`active`/`left`), "
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
            "observed** (see `health_check` `dds_domain_id`): a participant on "
            "another DDS domain is INVISIBLE here, so a missing participant may "
            "be on a different domain; `domain_id` does not switch domains "
            "(restart with `TOPICFORGE_DDS_DOMAIN_ID`). Works at the raw DDS "
            "layer beneath ROS, so it also sees non-ROS participants. "
            "**Read-only by architecture**: it cannot publish, modify QoS, or "
            "alter the bus. **Raises an MCP error** when no DDS module is "
            "active (install `pip install topicforge[dds]` and set "
            "`TOPICFORGE_DDS_BACKEND=cyclone`). The mock backend returns "
            "fixtures."
        ),
    )
    @guarded("dds")
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
        annotations=read_only_annotations("Detect QoS mismatches", open_world=True),
        description=(
            "Explain why DDS readers and writers on the same topic do not "
            "talk, and who will. Pairs every reader with every writer per "
            "topic and returns a `MismatchScan`: `matched` (pairs DDS will "
            "connect given the announced QoS; data flow is not observed), "
            "`reports` (incompatible or risky QoS pairs, each with participant "
            "names, requested vs offered values and the failed rule in "
            "`details`), `not_matched` "
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
            "DataRepresentation, History (risky only, and only where announced: "
            "discovery does not carry it). Not checked: see "
            "`policies_unchecked`. **An empty `reports` with a non-empty "
            "`not_matched` still means no data flows**, and an all-empty "
            "result does not prove the bus healthy: discovery shows the QoS "
            "DECLARED, not runtime behavior (a reader logging 'deadline "
            "missed' with compatible QoS means the writer's real period "
            "exceeds the deadline, which TopicForge cannot observe). Pass "
            "`topic` to scope to one topic; omit to scan all. `reports`, "
            "`matched` and `not_matched` are capped at 200 entries each "
            "(`truncated` is true, the `*_total` fields keep the real counts, "
            "incompatible reports come first). A matched pair flagged "
            "`late_joiner` is a VOLATILE writer whose reader joined later on "
            "the same host: normal, not a fault. "
            "**Read-only by architecture**. **Raises an MCP error** when no "
            "DDS module is active; the mock backend returns fixtures."
        ),
    )
    @guarded("dds")
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
        annotations=read_only_annotations("Peek DDS samples", open_world=True),
        description=(
            "Peek recent samples on a raw DDS topic. Unlike `sample_messages` "
            "(which uses the `ros2` CLI), this reads the DDS layer directly and "
            "works without ROS 2. Returns a `SampleResult` `{topic, count, samples, "
            "mode_effective, note}`, the same shape as `sample_messages`. "
            "`count` defaults to 5 and is silently clamped to 50. **Topic "
            "categories**: (a) The 3 builtin discovery topics "
            "(`DCPSParticipant`, `DCPSSubscription`, `DCPSPublication`) return "
            "structured discovery payloads: on Cyclone the CURRENT discovery "
            "state (one record per live participant or endpoint), not a stream "
            "of recent events (use `participant_events` for history). "
            "`DCPSPublication` and `DCPSSubscription` are the raw writers and "
            "readers behind `list_endpoints`. The topic may be given as `/scan`, "
            "`scan` or `rt/scan`: all three resolve to the same topic. (b) "
            "User-defined topics: payload "
            "decoding is DISABLED on every backend. The call returns `count` 0,"
            " `samples` empty and a `note` saying so; that does NOT mean the "
            "topic is silent. Use `list_endpoints` for the topic's presence, "
            "writers, readers and QoS. A user topic that is not announced on "
            "the bus raises an error. Right after server start the call waits "
            "up to 3 s for discovery to warm up. **Read-only by architecture**:"
            " it cannot publish. **Raises an MCP error** when no DDS module is "
            "active or the topic is not announced on the bus."
        ),
    )
    @guarded("dds")
    @instrument(telemetry, "peek_dds_samples")
    def peek_dds_samples(
        topic: Annotated[str, Field(description=_DDS_TOPIC_PARAM_DESC)],
        count: Annotated[int, Field(description=_COUNT_PARAM_DESC, ge=0)] = 5,
    ) -> SampleResult:
        return inspector.peek_dds_samples(topic, count)

    @mcp.tool(
        annotations=read_only_annotations("DDS participant events", open_world=True),
        description=(
            "Return DDS participant lifecycle events (`discovered` / `lost`) "
            "from a recent window, e.g. 'who was on the bus 5 minutes ago and "
            "left?' or 'when did this participant first appear?'. Returns `list[ParticipantEvent]`: each entry carries "
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
            "Capped at 200 events, silently (reduce `lookback_s` if you "
            "hit it). TopicForge only knows what happened since it started "
            "watching (see `health_check.observer_started_ns`). **Backend "
            "caveats**: Fast DDS captures arrivals and removals through "
            "listener callbacks; Cyclone tracks discovery in the background (a "
            "pass every 0.5 s, independent of tool calls), so restarts and "
            "crashes are recorded as they happen, but a participant cycle "
            "faster than the discovery reader's history depth between two "
            "passes can be missed; mock returns a fixture timeline. Right "
            "after server start the call waits up to 3 s for discovery to warm "
            "up. **Read-only by architecture**. **Raises an MCP error** when no"
            " DDS module is active (install `pip install topicforge[dds]` and "
            "set `TOPICFORGE_DDS_BACKEND=cyclone|fast`)."
        ),
    )
    @guarded("dds")
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
        lookback_s: Annotated[
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
        return inspector.participant_events(domain_id, lookback_s)

    @mcp.tool(
        annotations=read_only_annotations("Topic metrics", open_world=True),
        description=(
            "Return temporal metrics (frequency, sequence gaps, latency "
            "percentiles) for a DDS topic over a recent time window. Returns a "
            "`TopicMetrics` payload carrying `status`, `samples_observed`, "
            "`observed_frequency_hz`, `declared_frequency_hz`, "
            "`sequence_gaps_count`, `latency_ns_p50/p95/p99`, and boolean "
            "availability flags. **Read `status` first**: "
            "`unsupported_user_topic` means the topic is a user topic, whose "
            "payload is not decoded, so there are no metrics: every number is "
            "null or 0 and none of it is a measurement. `no_samples_yet` means "
            "a builtin topic with nothing buffered in the window. `ok` means "
            "metrics were computed. **Limits**: the buffer is filled only when "
            "`peek_dds_samples` runs on the topic, so `observed_frequency_hz` "
            "reflects how often it was called, not the real publish rate: "
            "treat it as a coarse presence signal. `declared_frequency_hz` is "
            "declared, not measured: `1 / deadline` of the shortest QoS "
            "Deadline a writer on the topic announced in discovery, null when "
            "none announced one. **Read-only by architecture**. **Raises an MCP"
            " error** when no DDS module is active or `window_s` is out "
            "of range (1..3600). Right after server start the call waits up to "
            "3 s for discovery to warm up."
        ),
    )
    @guarded("dds")
    @instrument(telemetry, "topic_metrics")
    def topic_metrics(
        topic: Annotated[str, Field(description=_DDS_TOPIC_PARAM_DESC)],
        window_s: Annotated[
            int,
            Field(
                description=(
                    "Window in seconds over which to compute metrics "
                    "(1..3600). Defaults to 60 seconds. Smaller windows "
                    "reflect more recent state; larger windows smooth "
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
        return inspector.topic_metrics(topic, window_s, domain_id)

    @mcp.tool(
        annotations=read_only_annotations("Peek bag samples", open_world=False),
        description=(
            "Peek up to `count` samples from a recorded bag file. Unlike "
            "`peek_dds_samples` (live DDS) and `sample_messages` (live ROS 2 "
            "graph), this reads **offline bag content** for post-mortem "
            "analysis. Supported formats: MCAP "
            "(`.mcap`), ROS 2 rosbag2 SQLite (`.db3`), ROS 1 legacy chunked "
            "binary (`.bag`), detected from the file extension. Returns the "
            "**first** `count` messages of the topic in recording order, not "
            "the last ones. **Clocks**: `timestamp_ns` is the message's own "
            "`header.stamp` (`stamp_source` `header`) when it has a top-level "
            "header, else the time in the body of `Clock`, `TFMessage` or `Log` "
            "(`stamp_source` `payload`), else the bag record time "
            "(`stamp_source` `recorded`); "
            "`recorded_ns` is always the bag record time. Returns a "
            "`SampleResult` in the same shape as `peek_dds_samples`: each "
            "sample's `payload` carries a `_decode_status` annotation (`full` /"
            " `partial` / `raw`). `count` defaults to 5 and is silently clamped"
            " to 50. **Uses the `rosbags` library** (installed with "
            "topicforge) and needs the ROS 2 side of the runtime: on a DDS-"
            "only setup it raises an error. The mock backend returns fixture "
            "samples on canned bag paths. Bags that embed no message "
            "definitions (rosbag2 `.db3` from Humble) are decoded with the "
            "type definitions of the bag's recorded distro, or Humble when it "
            "records none; `note` says which. Arrays over 4096 elements are "
            "cut and `note` lists the fields. **Read-only by "
            "architecture**: nothing writes to the bag file. **Raises an MCP "
            "error** when the bag path does not exist, the topic is not present"
            " in the bag, or `rosbags` is not installed."
        ),
    )
    @guarded(None)
    @instrument(telemetry, "peek_bag_samples")
    def peek_bag_samples(
        path: Annotated[str, Field(description=_PATH_PARAM_DESC, min_length=1)],
        topic: Annotated[str, Field(description=_TOPIC_PARAM_DESC)],
        count: Annotated[int, Field(description=_COUNT_PARAM_DESC, ge=0)] = 5,
    ) -> SampleResult:
        return inspector.peek_bag_samples(path, topic, count)

    @mcp.tool(
        annotations=read_only_annotations("List DDS endpoints", open_world=True),
        description=(
            "List every DDS endpoint (writer and reader) announced on the bus, "
            "one `EndpointInfo` per endpoint with `role`, "
            "`topic`, `type_name`, `type_id`, the owning `participant_guid` "
            "joined with its `participant_name`, and a structured `qos` "
            "(reliability, durability, history, deadline, liveliness kind and "
            "lease, ownership kind and strength, partitions, latency budget, "
            "destination order, data representation). Use it instead of "
            "parsing `peek_dds_samples` output and joining GUID prefixes. "
            "**Spotting orphans**: `by_topic` rolls the endpoints up per "
            "topic with `writer_count`, `reader_count` and `orphan` "
            '(`"no_reader"` = a writer nobody subscribes to, `"no_writer"` = '
            "a reader nobody publishes to), plus the union of partitions. "
            "**Reading `qos`**: a duration of `None` (`deadline_ns`, "
            "`liveliness_lease_ns`, `latency_budget_ns`) means infinite or "
            "not set; a policy field of `None` means the endpoint did not "
            "announce it. **Ownership**: among EXCLUSIVE writers the live one "
            "with the highest `ownership_strength` delivers to a reader; which "
            "writer currently owns an instance is reader-side runtime state "
            "TopicForge cannot observe. **Departed endpoints**: when a participant leaves, "
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
            "data flow**: it shows which endpoints exist and how they are "
            "configured, not whether samples move. `activity` is always `None` "
            "(see `activity_note`): TopicForge holds no reader on user topics "
            "and cannot tell a silent or hung writer from a healthy one. Pair "
            "it with `detect_qos_mismatches` to see which pairs cannot match. "
            "TopicForge's own observer participant is excluded unless "
            "`include_observer` is true. Output is capped at 500 endpoints "
            "(`truncated`, `total_discovered`); `by_topic` still covers all "
            "matches. Read-only. **Raises an MCP error** when no DDS module "
            "is active. Mock mode returns a fixture matching the other mock "
            "DDS tools."
        ),
    )
    @guarded("dds")
    @instrument(telemetry, "list_endpoints")
    def list_endpoints(
        topic: Annotated[
            str | None,
            Field(
                description=(
                    "Only endpoints on this DDS topic name: a bare name such as "
                    "`scan` or a ROS 2 mangled name such as `rt/scan` (exact "
                    "name first, then the alternate form). Omit to list every "
                    "topic."
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
