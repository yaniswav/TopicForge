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
from pydantic import BeforeValidator, Field

from topicforge.constants import (
    DEFAULT_DIAGNOSE_BUDGET_S,
    DEFAULT_NODE_TIMEOUT_S,
    DEFAULT_SAMPLE_TIMEOUT_S,
    DEFAULT_TF_STALE_AFTER_S,
    DEFAULT_TF_WINDOW_S,
    MAX_DIAGNOSE_BUDGET_S,
    MAX_NODE_TIMEOUT_S,
    MAX_SAMPLE_COUNT,
    MAX_SAMPLE_TIMEOUT_S,
    MAX_TF_STALE_AFTER_S,
    MAX_TF_WINDOW_S,
    MIN_DIAGNOSE_BUDGET_S,
    MIN_NODE_TIMEOUT_S,
    MIN_SAMPLE_TIMEOUT_S,
    MIN_TF_STALE_AFTER_S,
    MIN_TF_WINDOW_S,
)
from topicforge.models import (
    BagAnalysis,
    Diagnosis,
    EndpointListing,
    GraphDiff,
    HealthReport,
    MismatchScan,
    NodeInfo,
    NodeListing,
    ParticipantEventListing,
    ParticipantListing,
    SampleResult,
    TfReport,
    TopicInfo,
    TopicListing,
    TopicMetrics,
)
from topicforge.services import HealthService, Inspector
from topicforge.services.diagnose import DiagnoseService
from topicforge.telemetry import TelemetryClient, instrument
from topicforge.tools.annotations import read_only_annotations
from topicforge.tools.descriptions import (
    ANALYZE_BAG_DESC,
    ARRAYS_SUMMARY_PARAM_DESC,
    BAG_SNAPSHOT_PARAM_DESC,
    BASELINE_PARAM_DESC,
    COUNT_PARAM_DESC,
    DDS_TOPIC_PARAM_DESC,
    DETECT_QOS_MISMATCHES_DESC,
    DIAGNOSE_DESC,
    DOMAIN_PARAM_DESC,
    END_S_PARAM_DESC,
    GET_NODE_INFO_DESC,
    GET_TOPIC_INFO_DESC,
    GRAPH_DIFF_DESC,
    HEALTH_CHECK_DESC,
    INSPECT_TF_DESC,
    LIST_ENDPOINTS_DESC,
    LIST_NODES_DESC,
    LIST_PARTICIPANTS_DESC,
    LIST_TOPICS_DESC,
    LOG_LEVEL_PARAM_DESC,
    LOG_NODE_PARAM_DESC,
    MAX_ARRAY_LENGTH_PARAM_DESC,
    NODE_PARAM_DESC,
    NODE_TIMEOUT_PARAM_DESC,
    PARAMS_FILE_PARAM_DESC,
    PARTICIPANT_EVENTS_DESC,
    PATH_PARAM_DESC,
    PEEK_BAG_SAMPLES_DESC,
    PEEK_DDS_SAMPLES_DESC,
    SAMPLE_COUNT_PARAM_DESC,
    SAMPLE_MESSAGES_DESC,
    SAMPLE_TIMEOUT_PARAM_DESC,
    START_S_PARAM_DESC,
    TF_FRAME_PARAM_DESC,
    TOPIC_METRICS_DESC,
    TOPIC_PARAM_DESC,
)
from topicforge.tools.guard import guarded
from topicforge.tools.schema_compact import compact_registered_tools


def _log_level_name(value: object) -> object:
    """Fold `WARN`, `Error` or `warning` onto the lower-case names the enum declares."""
    if isinstance(value, str):
        name = value.strip().lower()
        return "warn" if name == "warning" else name
    return value


# Level names as ROS 2 prints them (`WARN`) are accepted; the schema keeps the lower case.
_LogLevel = Annotated[
    Literal["debug", "info", "warn", "error", "fatal"] | None,
    BeforeValidator(_log_level_name),
    Field(description=LOG_LEVEL_PARAM_DESC),
]


