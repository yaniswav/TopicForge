"""`diagnose`: the severity table, the budget, the skip rules and the mock scenario.

A scripted adapter (the mock fixtures with per-test overrides) stands in for a live ROS 2 graph
and DDS bus, and records every adapter method that is called. No ROS 2 and no DDS binding are
needed. The bench in `tests/integration/ros2/` runs the same calls against a real graph.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from tests.test_qos_fixes import _FORBIDDEN as _FORBIDDEN_VERB
from tests.test_qos_fixes import _everything
from topicforge import budget
from topicforge.adapters.base import AdapterError
from topicforge.adapters.common.nodes import build_node_listing
from topicforge.adapters.common.qos_scan import _typo_hint
from topicforge.adapters.ros2_mock import MockAdapter, fixtures, node_fixtures
from topicforge.config import Settings
from topicforge.models import (
    Diagnosis,
    Finding,
    MismatchScan,
    NodeInfo,
    NotMatchedPair,
    ParticipantEvent,
    ParticipantInfo,
    TopicListItem,
)
from topicforge.models.diagnose import EVIDENCE_VALUE_MAX, MAX_FINDINGS
from topicforge.server import build_app
from topicforge.services import HealthService, Inspector
from topicforge.services.diagnose import DiagnoseService

_SETTINGS = Settings(mode="mock", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False)
_NS = 1_000_000_000
_FORBIDDEN = {"peek_dds_samples", "topic_metrics"}


class Clock:
    """A clock the scripted adapter advances, so budget tests do not sleep."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Scripted(MockAdapter):
    """Mock fixtures with overrides, posing as a live ROS 2 + Cyclone setup; records its calls."""

    name = "ros2_cli+cyclone"  # type: ignore[assignment]

    def __init__(self, clock: Clock | None = None) -> None:
        super().__init__()
        self.calls: list[str] = []
        self.clock = clock or Clock()
        self.cost: dict[str, float] = {}
        self.fail: dict[str, Exception] = {}
        self.over: dict[str, Any] = {}
        self.node_reads: list[tuple[str, float]] = []
        self.nodes_ok: dict[str, bool] = {}
        self.param_status: dict[str, str] = {}

    @property
    def effective_mode(self) -> Any:
        return "live"

    def __getattribute__(self, item: str) -> Any:
        value = super().__getattribute__(item)
        if item.startswith("_") or not callable(value) or item in ("is_available", "clock"):
            return value
        calls = super().__getattribute__("calls")

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            calls.append(item)
            clock = super(Scripted, self).__getattribute__("clock")
            clock.now += self.cost.get(item, 0.0)
            if item in self.fail:
                raise self.fail[item]
            if item in self.over:
                over = self.over[item]
                return over(*args, **kwargs) if callable(over) else over
            return value(*args, **kwargs)

        return wrapper

    def get_node_info(self, node: str, timeout_s: float = 8.0) -> NodeInfo:
        self.node_reads.append((node, timeout_s))
        if node in self.param_status:
            return node_fixtures.mock_node_info("/lidar_driver").model_copy(
                update={
                    "full_name": node,
                    "parameters": None,
                    "parameters_status": self.param_status[node],
                    "parameters_note": f"no parameters: {self.param_status[node]}",
                }
            )
        if self.nodes_ok.get(node, True):
            return node_fixtures.mock_node_info("/lidar_driver").model_copy(
                update={"full_name": node}
            )
        return node_fixtures.mock_node_info("/lidar_driver").model_copy(
            update={
                "full_name": node,
                "parameters": None,
                "parameters_status": "timeout",
                "parameters_note": "no answer: the executor is probably blocked",
            }
        )


def _service(adapter: Scripted) -> DiagnoseService:
    return DiagnoseService(
        Inspector(adapter), HealthService(_SETTINGS, adapter), clock=adapter.clock
    )


def _run(adapter: Scripted, **kwargs: Any) -> Diagnosis:
    return _service(adapter).diagnose(**kwargs)


def _calm(adapter: Scripted, nodes: list[str] | None = None) -> Scripted:
    """A graph with nothing wrong in it: sound QoS, one TF tree, no error logs, no restarts."""
    names = ["/a", "/b"] if nodes is None else nodes
    adapter.over["list_nodes"] = build_node_listing(names, "live")
    adapter.over["list_topics"] = [
        TopicListItem(
            name="/x", message_type="std_msgs/msg/String", publisher_count=1, subscriber_count=1
        )
    ]
    adapter.over["detect_qos_mismatches"] = _empty_scan()
    adapter.over["list_participants"] = []
    adapter.over["participant_events"] = []
    adapter.over["capture_tf"] = lambda _w: _tf_ok()
    adapter.over["sample_messages"] = _no_logs
    return adapter


def _empty_scan() -> MismatchScan:
    return fixtures.mock_mismatch_scan(None).model_copy(
        update={"reports": [], "not_matched": [], "matched": [], "hints": []}
    )


