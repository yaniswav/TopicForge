"""MCP tool handlers.

Handlers delegate to services and return Pydantic models for the SDK to
serialize. Exceptions propagate and become `isError: true` results (`guarded`
re-raises `AdapterError` as the SDK's `ToolError` so its text reaches the
client). A custom error envelope would report a failure as a successful call.

The `description` strings are read by LLM clients, so they state caveats,
limits and mock/live differences. They are also sent in full on every
`tools/list`, which some clients truncate: keep them short and put long
explanations in docs/CONTRACT.md.
"""

from __future__ import annotations

from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from topicforge.constants import (
    DEFAULT_NODE_TIMEOUT_S,
    DEFAULT_SAMPLE_TIMEOUT_S,
    MAX_NODE_TIMEOUT_S,
    MAX_SAMPLE_COUNT,
    MAX_SAMPLE_TIMEOUT_S,
    MIN_NODE_TIMEOUT_S,
    MIN_SAMPLE_TIMEOUT_S,
)
from topicforge.models import (
    BagAnalysis,
    EndpointListing,
    HealthReport,
    MismatchScan,
    NodeInfo,
    NodeListing,
    ParticipantEventListing,
    ParticipantListing,
    SampleResult,
    TopicInfo,
    TopicListing,
    TopicMetrics,
)
from topicforge.services import HealthService, Inspector
from topicforge.telemetry import TelemetryClient, instrument
from topicforge.tools.annotations import read_only_annotations
from topicforge.tools.guard import guarded
from topicforge.tools.schema_compact import compact_registered_tools

_TOPIC_PARAM_DESC = (
    "Fully qualified ROS 2 topic name starting with `/`, e.g. `/cmd_vel`. Segments are "
    "letters, digits and underscores (not starting with a digit); anything else is "
    "rejected before reaching the `ros2` CLI."
)

_NODE_PARAM_DESC = (
    "Fully qualified node name as `list_nodes` gives it, e.g. `/robot1/lidar_driver` "
    "(a missing leading `/` is added)."
)

_NODE_TIMEOUT_PARAM_DESC = (
    "Seconds to wait for the parameter read, 1..20, default 8. A node that does not "
    "answer is reported in `parameters_note`, not as an error."
)

_DDS_TOPIC_PARAM_DESC = (
    "DDS topic name: bare (`scan`), ROS 2 mangled (`rt/scan`) or a builtin discovery "
    "topic (`DCPSParticipant`, `DCPSSubscription`, `DCPSPublication`). Letters, digits, "
    "`_`, `/` and `::` only."
)

_DOMAIN_PARAM_DESC = (
    "Ignored: it does not switch domains and is only range-checked (0..232). The domain "
    "observed is the one joined at startup (`TOPICFORGE_DDS_DOMAIN_ID`), reported in the "
    "response `domain_id`; `note` flags a different non-zero value."
)

_COUNT_PARAM_DESC = (
    "Messages to return, default 5, at most 50 (`health_check.max_sample_count`): more is "
    "capped to 50, negative raises an error. The result's `count` may be lower."
)

_SAMPLE_COUNT_PARAM_DESC = (
    "Messages to wait for, default 5, at most 50 (more is capped and `note` says so); 0 "
    "returns nothing. Fewer arrive when the topic is slow or `timeout_s` runs out."
)

_SAMPLE_TIMEOUT_PARAM_DESC = (
    "Seconds to wait, 1..40, default 10, covering topic lookup and CLI start-up. The call "
    "returns within `timeout_s` plus a few seconds, with what arrived and a `note`. Raise "
    "it for a topic slower than 1 Hz."
)

_MAX_ARRAY_LENGTH_PARAM_DESC = (
    "Longest array, string or bytes value returned in full, 1..65536, default 128 (a "
    "541-beam `LaserScan` is cut to 128 ranges; its `summary` still covers all). Longer "
    "values are cut and listed in `_truncated_fields`. Null returns everything (large for "
    "images and clouds; a message over 1 MiB is dropped with a note)."
)

_ARRAYS_SUMMARY_PARAM_DESC = (
    "When true, arrays become a type-and-length summary: use it to read the non-array "
    "fields of large messages. Default false."
)