def _register_ros_tools(
    mcp: MCPServer,
    inspector: Inspector,
    health: HealthService,
    telemetry: TelemetryClient,
    diagnoser: DiagnoseService,
) -> None:
    """Register the ROS 2 graph tools: health, topics, nodes, TF, sampling, bag summary, diff and diagnose."""

    @mcp.tool(
        annotations=read_only_annotations("Health check", open_world=False),
        description=HEALTH_CHECK_DESC,
    )
    @guarded(None)
    @instrument(telemetry, "health_check")
    def health_check() -> HealthReport:
        return health.report()

    @mcp.tool(
        annotations=read_only_annotations("List ROS 2 topics", open_world=True),
        description=LIST_TOPICS_DESC,
    )
    @guarded("ros")
    @instrument(telemetry, "list_topics")
    def list_topics() -> TopicListing:
        return inspector.list_topics()

    @mcp.tool(
        annotations=read_only_annotations("Get topic info", open_world=True),
        description=GET_TOPIC_INFO_DESC,
    )
    @guarded("ros")
    @instrument(telemetry, "get_topic_info")
    def get_topic_info(
        topic: Annotated[str, Field(description=TOPIC_PARAM_DESC)],
    ) -> TopicInfo:
        return inspector.get_topic_info(topic)

    @mcp.tool(
        annotations=read_only_annotations("List ROS 2 nodes", open_world=True),
        description=LIST_NODES_DESC,
    )
    @guarded("ros")
    @instrument(telemetry, "list_nodes")
    def list_nodes() -> NodeListing:
        return inspector.list_nodes()

    @mcp.tool(
        annotations=read_only_annotations("Get node info", open_world=True),
        description=GET_NODE_INFO_DESC,
    )
    @guarded("ros")
    @instrument(telemetry, "get_node_info")
    def get_node_info(
        node: Annotated[str, Field(description=NODE_PARAM_DESC)],
        timeout_s: Annotated[
            float,
            Field(
                description=NODE_TIMEOUT_PARAM_DESC,
                ge=MIN_NODE_TIMEOUT_S,
                le=MAX_NODE_TIMEOUT_S,
            ),
        ] = DEFAULT_NODE_TIMEOUT_S,
        params_file: Annotated[str | None, Field(description=PARAMS_FILE_PARAM_DESC)] = None,
    ) -> NodeInfo:
        return inspector.get_node_info(node, timeout_s, params_file)

    @mcp.tool(
        annotations=read_only_annotations("Inspect TF tree", open_world=True),
        description=INSPECT_TF_DESC,
    )
    @guarded("ros")
    @instrument(telemetry, "inspect_tf")
    def inspect_tf(
        window_s: Annotated[
            float,
            Field(
                description="Seconds to listen to `/tf`, 1..20, default 5.",
                ge=MIN_TF_WINDOW_S,
                le=MAX_TF_WINDOW_S,
            ),
        ] = DEFAULT_TF_WINDOW_S,
        stale_after_s: Annotated[
            float,
            Field(
                description="A frame is stale past this many seconds of stamp time, default 2.",
                ge=MIN_TF_STALE_AFTER_S,
                le=MAX_TF_STALE_AFTER_S,
            ),
        ] = DEFAULT_TF_STALE_AFTER_S,
        source_frame: Annotated[str | None, Field(description=TF_FRAME_PARAM_DESC)] = None,
        target_frame: Annotated[str | None, Field(description="See `source_frame`.")] = None,
    ) -> TfReport:
        return inspector.inspect_tf(window_s, stale_after_s, source_frame, target_frame)

    @mcp.tool(
        annotations=read_only_annotations("Sample topic messages", open_world=True),
        description=SAMPLE_MESSAGES_DESC,
    )
    @guarded("ros")
    @instrument(telemetry, "sample_messages")
    def sample_messages(
        topic: Annotated[str, Field(description=TOPIC_PARAM_DESC)],
        count: Annotated[
            int,
            Field(
                description=SAMPLE_COUNT_PARAM_DESC,
                ge=0,
                json_schema_extra={"maximum": MAX_SAMPLE_COUNT},
            ),
        ] = 5,
        max_array_length: Annotated[
            int | None, Field(description=MAX_ARRAY_LENGTH_PARAM_DESC, ge=1, le=65536)
        ] = 128,
        arrays_summary_only: Annotated[bool, Field(description=ARRAYS_SUMMARY_PARAM_DESC)] = False,
        timeout_s: Annotated[
            float,
            Field(
                description=SAMPLE_TIMEOUT_PARAM_DESC,
                ge=MIN_SAMPLE_TIMEOUT_S,
                le=MAX_SAMPLE_TIMEOUT_S,
            ),
        ] = DEFAULT_SAMPLE_TIMEOUT_S,
        min_log_level: _LogLevel = None,
        node: Annotated[str | None, Field(description=LOG_NODE_PARAM_DESC)] = None,
    ) -> SampleResult:
        return inspector.sample_messages(
            topic,
            count,
            max_array_length=max_array_length,
            arrays_summary_only=arrays_summary_only,
            timeout_s=timeout_s,
            min_log_level=min_log_level,
            node=node,
        )

    @mcp.tool(
        annotations=read_only_annotations("Analyze bag", open_world=False),
        description=ANALYZE_BAG_DESC,
    )
    @guarded("ros")
    @instrument(telemetry, "analyze_bag")
    def analyze_bag(
        path: Annotated[str, Field(description=PATH_PARAM_DESC, min_length=1)],
    ) -> BagAnalysis:
        return inspector.analyze_bag(path)

    @mcp.tool(
        annotations=read_only_annotations("Diff the ROS 2 graph", open_world=True),
        description=GRAPH_DIFF_DESC,
    )
    @guarded("ros")
    @instrument(telemetry, "graph_diff")
    def graph_diff(
        baseline: Annotated[str, Field(description=BASELINE_PARAM_DESC)] = "previous",
        bag_path: Annotated[str | None, Field(description=BAG_SNAPSHOT_PARAM_DESC)] = None,
    ) -> GraphDiff:
        return inspector.graph_diff(baseline, bag_path)

    @mcp.tool(
        annotations=read_only_annotations("Diagnose the robot", open_world=True),
        description=DIAGNOSE_DESC,
    )
    @guarded("ros")
    @instrument(telemetry, "diagnose")
    def diagnose(
        scope: Annotated[
            Literal["all", "graph", "tf", "dds", "logs"],
            Field(description="`all`, or one group: `graph`, `tf`, `dds`, `logs`."),
        ] = "all",
        budget_s: Annotated[
            float,
            Field(
                description="Seconds for the sweep, 10..40, default 30.",
                ge=MIN_DIAGNOSE_BUDGET_S,
                le=MAX_DIAGNOSE_BUDGET_S,
            ),
        ] = DEFAULT_DIAGNOSE_BUDGET_S,
        node: Annotated[
            str | None,
            Field(description="Node read first (6 s); a failed read is critical."),
        ] = None,
    ) -> Diagnosis:
        return diagnoser.diagnose(scope, budget_s, node)