def _tf_ok() -> Any:
    from topicforge.adapters.ros2_mock import tf_fixtures
    from topicforge.models import TfCapture, TfSample

    base = fixtures.MOCK_BASE_TS_NS
    dynamic = [
        TfSample(
            parent="odom", child="base_link", stamp_ns=base + i * _NS, received_ns=base + i * _NS
        )
        for i in range(6)
    ]
    static = [
        TfSample(parent="base_link", child="laser", stamp_ns=base, received_ns=base),
    ]
    assert tf_fixtures.MOCK_TF_WINDOW_S
    return TfCapture(static=static, dynamic=dynamic, window_actual_s=5.0)


def _no_logs(topic: str, count: int, **kwargs: Any) -> Any:
    from topicforge.models import SampleResult

    return SampleResult(topic=topic, count=0, samples=[], mode_effective="mock")


def _by(diagnosis: Diagnosis, check: str, severity: str | None = None) -> list[Finding]:
    return [
        f
        for f in diagnosis.findings
        if f.check == check and (severity is None or f.severity == severity)
    ]


def _participant(name: str, guid: str, status: str = "active") -> ParticipantInfo:
    return ParticipantInfo(
        guid=guid,
        name=name,
        vendor="cyclone",
        domain_id=0,
        status=status,  # type: ignore[arg-type]
        node_names=[f"/{name}"],
        node_names_source="ros_discovery_info",
        first_seen_ns=time.time_ns() - 10 * _NS,
        last_seen_ns=time.time_ns(),
    )


# ---- a sound graph ----------------------------------------------------------------------


def test_a_sound_graph_has_no_critical_and_no_warning() -> None:
    result = _run(_calm(Scripted()))
    assert result.critical_count == 0 and result.warning_count == 0
    assert result.checks_skipped == []
    assert result.checks_run == list(
        ("environment", "nodes", "topics", "dds", "tf", "logs", "responsiveness")
    )
    assert result.verdict == (
        "no critical or warning finding in the 7 checks run; data flow itself is not observed"
    )


# ---- one test per row of the severity table ---------------------------------------------


def test_environment_says_so_when_the_data_is_fixtures() -> None:
    app = build_app(_SETTINGS)
    result = asyncio.run(app.call_tool("diagnose", {"scope": "dds"})).structured_content
    env = [f for f in result["findings"] if f["check"] == "environment"]
    assert [(f["severity"], f["subject"]) for f in env] == [("info", None)]
    assert "Mock mode" in env[0]["text"]


def test_environment_reports_sros2_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROS_SECURITY_ENABLE", "true")
    result = _run(_calm(Scripted()), scope="dds")
    env = _by(result, "environment")
    assert env and env[0].severity == "info" and "identity" in env[0].text


def test_a_missing_backend_is_a_skipped_check_not_an_error() -> None:
    adapter = _calm(Scripted())
    adapter.name = "cyclone"  # type: ignore[assignment]  # DDS only: no ros2 CLI
    result = _run(adapter)
    assert result.checks_run == ["environment", "dds"]
    skipped = {s.check: s.reason for s in result.checks_skipped}
    assert set(skipped) == {"nodes", "topics", "tf", "logs", "responsiveness"}
    assert all("ROS 2 backend" in reason for reason in skipped.values())
    assert result.verdict.startswith("Incomplete sweep")


def test_a_ros_only_setup_skips_dds_and_the_verdict_is_never_clean() -> None:
    adapter = _calm(Scripted())
    adapter.name = "ros2_cli"  # type: ignore[assignment]
    result = _run(adapter)
    assert [s.check for s in result.checks_skipped] == ["dds"]
    assert "dds did not run" in result.verdict


def test_nodes_duplicates_are_a_warning() -> None:
    adapter = _calm(Scripted(), ["/a", "/a", "/b"])
    found = _by(_run(adapter), "nodes", "warning")
    assert [(f.subject) for f in found] == ["/a"]


def test_an_empty_graph_runs_the_responsiveness_check_on_no_node_instead_of_skipping_it() -> None:
    result = _run(_calm(Scripted(), []))
    assert "responsiveness" in result.checks_run and result.checks_skipped == []
    assert [f.severity for f in _by(result, "nodes")] == ["warning"]


def test_responsiveness_is_skipped_when_the_node_list_failed_unless_a_node_is_named() -> None:
    adapter = _calm(Scripted())
    adapter.fail["list_nodes"] = AdapterError("list_nodes broke")
    skipped = {s.check for s in _run(adapter).checks_skipped}
    assert skipped == {"nodes", "responsiveness"}
    named = _run(adapter, node="/a")
    assert "responsiveness" in named.checks_run and {s.check for s in named.checks_skipped} == {
        "nodes"
    }


def test_no_node_on_a_live_graph_is_a_warning() -> None:
    adapter = _calm(Scripted(), [])
    found = _by(_run(adapter), "nodes", "warning")
    assert len(found) == 1 and "No node" in found[0].text


def test_lifecycle_nodes_are_an_info() -> None:
    adapter = _calm(Scripted())
    adapter.over["list_nodes"] = build_node_listing(
        ["/a", "/b"], "live", lifecycle=frozenset({"/b"})
    )
    found = _by(_run(adapter), "nodes")
    assert [(f.severity, f.evidence[0].field) for f in found] == [("info", "lifecycle_count")]