_PATH_PARAM_DESC = (
    "Bag path: a `.mcap` or `.db3` file or a `rosbag2_*` directory (`peek_bag_samples` "
    "also reads ROS 1 `.bag`; `analyze_bag` does not). Existence and format are checked "
    "live; mock accepts any well-formed path."
)

_SUMMARY_RATE_NOTE = (
    " **Summary and rate**: `LaserScan`, `Odometry`, `Imu`, `Image` and `PointCloud2` samples "
    "carry a `summary` of the whole message (other types: null); `rate` gives frequency, "
    "interval spread and a `verdict` measured on %s. Use `count` >= 10 (50 above 20 Hz); a "
    "multi-publisher topic such as `/tf` reads `erratic` without being broken."
)

_ROS_LANE_NOTE = (
    " **Duration**: runs the `ros2` CLI behind the ROS lock (one call at a time); the whole "
    "call, lock wait included, ends within 45 s, and if another `ros2` call holds the lock "
    "it fails at once with a `busy` error (retry)."
)

_DDS_LANE_NOTE = (
    " **Duration**: uses the DDS binding behind the DDS lock (one call at a time); the whole "
    "call, lock wait included, ends within 45 s, and if another DDS call holds the lock it "
    "fails at once with a `busy` error (retry)."
)

_NO_LOCK_NOTE = (
    " **Duration**: never waits for a lock and never runs the `ros2` CLI; it answers at once."
)

