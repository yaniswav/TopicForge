"""`graph_diff`: snapshots, the ring, the pure diff, the mock scenario and the MCP layer.

Live snapshots are built from a scripted adapter, so no ROS 2 is needed. The bench in
`tests/integration/ros2/` runs the same calls against a real graph.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from topicforge.adapters.base import AdapterError
from topicforge.adapters.common.nodes import build_node_listing
from topicforge.adapters.ros2_mock import MockAdapter
from topicforge.config import Settings
from topicforge.models import (
    BagAnalysis,
    BagTopicStats,
    GraphDiff,
    NodeListing,
    QosProfile,
    TopicListItem,
)
from topicforge.server import build_app
from topicforge.services import Inspector, graph_snapshot
from topicforge.services.graph_diff import (
    GraphDiffService,
    diff_snapshots,
    parse_baseline,
)
from topicforge.services.graph_snapshot import (
    RING_SIZE,
    GraphSnapshot,
    SnapshotRing,
    TopicState,
)

SCAN = "sensor_msgs/msg/LaserScan"
TWIST = "geometry_msgs/msg/Twist"


class FakeAdapter:
    """A scripted adapter: set `nodes`, `topics`, `endpoints` or `bags`, then take snapshots."""

    name = "ros2_cli"
    effective_mode = "live"

    def __init__(self) -> None:
        self.nodes = ["/a", "/b"]
        self.topics = {"/scan": (SCAN, 1, 1), "/cmd_vel": (TWIST, 1, 2)}
        self.endpoints: list[Any] | Exception = AdapterError("DDS module is not active")
        self.endpoints_truncated = False
        self.bags: dict[str, BagAnalysis] = {}

    def list_nodes(self) -> NodeListing:
        return build_node_listing(self.nodes, "live")

    def list_topics(self) -> list[TopicListItem]:
        return [
            TopicListItem(name=n, message_type=t, publisher_count=p, subscriber_count=s)
            for n, (t, p, s) in self.topics.items()
        ]

    def list_endpoints(self, *args: Any, **kwargs: Any) -> Any:
        if isinstance(self.endpoints, Exception):
            raise self.endpoints
        return SimpleNamespace(endpoints=self.endpoints, truncated=self.endpoints_truncated)

    def analyze_bag(self, path: str) -> BagAnalysis:
        if path not in self.bags:
            raise AdapterError(f"Bag path does not exist: {path}")
        return self.bags[path]


def writer(topic: str, reliability: str = "RELIABLE", durability: str = "VOLATILE") -> Any:
    return SimpleNamespace(
        role="writer",
        ros_topic=topic,
        qos=QosProfile(reliability=reliability, durability=durability),  # type: ignore[arg-type]
    )


def bag(*topics: BagTopicStats, path: str = "/b/x") -> BagAnalysis:
    return BagAnalysis(
        path=path,
        duration_s=10.0,
        message_count=sum(t.message_count for t in topics),
        topics=list(topics),
        mode_effective="live",
    )


def bag_topic(name: str, type_: str, rate: float | None = None, **kw: Any) -> BagTopicStats:
    return BagTopicStats(name=name, message_type=type_, message_count=10, frequency_hz=rate, **kw)


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000_000_000

    def __call__(self) -> int:
        self.now += 1_000_000_000
        return self.now


@pytest.fixture(autouse=True)
def no_recent_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    """Other tests record spawned pids in a process-wide ring; keep this module independent."""
    monkeypatch.setattr(graph_snapshot, "recent_spawns", list)


@pytest.fixture
def fake() -> FakeAdapter:
    return FakeAdapter()


@pytest.fixture
def service(fake: FakeAdapter) -> GraphDiffService:
    return GraphDiffService(fake, Clock())  # type: ignore[arg-type]


def kinds(diff: GraphDiff) -> list[tuple[str, str, str]]:
    return [(c.kind, c.change, c.name) for c in diff.changes]


# ----- the mock scenario -----


def test_mock_first_call_reports_exactly_the_three_planned_differences() -> None:
    diff = Inspector(MockAdapter()).graph_diff()
    assert diff.baseline_id == "snap-0" and diff.snapshot_id == "snap-1"
    assert kinds(diff) == [
        ("node", "added", "/imu_driver"),
        ("topic", "added", "/imu/data"),
        ("qos", "changed", "/scan"),
    ]
    qos = diff.changes[2]
    assert (qos.field, qos.before, qos.after) == (
        "writer_qos",
        "reliable/volatile",
        "best_effort/volatile",
    )
    assert (diff.added_count, diff.removed_count, diff.changed_count) == (2, 0, 1)
    assert diff.builtin_changes_count == 0
    assert diff.snapshot_ids == ["snap-0", "snap-1"]
    assert diff.note is not None and "snap-0" in diff.note


def test_mock_second_call_reports_nothing() -> None:
    inspector = Inspector(MockAdapter())
    inspector.graph_diff()
    second = inspector.graph_diff()
    assert second.baseline_id == "snap-1" and second.snapshot_id == "snap-2"
    assert second.changes == []
    assert (second.added_count, second.removed_count, second.changed_count) == (0, 0, 0)


def test_mock_baseline_is_derived_from_the_fixtures_not_copied() -> None:
    current = Inspector(MockAdapter()).graph_diff().current
    baseline = Inspector(MockAdapter()).graph_diff().baseline
    assert baseline is not None
    assert (current.node_count, current.topic_count) == (
        (baseline.node_count or 0) + 1,
        baseline.topic_count + 1,
    )


def test_mock_bag_is_accepted_as_a_baseline() -> None:
    diff = Inspector(MockAdapter()).graph_diff(baseline="/tmp/demo.mcap")
    assert diff.baseline is not None and diff.baseline.source == "bag"
    assert diff.baseline_id == "/tmp/demo.mcap"
    assert diff.removed_count == 0
    assert {c.name for c in diff.changes if c.kind == "topic"} >= {"/imu/data", "/tf_static"}
    assert {"nodes", "rates", "counts", "qos"} <= set(diff.not_compared)
    assert diff.note is not None and "added means present live and absent from the bag" in diff.note


# ----- the pure diff -----


def test_node_added_and_removed(fake: FakeAdapter, service: GraphDiffService) -> None:
    service.diff("previous", None)
    fake.nodes = ["/b", "/c"]
    diff = service.diff("previous", None)
    assert kinds(diff) == [("node", "removed", "/a"), ("node", "added", "/c")]


def test_topic_added_and_removed(fake: FakeAdapter, service: GraphDiffService) -> None:
    service.diff("previous", None)
    del fake.topics["/scan"]
    fake.topics["/imu"] = ("sensor_msgs/msg/Imu", 1, 0)
    diff = service.diff("previous", None)
    assert kinds(diff) == [("topic", "added", "/imu"), ("topic", "removed", "/scan")]
    added, removed = diff.changes
    assert added.after == "sensor_msgs/msg/Imu" and removed.before == SCAN


def test_type_change(fake: FakeAdapter, service: GraphDiffService) -> None:
    service.diff("previous", None)
    fake.topics["/scan"] = ("sensor_msgs/msg/PointCloud2", 1, 1)
    diff = service.diff("previous", None)
    assert kinds(diff) == [("type", "changed", "/scan")]
    assert diff.changes[0].field == "message_type"
    assert diff.changes[0].before == SCAN


def test_writer_qos_change_with_a_dds_backend(fake: FakeAdapter, service: GraphDiffService) -> None:
    fake.endpoints = [writer("/scan", "RELIABLE"), writer("/cmd_vel")]
    first = service.diff("previous", None)
    assert first.current.has_qos
    fake.endpoints = [writer("/scan", "BEST_EFFORT"), writer("/cmd_vel")]
    diff = service.diff("previous", None)
    assert kinds(diff) == [("qos", "changed", "/scan")]
    assert (diff.changes[0].before, diff.changes[0].after) == (
        "reliable/volatile",
        "best_effort/volatile",
    )
    assert "qos" not in diff.not_compared


def test_writers_of_several_profiles_are_one_sorted_set(fake: FakeAdapter) -> None:
    fake.endpoints = [writer("/scan", "RELIABLE"), writer("/scan", "BEST_EFFORT")]
    snap = graph_snapshot.build_live_snapshot(fake, 1)  # type: ignore[arg-type]
    assert snap.qos == {"/scan": "best_effort/volatile, reliable/volatile"}


def test_qos_is_not_compared_without_a_dds_backend(service: GraphDiffService) -> None:
    diff = service.diff("previous", None)
    assert not diff.current.has_qos
    assert "no DDS backend" in service.diff("previous", None).not_compared["qos"]


def test_publisher_and_subscriber_count_change(
    fake: FakeAdapter, service: GraphDiffService
) -> None:
    service.diff("previous", None)
    fake.topics["/cmd_vel"] = (TWIST, 2, 1)
    diff = service.diff("previous", None)
    assert [(c.kind, c.field, c.before, c.after) for c in diff.changes] == [
        ("count", "publisher_count", "1", "2"),
        ("count", "subscriber_count", "2", "1"),
    ]


def test_builtin_counter_noise_is_counted_apart(
    fake: FakeAdapter, service: GraphDiffService
) -> None:
    fake.topics["/rosout"] = ("rcl_interfaces/msg/Log", 3, 1)
    fake.topics["/parameter_events"] = ("rcl_interfaces/msg/ParameterEvent", 3, 3)
    service.diff("previous", None)
    fake.topics["/rosout"] = ("rcl_interfaces/msg/Log", 4, 1)
    fake.topics["/parameter_events"] = ("rcl_interfaces/msg/ParameterEvent", 4, 5)
    diff = service.diff("previous", None)
    assert diff.changes == []
    assert diff.builtin_changes_count == 3


def test_a_lingering_cli_reader_warning_is_added_to_subscriber_counts(
    fake: FakeAdapter, service: GraphDiffService, monkeypatch: pytest.MonkeyPatch
) -> None:
    service.diff("previous", None)
    fake.topics["/scan"] = (SCAN, 1, 2)
    monkeypatch.setattr(graph_snapshot, "recent_spawns", lambda: [(42, 1_000_000_000_000)])
    diff = service.diff("previous", None)
    assert "may linger 2 s" in diff.changes[0].text
    fake.topics["/scan"] = (SCAN, 2, 2)
    monkeypatch.setattr(graph_snapshot, "recent_spawns", list)
    quiet = service.diff("previous", None)
    assert "linger" not in quiet.changes[0].text


# ----- bags -----


def test_bag_against_bag_compares_rates_with_a_20_percent_tolerance(fake: FakeAdapter) -> None:
    fake.bags["/b/old"] = bag(
        bag_topic("/scan", SCAN, 10.0), bag_topic("/odom", "nav_msgs/msg/Odometry", 10.0)
    )
    fake.bags["/b/new"] = bag(
        bag_topic("/scan", SCAN, 7.0), bag_topic("/odom", "nav_msgs/msg/Odometry", 8.5)
    )
    service = GraphDiffService(fake, Clock())  # type: ignore[arg-type]
    diff = service.diff("/b/old", "/b/new")
    assert kinds(diff) == [("rate", "changed", "/scan")]
    assert (diff.changes[0].before, diff.changes[0].after) == ("10", "7")
    assert "rates" not in diff.not_compared
    assert {"nodes", "counts"} <= set(diff.not_compared)


def test_a_rate_exactly_at_the_tolerance_is_not_a_change() -> None:
    old = GraphSnapshot("a", "bag", 0, {"/t": TopicState("T", rate_hz=10.0)})
    new = GraphSnapshot("b", "bag", 0, {"/t": TopicState("T", rate_hz=12.0)})
    assert diff_snapshots(old, new).changes == []
    faster = GraphSnapshot("b", "bag", 0, {"/t": TopicState("T", rate_hz=12.1)})
    assert len(diff_snapshots(old, faster).changes) == 1


def test_latching_change_between_two_bags(fake: FakeAdapter) -> None:
    fake.bags["/b/old"] = bag(bag_topic("/map", "nav_msgs/msg/OccupancyGrid", latched=False))
    fake.bags["/b/new"] = bag(bag_topic("/map", "nav_msgs/msg/OccupancyGrid", latched=True))
    diff = GraphDiffService(fake, Clock()).diff("/b/old", "/b/new")  # type: ignore[arg-type]
    assert [(c.kind, c.field, c.before, c.after) for c in diff.changes] == [
        ("qos", "latched", "volatile", "transient_local")
    ]


def test_bag_internal_topics_are_left_out(fake: FakeAdapter) -> None:
    fake.bags["/b/x"] = bag(
        bag_topic("/scan", SCAN),
        bag_topic(
            "/events/write_split", "rosbag2_interfaces/msg/WriteSplitEvent", kind="rosbag2_internal"
        ),
    )
    snap = graph_snapshot.build_bag_snapshot(fake, "/b/x", 1)  # type: ignore[arg-type]
    assert list(snap.topics) == ["/scan"]


def test_live_against_a_bag_baseline_reads_added_as_present_live(fake: FakeAdapter) -> None:
    fake.bags["/b/x"] = bag(bag_topic("/scan", SCAN, 10.0))
    diff = GraphDiffService(fake, Clock()).diff("/b/x", None)  # type: ignore[arg-type]
    assert kinds(diff) == [("topic", "added", "/cmd_vel")]
    assert diff.removed_count == 0
    assert set(diff.not_compared) == {"nodes", "rates", "counts", "qos"}
    assert diff.note is not None and "added means present live and absent from the bag" in diff.note
    assert diff.baseline is not None and diff.baseline.node_count is None
    assert diff.baseline.has_rates and not diff.current.has_rates


def test_a_bag_as_current_side_reads_removed_as_present_live(fake: FakeAdapter) -> None:
    fake.bags["/b/x"] = bag(bag_topic("/scan", SCAN, 10.0))
    service = GraphDiffService(fake, Clock())  # type: ignore[arg-type]
    service.diff("previous", None)
    diff = service.diff("previous", "/b/x")
    assert kinds(diff) == [("topic", "removed", "/cmd_vel")]
    assert diff.current.source == "bag" and diff.current.path == "/b/x"
    assert diff.note is not None and "removed means present live and absent" in diff.note


def test_a_bag_baseline_is_not_stored_in_the_ring(fake: FakeAdapter) -> None:
    fake.bags["/b/x"] = bag(bag_topic("/scan", SCAN))
    diff = GraphDiffService(fake, Clock()).diff("/b/x", None)  # type: ignore[arg-type]
    assert diff.snapshot_ids == ["snap-1"]


# ----- the ring and the baseline argument -----


def test_previous_without_a_baseline_stores_a_snapshot_and_says_so(
    service: GraphDiffService,
) -> None:
    first = service.diff("previous", None)
    assert first.baseline is None and first.baseline_id is None
    assert first.changes == []
    assert first.snapshot_id == "snap-1"
    assert first.note is not None and first.note.startswith("No baseline yet: snapshot snap-1")
    second = service.diff("previous", None)
    assert second.baseline_id == "snap-1" and second.note is None


def test_a_snapshot_id_is_a_baseline(fake: FakeAdapter, service: GraphDiffService) -> None:
    service.diff("previous", None)
    fake.nodes = ["/a"]
    service.diff("previous", None)
    fake.nodes = ["/a", "/b", "/c"]
    diff = service.diff("snap-1", None)
    assert diff.baseline_id == "snap-1"
    assert kinds(diff) == [("node", "added", "/c")]


def test_the_ring_keeps_sixteen_snapshots_and_ids_never_repeat(
    service: GraphDiffService,
) -> None:
    for _ in range(RING_SIZE + 4):
        diff = service.diff("previous", None)
    assert diff.snapshot_id == f"snap-{RING_SIZE + 4}"
    assert diff.snapshot_ids == [f"snap-{n}" for n in range(5, RING_SIZE + 5)]
    with pytest.raises(AdapterError, match="unknown snapshot id 'snap-1'"):
        service.diff("snap-1", None)


def test_unknown_id_lists_the_held_ids_and_stores_nothing(service: GraphDiffService) -> None:
    service.diff("previous", None)
    service.diff("previous", None)
    with pytest.raises(AdapterError, match=r"snap-9.*held snapshots: snap-1, snap-2"):
        service.diff("snap-9", None)
    assert service.diff("previous", None).snapshot_ids == ["snap-1", "snap-2", "snap-3"]


def test_unknown_id_with_an_empty_ring_says_none_yet(service: GraphDiffService) -> None:
    with pytest.raises(AdapterError, match="held snapshots: none yet"):
        service.diff("snap-1", None)


def test_an_unreadable_baseline_bag_stores_nothing(service: GraphDiffService) -> None:
    with pytest.raises(AdapterError, match="does not exist"):
        service.diff("/b/missing", None)
    assert service.diff("previous", None).snapshot_ids == ["snap-1"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("previous", ("previous", "previous")),
        (" snap-12 ", ("id", "snap-12")),
        ("/bags/yesterday", ("bag", "/bags/yesterday")),
        ("snap-x", ("bag", "snap-x")),
    ],
)
def test_parse_baseline(text: str, expected: tuple[str, str]) -> None:
    assert parse_baseline(text) == expected


def test_a_blank_baseline_is_refused() -> None:
    with pytest.raises(AdapterError, match="baseline must be"):
        parse_baseline("   ")


def test_the_ring_assigns_monotonic_ids_and_seeds_keep_theirs() -> None:
    ring = SnapshotRing(size=2)
    ring.seed(GraphSnapshot("snap-0", "live", 0, {}))
    ids = [ring.store(GraphSnapshot("", "live", 0, {})).id for _ in range(3)]
    assert ids == ["snap-1", "snap-2", "snap-3"]
    assert ring.ids() == ["snap-2", "snap-3"]
    assert ring.get("snap-0") is None


def test_only_the_mock_adapter_is_seeded(fake: FakeAdapter) -> None:
    with pytest.raises(AdapterError, match="unknown snapshot id 'snap-0'"):
        GraphDiffService(fake, Clock()).diff("snap-0", None)  # type: ignore[arg-type]


def test_truncated_endpoints_are_flagged(fake: FakeAdapter) -> None:
    fake.endpoints = [writer("/scan")]
    fake.endpoints_truncated = True
    service = GraphDiffService(fake, Clock())  # type: ignore[arg-type]
    service.diff("previous", None)
    note = service.diff("previous", None).note
    assert note is not None and "first 500 endpoints" in note


# ----- through the MCP layer -----


def _app() -> Any:
    return build_app(
        Settings(mode="mock", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False)
    )


def test_graph_diff_tool_returns_the_envelope() -> None:
    result = asyncio.run(_app().call_tool("graph_diff", {})).structured_content
    assert result is not None
    assert result["baseline_id"] == "snap-0" and len(result["changes"]) == 3
    assert set(result["changes"][0]) == {
        "kind",
        "change",
        "name",
        "field",
        "before",
        "after",
        "text",
    }


def test_graph_diff_tool_refuses_a_bad_bag_path_and_a_wrong_id() -> None:
    app = _app()
    with pytest.raises(ToolError, match="does not look like a ROS2 bag"):
        asyncio.run(app.call_tool("graph_diff", {"baseline": "/tmp/notes.txt"}))
    with pytest.raises(ToolError, match="held snapshots: snap-0"):
        asyncio.run(app.call_tool("graph_diff", {"baseline": "snap-7"}))
    with pytest.raises(ToolError, match="path must be a non-empty string"):
        asyncio.run(app.call_tool("graph_diff", {"bag_path": "  "}))
    with pytest.raises(ToolError, match="baseline must be"):
        asyncio.run(app.call_tool("graph_diff", {"baseline": " "}))


def test_graph_diff_tool_can_snapshot_a_bag() -> None:
    result = asyncio.run(_app().call_tool("graph_diff", {"bag_path": "/tmp/demo.mcap"}))
    data = result.structured_content
    assert data is not None
    assert data["current"]["source"] == "bag" and data["current"]["path"] == "/tmp/demo.mcap"
    assert {"nodes", "counts"} <= set(data["not_compared"])