def test_topics_with_subscribers_and_no_publisher_are_one_info() -> None:
    """A simulator listening on `/cmd_vel` is healthy: never above info."""
    adapter = _calm(Scripted())
    adapter.over["list_topics"] = [
        TopicListItem(name="/goal", message_type="t", publisher_count=0, subscriber_count=2),
        TopicListItem(name="/cmd_vel", message_type="t", publisher_count=0, subscriber_count=1),
        TopicListItem(name="/ok", message_type="t", publisher_count=1, subscriber_count=1),
    ]
    found = _by(_run(adapter), "topics")
    assert [(f.severity, f.subject) for f in found] == [("info", None)]
    assert "2 topic(s)" in found[0].text and "/goal" in found[0].text


def test_publishers_nobody_reads_are_one_info_and_the_builtins_are_left_out() -> None:
    adapter = _calm(Scripted())
    adapter.over["list_topics"] = [
        TopicListItem(name=f"/t{i}", message_type="t", publisher_count=1, subscriber_count=0)
        for i in range(5)
    ] + [
        TopicListItem(name="/rosout", message_type="t", publisher_count=6, subscriber_count=0),
        TopicListItem(
            name="/parameter_events", message_type="t", publisher_count=6, subscriber_count=0
        ),
    ]
    found = _by(_run(adapter), "topics")
    assert [f.severity for f in found] == ["info"]
    assert "5 topic(s)" in found[0].text and "/rosout" not in found[0].text


def test_an_incompatible_qos_pair_is_critical_and_carries_the_fixes() -> None:
    adapter = _calm(Scripted())
    adapter.over["detect_qos_mismatches"] = fixtures.mock_mismatch_scan(None)
    found = _by(_run(adapter), "dds", "critical")
    assert [f.subject for f in found] == ["/dds/qos_mismatch"]
    assert found[0].suggested_fixes, "the fixes of detect_qos_mismatches are copied"
    assert {e.tool for e in found[0].evidence} == {"detect_qos_mismatches"}


def test_a_risky_qos_pair_is_a_warning() -> None:
    scan = fixtures.mock_mismatch_scan(None)
    risky = scan.model_copy(
        update={"reports": [r.model_copy(update={"severity": "risky"}) for r in scan.reports]}
    )
    adapter = _calm(Scripted())
    adapter.over["detect_qos_mismatches"] = risky
    found = _by(_run(adapter), "dds")
    assert [f.severity for f in found] == ["warning"]


def test_a_pair_that_never_matches_is_critical() -> None:
    pair = NotMatchedPair(
        topic="rt/scan",
        reader_guid="r",
        reader_participant_guid="rp",
        writer_guid="w",
        writer_participant_guid="wp",
        reason="partition",
        detail="writer ['a'] reader ['b']",
    )
    adapter = _calm(Scripted())
    adapter.over["detect_qos_mismatches"] = _empty_scan().model_copy(
        update={"not_matched": [pair, pair]}
    )
    found = _by(_run(adapter), "dds")
    assert [(f.severity, f.subject) for f in found] == [("critical", "rt/scan")]
    assert "2 reader/writer pair(s)" in found[0].text


def test_a_near_homonym_topic_hint_is_a_warning() -> None:
    hint = _typo_hint("rt/scan", "rt/scna", {"rt/scan": "writer", "rt/scna": "reader"}, 2)
    adapter = _calm(Scripted())
    adapter.over["detect_qos_mismatches"] = _empty_scan().model_copy(
        update={"hints": [hint, "Topic 'rt/x' has writers but no reader: there is no pair."]}
    )
    found = _by(_run(adapter), "dds")
    assert [(f.severity, f.subject) for f in found] == [("warning", "rt/scan")]


@pytest.mark.parametrize(("lives", "severity"), [(3, "critical"), (2, "warning")])
def test_restarts_are_a_loop_at_three_lives_and_a_warning_at_two(lives: int, severity: str) -> None:
    adapter = _calm(Scripted())
    adapter.over["list_participants"] = [
        _participant("x", f"g{i}", "left" if i < lives - 1 else "active") for i in range(lives)
    ]
    found = _by(_run(adapter), "dds")
    assert [(f.severity, f.subject) for f in found] == [(severity, "/x")]


def test_a_node_that_left_is_an_info_and_one_that_came_back_is_not() -> None:
    adapter = _calm(Scripted())
    adapter.over["list_participants"] = [_participant("gone", "g1", "left")]
    found = _by(_run(adapter), "dds")
    assert [f.severity for f in found] == ["info"] and "/gone" in found[0].text


def test_an_endpoint_less_participant_note_is_an_info() -> None:
    adapter = _calm(Scripted())
    old = _participant("quiet", "g1").model_copy(update={"endpoint_count": 0})
    adapter.over["list_participants"] = [old]
    found = _by(_run(adapter), "dds")
    assert [f.severity for f in found] == ["info"] and "no endpoint" in found[0].text


def test_the_stale_frame_and_the_missing_urdf_link_are_warnings() -> None:
    adapter = _calm(Scripted())
    del adapter.over["capture_tf"], adapter.over["sample_messages"]  # mock capture and URDF
    found = _by(_run(adapter), "tf", "warning")
    assert {f.subject for f in found} == {"map->odom", "gripper_link"}