def _register_dds_tools(
    mcp: MCPServer,
    inspector: Inspector,
    telemetry: TelemetryClient,
) -> None:
    """Register the DDS discovery and observability tools (participants, QoS, DDS samples, events, metrics)."""
    # DDS tools: they need TOPICFORGE_DDS_BACKEND set to `cyclone` or
    # `mock`; with the `ros2_cli` adapter alone they raise AdapterError.

    @mcp.tool(
        annotations=read_only_annotations("List DDS participants", open_world=True),
        description=LIST_PARTICIPANTS_DESC,
    )
    @guarded("dds")
    @instrument(telemetry, "list_participants")
    def list_participants(
        domain_id: Annotated[
            int,
            Field(
                description=DOMAIN_PARAM_DESC,
                ge=0,
                le=232,
            ),
        ] = 0,
    ) -> ParticipantListing:
        return inspector.list_participants(domain_id)

    @mcp.tool(
        annotations=read_only_annotations("Detect QoS mismatches", open_world=True),
        description=DETECT_QOS_MISMATCHES_DESC,
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
        description=PEEK_DDS_SAMPLES_DESC,
    )
    @guarded("dds")
    @instrument(telemetry, "peek_dds_samples")
    def peek_dds_samples(
        topic: Annotated[str, Field(description=DDS_TOPIC_PARAM_DESC)],
        count: Annotated[int, Field(description=COUNT_PARAM_DESC, ge=0)] = 5,
    ) -> SampleResult:
        return inspector.peek_dds_samples(topic, count)

    @mcp.tool(
        annotations=read_only_annotations("DDS participant events", open_world=True),
        description=PARTICIPANT_EVENTS_DESC,
    )
    @guarded("dds")
    @instrument(telemetry, "participant_events")
    def participant_events(
        domain_id: Annotated[
            int,
            Field(
                description=DOMAIN_PARAM_DESC,
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
        description=TOPIC_METRICS_DESC,
    )
    @guarded("dds")
    @instrument(telemetry, "topic_metrics")
    def topic_metrics(
        topic: Annotated[str, Field(description=DDS_TOPIC_PARAM_DESC)],
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
                description=DOMAIN_PARAM_DESC,
                ge=0,
                le=232,
            ),
        ] = 0,
    ) -> TopicMetrics:
        return inspector.topic_metrics(topic, window_s, domain_id)


