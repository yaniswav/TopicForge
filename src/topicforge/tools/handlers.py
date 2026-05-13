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
    HealthReport,
    SampleResult,
    TopicInfo,
)
from topicforge.services import HealthService, Inspector

_TOPIC_PARAM_DESC = (
    "Fully qualified ROS2 topic name starting with `/`, e.g. `/cmd_vel` or "
    "`/camera/image_raw`. Each `/`-separated segment must start with a "
    "letter or underscore and contain only letters, digits, and "
    "underscores; everything else (whitespace, quotes, shell "
    "metacharacters, `//`, trailing `/`) is rejected before reaching the "
    "`ros2` CLI."
)

_COUNT_PARAM_DESC = (
    "Maximum number of recent messages to return. Defaults to 5; silently "
    "clamped to 50 (the hard cap that keeps tool output bounded — read it "
    "from `health_check.max_sample_count`). Negative values raise an error. "
    "The returned `SampleResult.count` reflects the actual number of "
    "samples produced — it can be lower than the request (empty topic, "
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
) -> None:
    """Register the MVP tool set on `mcp`."""

    @mcp.tool(
        description=(
            "Report TopicForge's effective runtime mode, whether ROS2 tooling is "
            "available, the server version, and the server-side sample cap. "
            "Always succeeds — call this first when something looks wrong."
        )
    )
    def health_check() -> HealthReport:
        return health.report()

    @mcp.tool(
        description=(
            "List all ROS2 topics known to the current graph (or the mock graph "
            "if running in mock mode). Returns name, message type, and "
            "publisher/subscriber counts for each topic."
        )
    )
    def list_topics() -> list[TopicInfo]:
        return inspector.list_topics()

    @mcp.tool(
        description=(
            "Return detailed info for a single ROS2 topic. `topic` must be a "
            "fully qualified topic name, e.g. `/cmd_vel`. Raises an MCP error "
            "if the topic name is malformed or the topic is unknown."
        )
    )
    def get_topic_info(
        topic: Annotated[str, Field(description=_TOPIC_PARAM_DESC)],
    ) -> TopicInfo:
        return inspector.get_topic_info(topic)

    @mcp.tool(
        description=(
            "Return up to `count` recent messages from `topic`. `topic` must be "
            "a fully qualified ROS2 name; see the `topic` parameter description "
            "for the exact accepted shape. `count` defaults to 5 and is silently "
            "clamped to 50 — request more and you receive at most 50 without "
            "warning. Returns a `SampleResult` envelope `{topic, count, samples}` "
            "where `count` is the actual number of samples returned (may be 0). "
            "In live mode the MVP shells out to `ros2 topic echo --once` with a "
            "short timeout, so the result is empty when no publisher is "
            "currently active, and every `samples[i].timestamp_ns` is 0 (the "
            "CLI does not expose receive times). The live parser only extracts "
            "top-level YAML keys into `samples[i].payload`; nested fields and "
            "the verbatim CLI output land under the reserved `_raw_text` key, "
            "so a client can reason over the raw bytes when it needs more than "
            "the flat view. In mock mode returns deterministic samples with "
            "monotonically increasing timestamps for the fictional demo robot "
            "(and no `_raw_text` key, since the payload is already structured)."
        )
    )
    def sample_messages(
        topic: Annotated[str, Field(description=_TOPIC_PARAM_DESC)],
        count: Annotated[int, Field(description=_COUNT_PARAM_DESC, ge=0)] = 5,
    ) -> SampleResult:
        samples = inspector.sample_messages(topic, count)
        return SampleResult(topic=topic, count=len(samples), samples=samples)

    @mcp.tool(
        description=(
            "Inspect a ROS2 bag at `path` and return a structured summary: "
            "duration, message count, per-topic stats, and any detected "
            "anomalies. Supports `.mcap`, `.db3`, and `.bag` paths via "
            "`ros2 bag info` in live mode. Returns rich fixture data in mock "
            "mode."
        )
    )
    def analyze_bag(
        path: Annotated[str, Field(description=_PATH_PARAM_DESC, min_length=1)],
    ) -> BagAnalysis:
        return inspector.analyze_bag(path)

    # TODO(roadmap): URDF tools — validate / inspect / generate URDF & xacro.
    # TODO(roadmap): bag anomaly detection — clock jumps, frame drops, TF gaps.
    # TODO(roadmap): dataset export — rosbag → COCO / Hugging Face Datasets.
    # TODO(roadmap): synthetic data pipeline — Blender / Gazebo / Isaac control.