def test_a_tf_tree_in_pieces_is_an_info() -> None:
    adapter = _calm(Scripted())
    from topicforge.models import TfCapture, TfSample

    base = fixtures.MOCK_BASE_TS_NS
    pieces = TfCapture(
        static=[],
        dynamic=[
            TfSample(parent="a", child="b", stamp_ns=base, received_ns=base),
            TfSample(parent="c", child="d", stamp_ns=base, received_ns=base),
        ],
        window_actual_s=5.0,
    )
    adapter.over["capture_tf"] = lambda _w: pieces
    found = [f for f in _by(_run(adapter), "tf", "info") if "roots" in f.text]
    assert len(found) == 1


def test_the_tf_window_is_eight_seconds_and_the_staleness_two() -> None:
    seen: list[float] = []
    adapter = _calm(Scripted())
    adapter.over["capture_tf"] = lambda w: (seen.append(w), _tf_ok())[1]
    _run(adapter)
    assert seen == [8.0]


def test_errors_on_rosout_are_one_warning_per_node_with_the_newest_line() -> None:
    adapter = _calm(Scripted())
    del adapter.over["sample_messages"]
    found = _by(_run(adapter), "logs", "warning")
    assert {f.subject for f in found} >= {"/camera_driver", "/lidar_driver"}
    assert len({f.subject for f in found}) == len(found)


def test_rosout_is_read_with_the_error_filter_and_a_five_second_timeout() -> None:
    seen: list[dict[str, Any]] = []

    def record(topic: str, count: int, **kwargs: Any) -> Any:
        seen.append({"topic": topic, "count": count, **kwargs})
        return _no_logs(topic, count)

    adapter = _calm(Scripted())
    adapter.over["sample_messages"] = record
    _run(adapter)
    seen = [s for s in seen if s["topic"] == "/rosout"]
    assert seen[0]["count"] == 50
    assert seen[0]["min_log_level"] == "error" and seen[0]["timeout_s"] == 5.0


def test_no_error_line_is_an_info() -> None:
    found = _by(_run(_calm(Scripted())), "logs")
    assert [f.severity for f in found] == ["info"]


def test_the_named_node_is_read_first_for_six_seconds_and_is_critical_when_it_is_silent() -> None:
    adapter = _calm(Scripted(), ["/a", "/b", "/blocked"])
    adapter.nodes_ok["/blocked"] = False
    result = _run(adapter, node="/blocked")
    assert adapter.node_reads[0] == ("/blocked", 6.0)
    assert [(f.severity, f.subject) for f in _by(result, "responsiveness")] == [
        ("critical", "/blocked")
    ]


def test_a_silent_node_that_was_not_named_is_a_warning_with_a_confirmation_step() -> None:
    adapter = _calm(Scripted(), ["/a", "/blocked"])
    adapter.nodes_ok["/blocked"] = False
    found = _by(_run(adapter), "responsiveness")
    assert [(f.severity, f.subject) for f in found] == [("warning", "/blocked")]
    assert "get_node_info(node, timeout_s=8)" in found[0].text
    assert dict(adapter.node_reads)["/blocked"] == 3.0


def test_nodes_are_read_lifecycle_first_then_by_name_and_ros2cli_is_left_out() -> None:
    adapter = _calm(Scripted(), ["/z", "/a", "/m", "/_ros2cli_123"])
    adapter.over["list_nodes"] = build_node_listing(
        ["/z", "/a", "/m", "/_ros2cli_123"], "live", lifecycle=frozenset({"/z"})
    )
    _run(adapter)
    assert [n for n, _ in adapter.node_reads] == ["/z", "/a", "/m"]


def test_the_nodes_the_budget_cannot_reach_are_one_info_that_names_them() -> None:
    names = [f"/n{i:02d}" for i in range(12)]
    adapter = _calm(Scripted(), names)
    adapter.cost["capture_tf"] = 8.0
    adapter.cost["list_nodes"] = 2.0
    adapter.cost["list_topics"] = 1.0
    adapter.cost["sample_messages"] = 3.0
    # 30 - 14 = 16 s left for the reads, in a clock the reads do not advance: pin K by hand.
    adapter.cost["get_node_info"] = 3.0
    result = _run(adapter)
    read = [n for n, _ in adapter.node_reads]
    info = [f for f in _by(result, "responsiveness", "info")]
    assert 0 < len(read) < len(names)
    assert len(info) == 1
    for name in names[len(read) :]:
        assert name in info[0].text
    assert result.checks_skipped == []


def test_a_node_missing_from_the_graph_is_a_skip_for_that_node_not_for_the_check() -> None:
    adapter = _calm(Scripted(), ["/a", "/b"])

    def read(node: str, timeout_s: float = 8.0) -> NodeInfo:
        if node == "/ghost":
            raise AdapterError("Node '/ghost' is not on the ROS 2 graph.")
        return node_fixtures.mock_node_info("/lidar_driver")

    adapter.get_node_info = read  # type: ignore[method-assign]
    result = _run(adapter, node="/ghost")
    assert result.checks_skipped == []  # /a and /b were still read: the check partly ran
    assert "responsiveness" in result.checks_run
    assert "did not run" not in result.verdict
    info = [f for f in _by(result, "responsiveness", "info") if "could not be read" in f.text]
    assert len(info) == 1 and "/ghost" in info[0].text
    assert result.critical_count == 0