_BAG_NOTE = (
    " **Duration**: takes no lock (in-process file read, so it runs while a ROS 2 or DDS "
    "call is busy); the time follows the bag's size and how deep the first samples are."
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
            "Report TopicForge environment state as a `HealthReport`: `mode`, `ros_backend`, "
            "`dds_backend`, `ros_tools_available`, `ros2_distro`, `rmw_implementation`, "
            "`dds_domain_id` and `observed_domain_note` (only the domain joined at startup is "
            "observed), server version and sample cap; for a live DDS backend also "
            "`observer_started_ns` / `now_ns` (nothing earlier was watched) and the discovery "
            "tracker status `tracker_*` (errors, evictions or a stale pass mean gaps). `live` "
            "with `ros_backend` `none` is a DDS-only setup: use `list_endpoints`. With "
            "`dds_backend` `none`, `dds_inactive_note` says why. `payload_decoding` is "
            "`disabled` (DDS user-topic payloads are not decoded); `dds_security` is "
            "`not_supported`. **Always succeeds**: call it first when something looks wrong. "
            "Read-only; no side effects." + _NO_LOCK_NOTE
        ),
    )
    @guarded(None)
    @instrument(telemetry, "health_check")
    def health_check() -> HealthReport:
        return health.report()

    @mcp.tool(
        annotations=read_only_annotations("List ROS 2 topics", open_world=True),
        description=(
            "ROS 2 graph only; on a DDS-only setup use `list_endpoints`. List the topics on "
            "the ROS 2 graph (or the mock graph) as a `TopicListing`: each has `name`, "
            "`message_type`, `publisher_count` and `subscriber_count`; `mode_effective` tells a "
            "real graph from fixtures. QoS is not listed: use `get_topic_info`. **Empty "
            "`topics`** when the graph has none or live discovery times out. **Raises an MCP "
            "error** without a `ros2` CLI. Read-only; no side effects." + _ROS_LANE_NOTE
        ),
    )
    @guarded("ros")
    @instrument(telemetry, "list_topics")
    def list_topics() -> TopicListing:
        return inspector.list_topics()

    @mcp.tool(
        annotations=read_only_annotations("Get topic info", open_world=True),
        description=(
            "ROS 2 graph only; on a DDS-only setup use `list_endpoints`. Describe one ROS 2 "
            "`topic` (fully qualified, e.g. `/cmd_vel`) as a `TopicInfo`: counts and, in live "
            "mode, `publisher_qos` / `subscription_qos` (`reliability`, `durability` where "
            "`transient_local` marks a latched topic such as `/tf_static`, `endpoint_count`; a "
            "`null` side says why in its `_note`) and `publisher_nodes` / `subscriber_nodes`. "
            "**Raises an MCP error** for a malformed or unknown topic, or without a `ros2` CLI. "
            "Read-only; no side effects." + _ROS_LANE_NOTE
        ),
    )
    @guarded("ros")
    @instrument(telemetry, "get_topic_info")
    def get_topic_info(
        topic: Annotated[str, Field(description=_TOPIC_PARAM_DESC)],
    ) -> TopicInfo:
        return inspector.get_topic_info(topic)

    @mcp.tool(
        annotations=read_only_annotations("List ROS 2 nodes", open_world=True),
        description=(
            "ROS 2 graph only; on a DDS-only setup use `list_participants`. List the nodes on "
            "the ROS 2 graph (or the mock graph) from one `ros2 node list` call as a "
            "`NodeListing`: `name`, `namespace`, `full_name`, `duplicate_count`. `duplicates` "
            "lists full names used by several nodes (a per-node tool then answers for one of "
            "them, unidentified). Nodes starting with an underscore are hidden and a node on "
            "another `ROS_DOMAIN_ID` is invisible. **Empty `nodes`** when the graph has none. "
            "Use `get_node_info` for one node's interfaces. **Raises an MCP error** without a "
            "`ros2` CLI. Read-only; cheap (one CLI call)." + _ROS_LANE_NOTE
        ),
    )
    @guarded("ros")
    @instrument(telemetry, "list_nodes")
    def list_nodes() -> NodeListing:
        return inspector.list_nodes()

    @mcp.tool(
        annotations=read_only_annotations("Get node info", open_world=True),
        description=(
            "ROS 2 graph only; on a DDS-only setup use `list_participants`. Describe one ROS 2 "
            "node as a `NodeInfo`: the topics it publishes and subscribes to, the services and "
            "actions it serves or calls (each `{name, type}`), its parameters and "
            "`use_sim_time`. Interfaces come from the graph (`ros2 node info`), which never "
            "waits for the node. Parameters come from a read (`ros2 param dump`) bounded by "
            "`timeout_s`: **a node that does not answer in time is a finding, not an error**: "
            "`parameters` is `null`, `parameters_note` says its executor is probably blocked, "
            "and the interfaces are still returned. A value whose name contains password, "
            "secret, token, api_key or credential is replaced by `<masked>` and a long value "
            "is cut (`truncated`, `original_size`), both said in `parameters_note`. With "
            "several nodes of that name `duplicate_count` is above 1 and `note` warns. "
            "**Raises an MCP error** when the node is not on the graph (the message lists close "
            "matches), the name is malformed, or without a `ros2` CLI. **Read-only**: the only "
            "request TopicForge ever sends is a parameter read (list/get); it sends no other "
            "request to a node. **Duration**: runs the `ros2` CLI behind the ROS lock, one "
            "call at a time: up to three CLI calls (node list and node info, 8 s each at most; "
            "then the parameter read, at most `timeout_s`, 20 s). The whole call, lock wait "
            "included, ends within 45 s: if another `ros2` call holds the lock it fails at "
            "once with a `busy` error (retry), and a call that waited has less time for the "
            "parameter read."
        ),
    )
    @guarded("ros")
    @instrument(telemetry, "get_node_info")
    def get_node_info(
        node: Annotated[str, Field(description=_NODE_PARAM_DESC)],
        timeout_s: Annotated[
            float,
            Field(
                description=_NODE_TIMEOUT_PARAM_DESC,
                ge=MIN_NODE_TIMEOUT_S,
                le=MAX_NODE_TIMEOUT_S,
            ),
        ] = DEFAULT_NODE_TIMEOUT_S,
    ) -> NodeInfo:
        return inspector.get_node_info(node, timeout_s)

    @mcp.tool(
        annotations=read_only_annotations("Sample topic messages", open_world=True),
        description=(
            "ROS 2 graph only; on a DDS-only setup use `list_endpoints` or `peek_dds_samples` "
            "on `DCPSPublication` / `DCPSSubscription`. Collect up to `count` live messages "
            "from a ROS 2 `topic`, waiting at most `timeout_s` seconds, as a `SampleResult`. "
            "**Live mode** streams `ros2 topic echo` with the publishers' QoS (latched topics "
            "work) and returns what arrived by the deadline: `count` is the actual number and "
            "`note` says `N of M messages` and why it is short (no publisher, silent, slow or "
            "too large to print in time; a latched topic usually holds one message, so use "
            "`count` 1). Each sample has `payload` (nested named values), `timestamp_ns` (the "
            "publisher's clock, sim time on a simulation, 0 for a headerless message such as "
            "`std_msgs/String`; see `stamp_source`) and `received_ns` (wall clock when the CLI "
            "printed it). **Arrays**: arrays, strings and bytes are cut at 128 elements by "
            "default (a 541-beam `LaserScan` loses beams 128 and up); cut fields are listed "
            "in `payload._truncated_fields` and `note`. Raise `max_array_length` (up to "
            "65536, or null) or set `arrays_summary_only`. A message over the 1 MiB cap "
            "(`TOPICFORGE_MAX_SAMPLE_BYTES`) is dropped with a `note`. **Mock mode** returns "
            "demo-robot samples instantly. **Raises an MCP error** without a `ros2` CLI, for "
            "an unknown topic, or when the CLI fails. Read-only; never publishes."
            + (_SUMMARY_RATE_NOTE % "`received_ns` (when the CLI printed each message)")
            + (
                " **Duration**: runs the `ros2` CLI behind the ROS lock, one call at a time. "
                "It returns within `timeout_s` (at most 40) plus about 3 s to stop the CLI and "
                "decode; the whole call, lock wait included, ends within 45 s: a call that "
                "cannot get the lock fails at once with a `busy` error (retry), and one that "
                "waited has less time to collect."
            )
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
            "Summarize a ROS 2 bag at `path` as a `BagAnalysis`: storage format, duration, "
            "message count, per-topic stats and `mode_effective`. Per topic `frequency_hz` is "
            "`(n - 1) / (last - first message time)` (`frequency_basis` `topic_span`), with "
            "`first_timestamp_ns`, `last_timestamp_ns` and `latched`; a latched topic whose "
            "messages all fall within 1 s (e.g. `/tf_static`) has a null `frequency_hz`. When "
            "the bag cannot be read locally or is a `.mcap` over 200 MiB, the rate falls back "
            "to count / bag duration (`bag_duration`) and `note` says why. **Live mode** runs "
            "`ros2 bag info` on `.mcap`, `.db3` or `rosbag2_*` directories (for ROS 1 `.bag` "
            "use `peek_bag_samples`); **mock mode** returns fixture data for any bag-like "
            "path, and only mock detects anomalies. **Raises an MCP error** for a malformed or "
            "missing path (live), unparseable output, or without a `ros2` CLI. Read-only; no "
            "side effects." + _ROS_LANE_NOTE
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
            "List DDS participants observed on the bus as a `ParticipantListing`: `guid`, "
            "`vendor` (read from the GUID prefix, `unknown` for vendors that do not put theirs "
            "there, e.g. Dust DDS and RTI Connext; see `vendor_source`), optional `name` (the "
            "announced EntityName, e.g. `lidar_driver`; a participant with none is normal, not "
            "a fault: identify it by its topics), `hostname`, `domain_id` and `is_observer` "
            "(TopicForge's own read-only participant, listed like any other). Lifecycle: "
            "`status` (`active` / `left`), `first_seen_ns` / `last_seen_ns` (TopicForge's "
            "clock), `announced_ns` (DDS source timestamp) and, once left, `lost_ns` + "
            "`lost_time_source`: an upper bound of the death (exact after a clean shutdown; "
            "after a crash the lease expiry, so up to one lease earlier: 10 s Cyclone, 20 s "
            "Fast DDS, 100 s RTI). **Restarts**: a restarted node is a new GUID with the same "
            "name; `restarts_by_name` gives `lives` (GUIDs) and `departures` per name, so do "
            "not count GUIDs by hand (`seen_count` is not a restart count). Cyclone tracks "
            "discovery in the background; right after start the call waits up to 3 s for it. "
            "**Only the domain joined at startup is observed** (`health_check` "
            "`dds_domain_id`): a participant on another domain is INVISIBLE here, and "
            "`domain_id` does not switch domains (restart with `TOPICFORGE_DDS_DOMAIN_ID`). "
            "Works at the raw DDS layer, so non-ROS participants appear too. **Read-only by "
            "architecture**: it cannot publish, modify QoS or alter the bus. **Raises an MCP "
            "error** when no DDS module is active (install `topicforge[dds]`, set "
            "`TOPICFORGE_DDS_BACKEND=cyclone`); the mock backend returns fixtures." + _DDS_LANE_NOTE
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
    ) -> ParticipantListing:
        return inspector.list_participants(domain_id)

    @mcp.tool(
        annotations=read_only_annotations("Detect QoS mismatches", open_world=True),
        description=(
            "Explain why DDS readers and writers on a topic do not talk, and who will. Pairs "
            "every reader with every writer per topic and returns a `MismatchScan`: `summary` "
            "(one line, read it first), `matched` (pairs DDS will connect given the announced "
            "QoS; data flow is not observed), `reports` (incompatible or risky QoS pairs, with "
            "participant names, requested vs offered values and the failed rule in `details`), "
            "`not_matched` (pairs DDS never matches: different partitions or type names; the "
            "QoS rules are NOT evaluated for them, and `latent_incompatible_policies` lists "
            "what would also break once fixed), `hints` (probable typos in orphan topic names, "
            "type id notes), plus `pairs_checked`, `topics_scanned`, `policies_checked` and "
            "`policies_unchecked`. Checked: Partition (`*` and `?` wildcards), type name, "
            "Reliability, Durability, Deadline, Liveliness, LatencyBudget, Ownership, "
            "DestinationOrder, DataRepresentation, History (risky only, where announced). "
            "**An empty `reports` with a non-empty `not_matched` still means no data flows**, "
            "and an all-empty result does not prove the bus healthy: discovery shows the QoS "
            "DECLARED, not runtime behavior (a 'deadline missed' log with compatible QoS means "
            "the writer's real period exceeds the deadline, which TopicForge cannot see). "
            "Pass `topic` to scope the scan. The lists are capped at 200 each (`truncated`; "
            "`*_total` keep the counts). A `late_joiner` match is a VOLATILE writer whose "
            "reader joined later on the same host: normal. Reports and partition splits carry "
            "`suggested_fixes`: text for a human, never applied by TopicForge; `verify_with` "
            "names the call that confirms the repair. **Read-only by architecture**. "
            "**Raises an MCP error** when no DDS module is active; mock returns fixtures."
            + _DDS_LANE_NOTE
        ),
    )
    @guarded("dds")
    @instrument(telemetry, "detect_qos_mismatches")
    def detect_qos_mismatches(
        topic: Annotated[
            str | None,
            Field(
                description=(
                    "Optional DDS topic to scope the scan to: `scan` or `rt/scan`. Omit to "
                    "scan all topics."
                ),
                default=None,
            ),
        ] = None,
    ) -> MismatchScan:
        return inspector.detect_qos_mismatches(topic)

    @mcp.tool(
        annotations=read_only_annotations("Peek DDS samples", open_world=True),
        description=(
            "Peek recent samples on a raw DDS topic as a `SampleResult`, like `sample_messages` "
            "but read from the DDS layer (works without ROS 2). `count` defaults to 5, clamped "
            "to 50. (a) The builtin discovery topics (`DCPSParticipant`, `DCPSSubscription`, "
            "`DCPSPublication`) return structured discovery records: on Cyclone the CURRENT "
            "state (one per live participant or endpoint), not recent events (use "
            "`participant_events`); `DCPSPublication` / `DCPSSubscription` are the raw data "
            "behind `list_endpoints`. The topic may be `/scan`, `scan` or `rt/scan`. (b) User "
            "topics: payload decoding is DISABLED on every backend, so the call returns "
            "`count` 0, empty `samples` and a `note`; that does NOT mean the topic is silent. "
            "Use `list_endpoints` for its presence, writers, readers and QoS. A user topic not "
            "announced on the bus raises an error. Right after start the call waits up to 3 s "
            "for discovery. **Read-only by architecture**: it cannot publish. **Raises an MCP "
            "error** when no DDS module is active or the topic is not announced." + _DDS_LANE_NOTE
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
            "Return DDS participant lifecycle events (`discovered` / `lost`) from a recent "
            "window, e.g. 'who left in the last 5 minutes?', as a `ParticipantEventListing`, "
            "newest first: `guid`, `event_type`, `vendor`, `timestamp_ns`, `time_source`, "
            "`observed_ns`, `age_s` (seconds before the response; live only), optional `name` "
            "(announced name), `hostname` and `domain_id`. `time_source` is "
            "`dds_source_timestamp` (timestamp of the announcement or dispose) or "
            "`observed_local` (when TopicForge noticed, weakest). **Crash caveat**: a `lost` "
            "timestamp is an upper bound of the death: exact after a clean shutdown, the "
            "lease expiry after a crash, so the process died up to one lease earlier (10 s "
            "Cyclone, 20 s Fast DDS, 100 s RTI); the two cases cannot be told apart. "
            "**Restarts**: a restarted node is a new participant (one `lost` and one "
            "`discovered`, different `guid`, same `name`); `restarts_by_name` gives `lives` "
            "and `departures` per name over the window, so do not count GUIDs by hand. Capped "
            "at 200 events (`truncated`, `note`; reduce `lookback_s`). TopicForge only knows "
            "what happened since it started watching (`health_check.observer_started_ns`). "
            "**Backends**: Fast DDS uses listener callbacks; Cyclone polls discovery in the "
            "background every 0.5 s, so a participant cycle faster than the reader's history "
            "depth between two passes can be missed; mock returns a fixture timeline. Right "
            "after start the call waits up to 3 s for discovery. **Read-only by "
            "architecture**. **Raises an MCP error** when no DDS module is active." + _DDS_LANE_NOTE
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
                    "Window in seconds, 1..86400, default 300. A larger window may hit the "
                    "200-event cap."
                ),
                ge=1,
                le=86400,
            ),
        ] = 300,
    ) -> ParticipantEventListing:
        return inspector.participant_events(domain_id, lookback_s)

    @mcp.tool(
        annotations=read_only_annotations("Topic metrics", open_world=True),
        description=(
            "Return temporal metrics (frequency, sequence gaps, latency percentiles) for a DDS "
            "topic over a recent window as a `TopicMetrics`. **Read `status` first**: "
            "`unsupported_user_topic` means the payload is not decoded, so no metric exists: "
            "every null or 0 is not a measurement; `no_samples_yet` is a builtin topic with "
            "nothing buffered; `ok` means computed. **Limits**: the buffer is filled only when "
            "`peek_dds_samples` runs on the topic, so `observed_frequency_hz` reflects how "
            "often it was called, not the publish rate: a coarse presence signal. "
            "`declared_frequency_hz` is `1 / deadline` of the shortest writer Deadline "
            "announced in discovery (declared, not measured; null when none). Also "
            "`sequence_gaps_count`, `latency_ns_p50/p95/p99` and availability flags. "
            "**Read-only by architecture**. **Raises an MCP error** when no DDS module is "
            "active or `window_s` is outside 1..3600. Right after start the call waits up to "
            "3 s for discovery." + _DDS_LANE_NOTE
        ),
    )
    @guarded("dds")
    @instrument(telemetry, "topic_metrics")
    def topic_metrics(
        topic: Annotated[str, Field(description=_DDS_TOPIC_PARAM_DESC)],
        window_s: Annotated[
            int,
            Field(
                description="Window in seconds, 1..3600, default 60.",
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
            "Peek up to `count` samples from a recorded bag file as a `SampleResult`: offline, "
            "for post-mortem (`peek_dds_samples` and `sample_messages` are live). Formats, by "
            "extension: MCAP (`.mcap`), rosbag2 SQLite (`.db3`), ROS 1 (`.bag`). Returns the "
            "**first** `count` messages of the topic in recording order, not the last. "
            "**Clocks**: `timestamp_ns` is `header.stamp` when the message has a top-level "
            "header (`stamp_source` `header`), else the time in the body of `Clock`, "
            "`TFMessage` or `Log` (`payload`), else the bag record time (`recorded`); "
            "`recorded_ns` is always the record time. Each `payload` carries `_decode_status` "
            "(`full` / `partial` / `raw`). `count` defaults to 5, clamped to 50. **Uses the "
            "`rosbags` library** and needs the ROS 2 side of the runtime (an error on a "
            "DDS-only setup). Mock returns fixtures on canned paths. A bag without embedded "
            "definitions (Humble `.db3`) is decoded with those of its recorded distro, or "
            "Humble; `note` says which. Arrays over 4096 elements are cut and `note` lists "
            "them. **Read-only by architecture**. **Raises an MCP error** when the path does "
            "not exist, the topic is not in the bag, or `rosbags` is missing."
            + (_SUMMARY_RATE_NOTE % "`recorded_ns` (the bag record time of the returned messages)")
            + _BAG_NOTE
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
            "List every DDS endpoint (writer and reader) announced on the bus as an "
            "`EndpointListing`; one `EndpointInfo` each with `role`, `dds_topic` (raw, e.g. "
            "`rt/scan`), `ros_topic` (`/scan`; `null` with `ros_topic_note` for a non-ROS "
            "topic), `type_name`, `type_id`, the owning `participant_guid` with "
            "`participant_name`, and a structured `qos`. Use it instead of parsing "
            "`peek_dds_samples` and joining GUID prefixes. **Large buses**: pass `fields` "
            "`compact` to replace each `qos` by a one-line `qos_summary` and drop `type_id`, "
            "`announced_ns` and `ros_topic_note`; the default `full` is verbose, so scope with "
            "`topic` or `participant_guid` to read one topic's full QoS. **Orphans**: "
            "`by_topic` rolls endpoints up per topic with `writer_count`, `reader_count`, "
            "`orphan` (`no_reader` = a writer nobody subscribes to, `no_writer` = a reader "
            "nobody publishes to) and the union of partitions. In `qos`, a duration or policy "
            "of `None` is infinite/unset or not announced. Among EXCLUSIVE writers the live "
            "one with the highest `ownership_strength` delivers; which one owns an instance "
            "is runtime state TopicForge cannot observe. **Departed endpoints**: a leaving "
            "participant's endpoints are remembered (last 200, 1 h) and shown in `by_topic` as "
            "`departed_writers` / `departed_readers` (listed in `endpoints` only with "
            "`include_departed`), so a topic that lost its only writer is explained in one "
            "call. **Topic filter**: `rt/scan` and `scan` match each other; a filter matching "
            "nothing returns a `note` with the closest topics. **This lists discovery facts, "
            "not data flow**: it cannot tell a silent or hung writer from a healthy one; pair "
            "it with `detect_qos_mismatches`. The observer's own endpoints are excluded unless "
            "`include_observer`; ROS 2 service and action endpoints (`rq/`, `rr/`, `rs/`, "
            "`rp/`, `ra/`, `ros_discovery_info`) unless `include_internal`. Capped at 500 "
            "endpoints (`truncated`, `total`); `by_topic` covers all matches. Read-only. "
            "**Raises an MCP error** when no DDS module is active; mock returns a fixture."
            + _DDS_LANE_NOTE
        ),
    )
    @guarded("dds")
    @instrument(telemetry, "list_endpoints")
    def list_endpoints(
        topic: Annotated[
            str | None,
            Field(
                description=(
                    "Only endpoints on this DDS topic: `scan` or `rt/scan` (exact name first, "
                    "then the alternate form). Omit for every topic."
                )
            ),
        ] = None,
        participant_guid: Annotated[
            str | None,
            Field(
                description=(
                    "Only endpoints of this participant (a guid from `list_participants`). "
                    "Omit for all."
                )
            ),
        ] = None,
        include_observer: Annotated[
            bool,
            Field(description="Include TopicForge's own observer endpoints. Default false."),
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
                    "Also list endpoints whose participant left (flagged with `gone_ns`); "
                    "`by_topic` reports them either way. Default false."
                )
            ),
        ] = False,
        include_internal: Annotated[
            bool,
            Field(
                description=(
                    "Also list ROS 2 service and action endpoints (`rq/`, `rr/`, `rs/`, "
                    "`rp/`, `ra/`, `ros_discovery_info`), most of a small robot's endpoints. "
                    "Default false."
                )
            ),
        ] = False,
        fields: Annotated[
            Literal["full", "compact"],
            Field(
                description=(
                    "`full` (default) or `compact`: one-line `qos_summary` instead of the "
                    "full `qos`, no `type_id`, `announced_ns` or `ros_topic_note`. Use "
                    "`compact` on large buses."
                )
            ),
        ] = "full",
    ) -> EndpointListing:
        return inspector.list_endpoints(
            topic,
            participant_guid,
            include_observer,
            domain_id,
            include_departed,
            include_internal,
            fields,
        )

    compact_registered_tools(mcp)

    # TODO(roadmap): URDF tools: validate / inspect / generate URDF & xacro.
    # TODO(roadmap): bag anomaly detection: clock jumps, frame drops, TF gaps.
    # TODO(roadmap): dataset export: rosbag -> COCO / Hugging Face Datasets.
    # TODO(roadmap): synthetic data pipeline: Blender / Gazebo / Isaac control.