def _register_bag_tools(
    mcp: MCPServer,
    inspector: Inspector,
    telemetry: TelemetryClient,
) -> None:
    """Register the recorded-bag tools that read files in-process and take no lane lock."""

    @mcp.tool(
        annotations=read_only_annotations("Peek bag samples", open_world=False),
        description=PEEK_BAG_SAMPLES_DESC,
    )
    @guarded(None)
    @instrument(telemetry, "peek_bag_samples")
    def peek_bag_samples(
        path: Annotated[str, Field(description=PATH_PARAM_DESC, min_length=1)],
        topic: Annotated[str, Field(description=TOPIC_PARAM_DESC)],
        count: Annotated[int, Field(description=COUNT_PARAM_DESC, ge=0)] = 5,
        start_s: Annotated[float | None, Field(description=START_S_PARAM_DESC, ge=0)] = None,
        end_s: Annotated[float | None, Field(description=END_S_PARAM_DESC, ge=0)] = None,
        min_log_level: _LogLevel = None,
        node: Annotated[str | None, Field(description=LOG_NODE_PARAM_DESC)] = None,
    ) -> SampleResult:
        return inspector.peek_bag_samples(
            path,
            topic,
            count,
            start_s=start_s,
            end_s=end_s,
            min_log_level=min_log_level,
            node=node,
        )


def _register_endpoint_tools(
    mcp: MCPServer,
    inspector: Inspector,
    telemetry: TelemetryClient,
) -> None:
    """Register `list_endpoints`, the normalized DDS writer and reader listing."""

    @mcp.tool(
        annotations=read_only_annotations("List DDS endpoints", open_world=True),
        description=LIST_ENDPOINTS_DESC,
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
                description=DOMAIN_PARAM_DESC,
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


def register_tools(
    mcp: MCPServer,
    inspector: Inspector,
    health: HealthService,
    telemetry: TelemetryClient,
) -> None:
    """Register the tool set on `mcp`.

    With telemetry disabled (the default), `instrument(...)` returns the
    handler unchanged. The call order below is the order clients see in
    `tools/list`, so it interleaves the DDS, bag and endpoint groups as before.
    """
    diagnoser = DiagnoseService(inspector, health)
    _register_ros_tools(mcp, inspector, health, telemetry, diagnoser)
    _register_dds_tools(mcp, inspector, telemetry)
    _register_bag_tools(mcp, inspector, telemetry)
    _register_endpoint_tools(mcp, inspector, telemetry)

    compact_registered_tools(mcp)

    # TODO(roadmap): URDF tools: validate / inspect / generate URDF & xacro.
    # TODO(roadmap): bag anomaly detection: clock jumps, frame drops, TF gaps.