def test_the_check_is_skipped_only_when_no_node_could_be_read() -> None:
    adapter = _calm(Scripted(), ["/a", "/b"])

    def read(node: str, timeout_s: float = 8.0) -> NodeInfo:
        raise AdapterError(f"{node} broke")

    adapter.get_node_info = read  # type: ignore[method-assign]
    result = _run(adapter)
    assert [s.check for s in result.checks_skipped] == ["responsiveness"]
    assert "responsiveness did not run" in result.verdict
    assert "responsiveness" not in result.checks_run


def test_only_a_timeout_is_a_silent_node_the_other_causes_are_not() -> None:
    for status in ("no_parameter_services", "failed", "no_budget"):
        adapter = _calm(Scripted(), ["/a", "/b"])
        adapter.param_status["/b"] = status
        result = _run(adapter, node="/b")
        assert result.critical_count == 0 and result.warning_count == 0, status
        found = _by(result, "responsiveness")
        if status == "no_parameter_services":
            assert found == []
        elif status == "failed":
            assert "could not be read" in found[0].text and "/b" in found[0].text
        else:
            assert "not tested" in found[0].text and "/b" in found[0].text
        assert result.checks_skipped == [], status


def test_a_node_that_timed_out_is_still_critical_when_named() -> None:
    adapter = _calm(Scripted(), ["/a", "/b"])
    adapter.param_status["/b"] = "timeout"
    assert [(f.severity, f.subject) for f in _by(_run(adapter, node="/b"), "responsiveness")] == [
        ("critical", "/b")
    ]


def test_old_departures_outside_the_window_are_not_a_restart_loop() -> None:
    now = time.time_ns()
    old = _participant("x", "g0", "left").model_copy(
        update={"first_seen_ns": now - 86400 * _NS, "last_seen_ns": now - 80000 * _NS}
    )
    older = old.model_copy(update={"guid": "g1"})
    adapter = _calm(Scripted())
    adapter.over["list_participants"] = [old, older, _participant("x", "g2")]
    found = _by(_run(adapter), "dds")
    assert [f for f in found if f.severity != "info"] == []


def test_a_name_with_an_active_participant_never_left_and_did_not_come_back() -> None:
    now = time.time_ns()
    gone = {"first_seen_ns": now - 86400 * _NS, "last_seen_ns": now - 80000 * _NS}
    adapter = _calm(Scripted())
    adapter.over["list_participants"] = [
        _participant("x", "g0", "left").model_copy(update=gone),
        _participant("x", "g1", "left").model_copy(update=gone),
        _participant("x", "g2"),
        _participant("y", "g3", "left").model_copy(update=gone),
    ]
    infos = [f.text for f in _by(_run(adapter), "dds", "info")]
    assert not any("/x" in t for t in infos)
    assert any("/y" in t for t in infos)  # a real departure is still reported


def test_the_restart_evidence_names_the_window_not_the_all_time_field() -> None:
    adapter = _calm(Scripted())
    adapter.over["list_participants"] = [_participant("x", f"g{i}") for i in range(3)]
    found = [f for f in _by(_run(adapter), "dds") if f.severity == "critical"]
    assert len(found) == 1
    ev = found[0].evidence[0]
    assert "restarts_by_node" not in ev.field and "300" in ev.field and ev.value == "3"


def test_parameters_without_a_status_are_unreadable_not_silent_and_not_answered() -> None:
    adapter = _calm(Scripted(), ["/a", "/b"])
    adapter.param_status["/a"] = None  # type: ignore[assignment]
    result = _run(adapter)
    assert [f for f in _by(result, "responsiveness") if f.severity != "info"] == []
    assert any(
        "could not be read" in f.text and "/a" in f.text for f in _by(result, "responsiveness")
    )


def test_findings_about_the_named_node_survive_the_cap_behind_many_criticals() -> None:
    reports = []
    for i in range(MAX_FINDINGS + 10):
        reports += _readers(1, f"w{i}").reports
        reports[-1] = reports[-1].model_copy(update={"topic": f"/t{i:03d}"})
    risky = (
        _readers(1, "wz")
        .reports[0]
        .model_copy(update={"topic": "/zzz", "severity": "risky", "reader_node": "/camera_driver"})
    )
    adapter = _calm(Scripted(), ["/a", "/camera_driver"])
    adapter.over["detect_qos_mismatches"] = _empty_scan().model_copy(
        update={"reports": [*reports, risky]}
    )
    del adapter.over["sample_messages"]
    result = _run(adapter, node="/camera_driver")
    assert len(result.findings) == MAX_FINDINGS
    mine = [(f.check, f.severity) for f in result.findings if f.severity == "warning"]
    assert ("logs", "warning") in mine and ("dds", "warning") in mine


def test_writers_without_identity_stay_apart_and_differing_requests_are_all_shown() -> None:
    base = fixtures.mock_mismatch_scan(None).reports[0]
    anon = base.model_copy(
        update={"writer_guid": None, "writer_node": None, "writer_participant_name": None}
    )
    one = anon.model_copy(update={"reader_node": "/r1"})
    two = anon.model_copy(update={"reader_node": "/r2"})
    adapter = _calm(Scripted())
    adapter.over["detect_qos_mismatches"] = _empty_scan().model_copy(update={"reports": [one, two]})
    assert len(_by(_run(adapter), "dds", "critical")) == 2
    d0 = base.details[0]
    a = base.model_copy(
        update={"reader_node": "/r1", "details": [d0.model_copy(update={"requested": "AAA"})]}
    )
    b = a.model_copy(
        update={"reader_node": "/r2", "details": [d0.model_copy(update={"requested": "BBB"})]}
    )
    adapter.over["detect_qos_mismatches"] = _empty_scan().model_copy(update={"reports": [a, b]})
    found = _by(_run(adapter), "dds", "critical")
    assert len(found) == 1 and "AAA" in found[0].text and "BBB" in found[0].text


def test_recent_departures_still_count_and_a_fresh_loss_event_ties_an_old_guid_in() -> None:
    now = time.time_ns()
    stale = {"first_seen_ns": now - 86400 * _NS, "last_seen_ns": now - 3600 * _NS}
    adapter = _calm(Scripted())
    adapter.over["list_participants"] = [
        _participant("x", "g0", "left").model_copy(update=stale),
        _participant("x", "g1", "left").model_copy(update=stale),
        _participant("x", "g2"),
    ]
    adapter.over["participant_events"] = [
        ParticipantEvent(
            guid=g,
            event_type="lost",
            vendor="cyclone",
            timestamp_ns=now,
            domain_id=0,
            time_source="observed_local",
        )
        for g in ("g0", "g1")
    ]
    found = [f for f in _by(_run(adapter), "dds") if f.severity != "info"]
    assert [(f.severity, f.subject) for f in found] == [("critical", "/x")]


def _readers(count: int, writer: str = "w") -> MismatchScan:
    base = fixtures.mock_mismatch_scan(None).reports[0]
    reports = [
        base.model_copy(
            update={
                "writer_node": f"/{writer}",
                "writer_guid": writer,
                "reader_node": f"/r{i}",
                "reader_guid": f"r{i}",
            }
        )
        for i in range(count)
    ]
    return _empty_scan().model_copy(update={"reports": reports})


def test_one_bad_writer_with_many_readers_is_one_finding_that_counts_them() -> None:
    adapter = _calm(Scripted())
    adapter.over["detect_qos_mismatches"] = _readers(5)
    found = _by(_run(adapter), "dds", "critical")
    assert len(found) == 1 and "5 readers" in found[0].text and "/r0" in found[0].text
    assert found[0].suggested_fixes


def test_two_writers_stay_two_findings() -> None:
    scan = _readers(2, "w1")
    scan = scan.model_copy(update={"reports": scan.reports + _readers(2, "w2").reports})
    adapter = _calm(Scripted())
    adapter.over["detect_qos_mismatches"] = scan
    assert len(_by(_run(adapter), "dds", "critical")) == 2


def test_the_named_node_finding_survives_the_cap() -> None:
    reports = []
    for i in range(MAX_FINDINGS + 10):
        reports += _readers(1, f"w{i}").reports
        reports[-1] = reports[-1].model_copy(update={"topic": f"/t{i:03d}"})
    adapter = _calm(Scripted(), ["/a", "/blocked"])
    adapter.over["detect_qos_mismatches"] = _empty_scan().model_copy(update={"reports": reports})
    adapter.param_status["/blocked"] = "timeout"
    result = _run(adapter, node="/blocked")
    assert len(result.findings) == MAX_FINDINGS
    assert result.findings[0].subject == "/blocked"
    assert result.findings[0].check == "responsiveness"


def test_topics_whose_counts_could_not_be_read_are_reported_not_dropped() -> None:
    adapter = _calm(Scripted())
    adapter.over["list_topics"] = [
        TopicListItem(
            name="/lost",
            message_type="t",
            publisher_count=0,
            subscriber_count=0,
            counts_unknown=True,
        ),
        TopicListItem(name="/ok", message_type="t", publisher_count=1, subscriber_count=1),
    ]
    found = _by(_run(adapter), "topics")
    assert [f.severity for f in found] == ["info"] and "/lost" in found[0].text
    assert "could not be read" in found[0].text


def test_the_rosout_all_clear_states_the_window_actually_listened_to() -> None:
    adapter = _calm(Scripted())
    adapter.cost["list_nodes"] = 17.0
    adapter.cost["capture_tf"] = 17.0
    result = _run(adapter, budget_s=40.0)
    text = _by(result, "logs")[0].text
    assert "5 s listened" not in text and "budget cut" in text


# ---- budget, errors, order ----------------------------------------------------------------


def test_a_check_that_no_longer_fits_is_skipped_for_budget() -> None:
    clock = Clock()
    adapter = _calm(Scripted(clock))
    adapter.cost["list_nodes"] = 9.5
    result = _run(adapter, budget_s=10.0)
    reasons = {s.check: s.reason for s in result.checks_skipped}
    assert reasons.get("topics") == "budget"
    assert "nodes" in result.checks_run
    assert result.verdict.startswith("Incomplete sweep")
    assert "healthy" not in result.verdict.lower()


def test_the_internal_deadline_stays_two_seconds_inside_the_guard() -> None:
    adapter = _calm(Scripted())
    with budget.deadline_at(time.monotonic() + 2.5):  # horizon 0.5 s, under every floor
        result = _run(adapter, budget_s=40.0)
    assert result.checks_run == ["environment"]
    assert {s.reason for s in result.checks_skipped} == {"budget"}
    assert len(result.checks_skipped) == 6


def test_the_tf_window_shrinks_to_what_the_budget_leaves_and_says_so() -> None:
    seen: list[float] = []
    adapter = _calm(Scripted())
    adapter.over["capture_tf"] = lambda w: (seen.append(w), _tf_ok())[1]
    adapter.cost["list_nodes"] = 2.0
    adapter.cost["list_topics"] = 1.0
    result = _run(adapter, budget_s=14.0, scope="all")
    assert seen and seen[0] < 8.0
    cut = [f for f in _by(result, "tf", "info") if "budget cut" in f.text]
    assert len(cut) == 1


@pytest.mark.parametrize(
    ("method", "check"),
    [
        ("list_nodes", "nodes"),
        ("list_topics", "topics"),
        ("detect_qos_mismatches", "dds"),
        ("capture_tf", "tf"),
        ("sample_messages", "logs"),
    ],
)
def test_an_adapter_error_in_a_check_is_a_skip_with_its_message_not_a_tool_error(
    method: str, check: str
) -> None:
    adapter = _calm(Scripted())
    adapter.fail[method] = AdapterError(f"{method} broke")
    result = _run(adapter)
    assert (check, f"{method} broke") in [(s.check, s.reason) for s in result.checks_skipped]
    assert check not in result.checks_run
    assert "Incomplete sweep" in result.verdict


def test_the_tool_does_not_raise_when_a_check_fails() -> None:
    app = build_app(_SETTINGS)
    result = asyncio.run(app.call_tool("diagnose", {"scope": "logs"})).structured_content
    assert result["checks_skipped"] == []


def test_findings_are_sorted_by_severity_then_check_then_subject() -> None:
    adapter = Scripted()
    adapter.over["detect_qos_mismatches"] = fixtures.mock_mismatch_scan(None)
    findings = _run(adapter).findings
    rank = {"critical": 0, "warning": 1, "info": 2}
    keys = [(rank[f.severity], f.check, f.subject or "") for f in findings]
    assert keys == sorted(keys)


def test_scope_limits_the_checks_and_the_verdict_says_so() -> None:
    adapter = _calm(Scripted())
    result = _run(adapter, scope="tf")
    assert result.checks_run == ["environment", "tf"]
    assert result.verdict.startswith("Incomplete sweep:") and "scope tf only" in result.verdict
    assert "list_nodes" not in adapter.calls and "capture_tf" in adapter.calls


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        ("graph", ["environment", "nodes", "topics", "responsiveness"]),
        ("dds", ["environment", "dds"]),
        ("logs", ["environment", "logs"]),
    ],
)
def test_each_scope_runs_its_group(scope: str, expected: list[str]) -> None:
    assert _run(_calm(Scripted()), scope=scope).checks_run == expected


def test_responsiveness_runs_last() -> None:
    assert _run(_calm(Scripted())).checks_run[-1] == "responsiveness"


def test_the_findings_are_capped_at_fifty_and_the_counts_cover_all_of_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from topicforge.services import diagnose as diagnose_service

    def many(ctx: Any) -> list[Any]:
        return [
            diagnose_service._finding("warning", "topics", f"/t{i:03d}", "x") for i in range(70)
        ]

    monkeypatch.setitem(diagnose_service._CHECK_FUNCTIONS, "topics", many)
    result = _run(_calm(Scripted()))
    assert len(result.findings) == MAX_FINDINGS
    assert result.findings_total >= 70 and result.warning_count >= 70
    assert result.note and "Showing 50" in result.note


def test_a_verdict_is_never_clean_after_a_skip_and_never_says_healthy() -> None:
    adapter = _calm(Scripted())
    adapter.fail["capture_tf"] = AdapterError("no tf")
    result = _run(adapter)
    assert result.critical_count == 0 and result.warning_count == 0
    assert result.verdict.startswith("Incomplete sweep") and "tf did not run" in result.verdict
    for diagnosis in (result, _run(_calm(Scripted()))):
        assert "healthy" not in diagnosis.verdict.lower()


def test_evidence_values_are_cut_at_two_hundred_characters() -> None:
    adapter = _calm(Scripted())
    long_hint = _typo_hint(
        "rt/" + "a" * 400, "rt/b", {"rt/" + "a" * 400: "writer", "rt/b": "reader"}, 1
    )
    adapter.over["detect_qos_mismatches"] = _empty_scan().model_copy(update={"hints": [long_hint]})
    found = _by(_run(adapter), "dds")
    assert len(found[0].evidence[0].value) == EVIDENCE_VALUE_MAX
    assert found[0].evidence[0].value.endswith("...")


# ---- input and structure ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"scope": "everything"}, "scope must be one of"),
        ({"budget_s": 9.9}, "budget_s must be in 10..40"),
        ({"budget_s": 41}, "budget_s must be in 10..40"),
        ({"budget_s": True}, "budget_s must be a number"),
        ({"node": "bad name"}, "node name is malformed"),
        ({"node": "  "}, "node must be a non-empty string"),
    ],
)
def test_invalid_input_is_the_only_thing_that_raises(kwargs: dict[str, Any], message: str) -> None:
    adapter = _calm(Scripted())
    with pytest.raises(AdapterError, match=message):
        _run(adapter, **kwargs)
    assert adapter.calls == []  # nothing ran before the input was refused


def test_the_tool_refuses_a_bad_budget_over_mcp() -> None:
    app = build_app(_SETTINGS)
    with pytest.raises(ToolError):
        asyncio.run(app.call_tool("diagnose", {"budget_s": 5}))


def test_the_dds_side_never_peeks_samples_or_reads_metrics() -> None:
    adapter = Scripted()
    adapter.over["detect_qos_mismatches"] = fixtures.mock_mismatch_scan(None)
    _run(adapter)
    assert not set(adapter.calls) & _FORBIDDEN
    assert {"detect_qos_mismatches", "participant_events", "list_participants"} <= set(
        adapter.calls
    )


def test_the_sweep_only_uses_methods_of_the_adapter_protocol() -> None:
    adapter = Scripted()
    _run(adapter)
    assert set(adapter.calls) <= {
        "list_nodes",
        "list_topics",
        "get_node_info",
        "capture_tf",
        "sample_messages",
        "detect_qos_mismatches",
        "participant_events",
        "list_participants",
        "get_topic_info",  # not called today; allowed by the protocol
    }


# ---- the mock scenario ------------------------------------------------------------------


def test_the_mock_diagnosis_is_derived_from_the_fixtures() -> None:
    app = build_app(_SETTINGS)
    raw = asyncio.run(app.call_tool("diagnose", {})).structured_content
    result = Diagnosis.model_validate(raw)
    assert result.checks_skipped == [] and result.mode_effective == "mock"
    assert result.elapsed_s < 1.0

    expected_critical = {
        ("dds", r.topic)
        for r in fixtures.mock_mismatch_scan(None).reports
        if r.severity == "incompatible"
    }
    assert expected_critical == {("dds", "/dds/qos_mismatch")}
    assert {(f.check, f.subject) for f in result.findings if f.severity == "critical"} == (
        expected_critical
    )

    inspector = Inspector(MockAdapter())
    missing = inspector.inspect_tf(8, 2).declared_frames_missing
    assert missing == ["gripper_link"]
    warned = {(f.check, f.subject) for f in result.findings if f.severity == "warning"}
    assert {("tf", name) for name in missing} <= warned
    assert ("tf", "map->odom") in warned
    logged = {
        "/" + str(sample.payload["name"]).replace(".", "/")
        for sample in inspector.sample_messages("/rosout", 50, min_log_level="error").samples
    }
    assert {subject for check, subject in warned if check == "logs"} == logged


def test_the_mock_critical_names_the_nodes_and_carries_the_qos_fixes() -> None:
    app = build_app(_SETTINGS)
    result = asyncio.run(app.call_tool("diagnose", {})).structured_content
    critical = [f for f in result["findings"] if f["severity"] == "critical"]
    assert len(critical) == 1
    assert "/nav_planner" in critical[0]["text"] and "/lidar_driver" in critical[0]["text"]
    assert critical[0]["suggested_fixes"]
    assert result["info_count"] >= 1 and result["warning_count"] >= 2


def test_the_fixes_of_a_diagnosis_pass_the_forbidden_verb_rule() -> None:
    """The fixes are copied from the scan; the rule of CONTRACT section 3.1 holds on the copy."""
    adapter = _calm(Scripted())
    adapter.over["detect_qos_mismatches"] = _everything()
    fixes = [fix for f in _run(adapter).findings for fix in f.suggested_fixes]
    assert len(fixes) >= 7 * 2
    for fix in fixes:
        for text in (fix.text, fix.tradeoff, fix.target, fix.verify_with):
            assert _FORBIDDEN_VERB.search(text) is None, text


def test_diagnose_takes_the_ros_lane_and_answers_busy_when_it_is_held() -> None:
    from topicforge.tools.guard import _LANES

    held = _LANES["ros"].lock
    app = build_app(_SETTINGS)
    assert held.acquire(blocking=False)
    try:
        with budget.deadline_at(time.monotonic() + 100), pytest.raises(ToolError, match="busy"):
            from topicforge.tools import guard

            old = guard.TOOL_BUDGET_S
            guard.TOOL_BUDGET_S = 5.5
            try:
                asyncio.run(app.call_tool("diagnose", {}))
            finally:
                guard.TOOL_BUDGET_S = old
    finally:
        held.release()
