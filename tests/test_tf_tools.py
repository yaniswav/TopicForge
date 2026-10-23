"""`inspect_tf`: the echo parser, the live capture (stubbed stream), mock, service and MCP layer.

No ROS 2: the live tests replace `stream_echo`. The Docker bench runs the same tool
against a real graph (`tests/integration/ros2/`).
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from topicforge.adapters.base import AdapterError, MiddlewareAdapter
from topicforge.adapters.composite import CompositeAdapter
from topicforge.adapters.ros2_live import tf_capture
from topicforge.adapters.ros2_live.adapter import Ros2CliAdapter
from topicforge.adapters.ros2_live.echo_stream import EchoDocument, EchoRun
from topicforge.adapters.ros2_live.tf_parsers import parse_tf_message
from topicforge.adapters.ros2_mock import MockAdapter
from topicforge.config import Settings
from topicforge.models import TopicInfo
from topicforge.server import build_app
from topicforge.services.inspector import Inspector

NS = 1_000_000_000


def _tf_doc(*edges: tuple[str, str, int]) -> str:
    """One echoed `TFMessage`: edges are (parent, child, stamp seconds)."""
    lines = ["transforms:"]
    for parent, child, sec in edges:
        lines += [
            "- header:",
            "    stamp:",
            f"      sec: {sec}",
            "      nanosec: 500000000",
            f"    frame_id: {parent}",
            f"  child_frame_id: {child}",
            "  transform:",
            "    translation:",
            "      x: 1.0",
            "      y: 0.0",
            "      z: 0.0",
            "    rotation:",
            "      x: 0.0",
            "      y: 0.0",
            "      z: 0.0",
            "      w: 1.0",
        ]
    return "\n".join(lines) + "\n"


# ----- parser -----


def test_parse_tf_message_reads_every_transform() -> None:
    samples = parse_tf_message(_tf_doc(("odom", "base_link", 7), ("/base_link", "laser", 8)), 99)
    assert samples is not None and len(samples) == 2
    assert (samples[0].parent, samples[0].child) == ("odom", "base_link")
    assert samples[0].stamp_ns == 7 * NS + 500_000_000 and samples[0].received_ns == 99
    assert samples[1].parent == "base_link"  # the leading slash is dropped


def test_parse_tf_message_rejects_what_is_not_a_tf_message() -> None:
    assert parse_tf_message("not: [valid", 1) is None
    assert parse_tf_message("data: 3\n", 1) is None
    assert parse_tf_message("transforms: []\n", 1) == []


def test_parse_tf_message_skips_a_transform_without_frames_and_defaults_the_stamp() -> None:
    doc = "transforms:\n- child_frame_id: a\n- header:\n    frame_id: p\n  child_frame_id: c\n"
    samples = parse_tf_message(doc, 5)
    assert samples is not None and [(s.parent, s.child, s.stamp_ns) for s in samples] == [
        ("p", "c", 0)
    ]


# ----- live capture -----


def _info(topic: str, publishers: int) -> TopicInfo:
    return TopicInfo(
        name=topic,
        message_type="tf2_msgs/msg/TFMessage",
        publisher_count=publishers,
        subscriber_count=1,
        publisher_qos=None,
        subscription_qos=None,
        publisher_nodes=[],
        subscriber_nodes=[],
        mode_effective="live",
    )


class FakeEcho:
    """A scripted `stream_echo`: answers by topic and records every call."""

    def __init__(self, runs: dict[str, EchoRun | Exception]) -> None:
        self.runs = runs
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, cmd: list[str], **kw: Any) -> EchoRun:
        self.calls.append((cmd, kw))
        found = self.runs[cmd[-1]]
        if isinstance(found, Exception):
            raise found
        return found

    def call(self, topic: str) -> tuple[list[str], dict[str, Any]]:
        """The recorded call for `topic` (the two echoes run concurrently, in any order)."""
        return next(c for c in self.calls if c[0][-1] == topic)


def _run(*docs: str, oversized: int = 0, exit_code: int | None = None) -> EchoRun:
    return EchoRun(
        documents=[EchoDocument(d, (i + 1) * NS) for i, d in enumerate(docs)],
        oversized=oversized,
        exit_code=exit_code,
        started_ns=0,
        ended_ns=4 * NS,
    )


def _capture(
    monkeypatch: pytest.MonkeyPatch,
    echo: FakeEcho,
    infos: dict[str, TopicInfo | AdapterError],
    window_s: float = 5.0,
):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(tf_capture, "stream_echo", echo)

    def topic_info(topic: str, _timeout: float) -> TopicInfo:
        found = infos[topic]
        if isinstance(found, AdapterError):
            raise found
        return found

    return tf_capture.capture_tf(
        window_s,
        exe="ros2",
        topic_info=topic_info,
        qos_args=lambda _info: ["--qos-reliability", "reliable", "--qos-durability", "volatile"],
        max_message_chars=1000,
    )


def test_capture_reads_the_latched_topic_then_listens_to_tf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    echo = FakeEcho(
        {
            "/tf_static": _run(_tf_doc(("base_link", "laser", 0)), _tf_doc(("a", "b", 0))),
            "/tf": _run(_tf_doc(("odom", "base_link", 1)), _tf_doc(("odom", "base_link", 2))),
        }
    )
    cap = _capture(
        monkeypatch, echo, {"/tf_static": _info("/tf_static", 2), "/tf": _info("/tf", 1)}
    )
    assert len(cap.static) == 2 and len(cap.dynamic) == 2
    assert cap.window_actual_s == 4.0 and cap.notes == [] and not cap.capped
    static_cmd, static_kw = echo.call("/tf_static")
    assert static_cmd[-1] == "/tf_static" and static_kw["count"] == 2
    assert "transient_local" in static_cmd and "--full-length" in static_cmd
    assert static_kw["deadline_s"] == tf_capture._STATIC_MIN_WAIT_S
    tf_cmd, tf_kw = echo.call("/tf")
    assert tf_kw["count"] == tf_capture.MAX_TF_DOCS and tf_kw["deadline_s"] == 5.0
    assert "--full-length" in tf_cmd and "volatile" in tf_cmd


def test_capture_counts_what_it_could_not_decode_and_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docs = ["junk: [", _tf_doc(("odom", "base_link", 1))]
    echo = FakeEcho({"/tf_static": _run(), "/tf": _run(*docs, oversized=2)})
    cap = _capture(
        monkeypatch, echo, {"/tf_static": _info("/tf_static", 0), "/tf": _info("/tf", 1)}
    )
    assert len(cap.dynamic) == 1 and cap.dropped_count == 3
    monkeypatch.setattr(tf_capture, "MAX_TF_DOCS", 2)
    capped = _capture(
        monkeypatch, echo, {"/tf_static": _info("/tf_static", 0), "/tf": _info("/tf", 1)}
    )
    assert capped.capped


def test_missing_streams_become_notes_not_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    echo = FakeEcho({"/tf": _run()})
    cap = _capture(
        monkeypatch,
        echo,
        {"/tf_static": AdapterError("Topic not found"), "/tf": _info("/tf", 0)},
    )
    assert cap.static == [] and cap.dynamic == []
    assert any("/tf_static could not be read" in n for n in cap.notes)
    assert any("/tf has no publisher" in n for n in cap.notes)
    # No publisher on /tf: listens a short while only.
    assert echo.call("/tf")[1]["deadline_s"] <= 2.0


def test_a_static_publisher_that_stays_silent_is_noted(monkeypatch: pytest.MonkeyPatch) -> None:
    echo = FakeEcho({"/tf_static": _run(_tf_doc(("a", "b", 0))), "/tf": _run()})
    cap = _capture(
        monkeypatch, echo, {"/tf_static": _info("/tf_static", 3), "/tf": _info("/tf", 1)}
    )
    assert any("1 of 3 /tf_static publishers" in n for n in cap.notes)


def test_a_cli_that_exits_early_is_noted(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = EchoRun(exit_code=1, stderr_tail="rmw failed", started_ns=0, ended_ns=NS)
    echo = FakeEcho({"/tf_static": _run(), "/tf": bad})
    cap = _capture(
        monkeypatch, echo, {"/tf_static": _info("/tf_static", 0), "/tf": _info("/tf", 1)}
    )
    assert any("exited early" in n and "rmw failed" in n for n in cap.notes)


def test_adapter_capture_tf_wires_the_pieces(monkeypatch: pytest.MonkeyPatch) -> None:
    echo = FakeEcho(
        {"/tf_static": _run(_tf_doc(("a", "b", 0))), "/tf": _run(_tf_doc(("odom", "a", 1)))}
    )
    monkeypatch.setattr(tf_capture, "stream_echo", echo)
    monkeypatch.setattr(
        "topicforge.adapters.ros2_live.adapter.shutil.which", lambda _n: "/bin/ros2"
    )
    adapter = Ros2CliAdapter()
    monkeypatch.setattr(adapter, "_topic_info", lambda t, _timeout: _info(t, 1))
    cap = adapter.capture_tf(3.0)
    assert len(cap.static) == 1 and len(cap.dynamic) == 1
    assert echo.call("/tf")[0][0] == "/bin/ros2"


def test_adapter_capture_tf_needs_the_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("topicforge.adapters.ros2_live.adapter.shutil.which", lambda _n: None)
    with pytest.raises(AdapterError, match="not found on PATH"):
        Ros2CliAdapter().capture_tf(3.0)


# ----- protocol, composite, mock -----


def test_every_adapter_has_capture_tf() -> None:
    assert isinstance(Ros2CliAdapter(), MiddlewareAdapter)
    assert isinstance(MockAdapter(), MiddlewareAdapter)


def test_composite_routes_capture_tf_to_the_ros_half() -> None:
    class Half:
        name = "x"
        effective_mode = "live"

        def __init__(self) -> None:
            self.calls: list[float] = []

        def capture_tf(self, window_s: float) -> str:
            self.calls.append(window_s)
            return "capture"

    ros, dds = Half(), Half()
    assert CompositeAdapter(ros, dds).capture_tf(7.0) == "capture"  # type: ignore[arg-type]
    assert ros.calls == [7.0] and dds.calls == []


def test_mock_tf_topics_match_the_fixed_frames() -> None:
    mock = MockAdapter()
    info = mock.get_topic_info("/tf_static")
    assert info.publisher_qos is not None and info.publisher_qos.durability == "transient_local"
    sample = mock.sample_messages("/tf_static", 1).samples[0]
    edges = {(t["header"]["frame_id"], t["child_frame_id"]) for t in sample.payload["transforms"]}
    assert edges == {(s.parent, s.child) for s in mock.capture_tf(5.0).static}
    assert ("base_link", "laser") not in {
        (t["header"]["frame_id"], t["child_frame_id"])
        for s in mock.sample_messages("/tf", 2).samples
        for t in s.payload["transforms"]
    }


def test_mock_report_has_one_stale_and_one_declared_not_published_finding() -> None:
    report = Inspector(MockAdapter()).inspect_tf()
    assert [f.kind for f in report.findings] == ["stale", "declared_not_published"]
    assert report.findings[0].frames == ["map", "odom"]
    assert report.roots == ["map"] and report.stale_count == 1
    assert report.mode_effective == "mock" and report.window_s == 5.0
    by_name = {f.name: f for f in report.frames}
    assert by_name["base_link"].observed_frequency_hz == 50.0
    assert by_name["camera_optical_frame"].parent == "camera_link"
    assert report.real_time_factor == 1.0 and report.hints[0].startswith("A stale frame")


def test_mock_path_between_two_frames() -> None:
    report = Inspector(MockAdapter()).inspect_tf(
        source_frame="camera_optical_frame", target_frame="laser"
    )
    assert report.path == ["camera_optical_frame", "camera_link", "base_link", "laser"]


# ----- service validation -----


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"window_s": 0.5}, "window_s must be in 1..20"),
        ({"window_s": 30}, "window_s must be in 1..20"),
        ({"stale_after_s": 0.0}, "stale_after_s must be in"),
        ({"window_s": True}, "window_s must be a number"),
        ({"source_frame": "a"}, "give both or neither"),
        ({"target_frame": "a"}, "give both or neither"),
        ({"source_frame": "a b", "target_frame": "c"}, "source_frame must be a frame name"),
        ({"source_frame": "a", "target_frame": " / "}, "target_frame must be a frame name"),
    ],
)
def test_inspect_tf_validates_its_inputs(kwargs: dict[str, Any], message: str) -> None:
    with pytest.raises(AdapterError, match=message):
        Inspector(MockAdapter()).inspect_tf(**kwargs)


def test_frame_names_are_cleaned() -> None:
    report = Inspector(MockAdapter()).inspect_tf(source_frame=" /laser ", target_frame="/odom")
    assert report.path == ["laser", "base_link", "odom"]


def test_the_dds_only_message_names_inspect_tf() -> None:
    from topicforge.adapters.common import DDS_ONLY_ERROR_MSG

    assert "inspect_tf" in DDS_ONLY_ERROR_MSG


# ----- MCP layer -----


def _app() -> Any:
    return build_app(
        Settings(mode="mock", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False)
    )


def test_inspect_tf_over_mcp() -> None:
    result = asyncio.run(_app().call_tool("inspect_tf", {"window_s": 2, "stale_after_s": 3.5}))
    data = result.structured_content
    assert data["window_s"] == 2.0 and data["stale_count"] == 1
    assert data["mode_effective"] == "mock"
    # 4.0 s old stays stale at 3.5 s; a larger limit clears it.
    clear = asyncio.run(_app().call_tool("inspect_tf", {"stale_after_s": 10}))
    assert [f["kind"] for f in clear.structured_content["findings"]] == ["declared_not_published"]


def test_inspect_tf_errors_reach_the_client() -> None:
    with pytest.raises(ToolError):
        asyncio.run(_app().call_tool("inspect_tf", {"source_frame": "laser"}))
    with pytest.raises(ToolError):
        asyncio.run(_app().call_tool("inspect_tf", {"window_s": 99}))


def test_both_streams_failing_for_a_real_reason_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    echo = FakeEcho({})
    with pytest.raises(AdapterError, match="Could not read the TF topics"):
        _capture(
            monkeypatch,
            echo,
            {"/tf_static": AdapterError("ros2 timed out"), "/tf": AdapterError("daemon down")},
        )


def test_both_topics_absent_is_a_note_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _capture(
        monkeypatch,
        FakeEcho({}),
        {"/tf_static": AdapterError("Topic not found"), "/tf": AdapterError("Topic not found")},
    )
    assert cap.static == [] and cap.dynamic == []
    assert len(cap.notes) == 2


def test_one_failed_stream_stays_a_note(monkeypatch: pytest.MonkeyPatch) -> None:
    echo = FakeEcho({"/tf": _run(_tf_doc(("odom", "base_link", 1)))})
    cap = _capture(
        monkeypatch, echo, {"/tf_static": AdapterError("ros2 timed out"), "/tf": _info("/tf", 1)}
    )
    assert len(cap.dynamic) == 1 and any("timed out" in n for n in cap.notes)


def test_a_shortened_window_is_noted(monkeypatch: pytest.MonkeyPatch) -> None:
    echo = FakeEcho({"/tf_static": _run(), "/tf": _run(_tf_doc(("odom", "base_link", 1)))})
    real = tf_capture.call_budget.clamp
    monkeypatch.setattr(
        tf_capture.call_budget,
        "clamp",
        lambda t, r=0.0: min(real(t, r), 2.0) if t > 4 and t != 8.0 else real(t, r),
    )
    cap = _capture(
        monkeypatch, echo, {"/tf_static": _info("/tf_static", 0), "/tf": _info("/tf", 1)}
    )
    assert any("shortened the /tf window" in n for n in cap.notes)


def test_static_and_dynamic_echoes_run_concurrently(monkeypatch: pytest.MonkeyPatch) -> None:
    dynamic_started = threading.Event()
    static_saw_dynamic: list[bool] = []

    def echo(cmd: list[str], **_kw: Any) -> EchoRun:
        if cmd[-1] == "/tf":
            dynamic_started.set()
            return _run(_tf_doc(("odom", "base_link", 1)))
        # The static run only completes once the dynamic one has started.
        static_saw_dynamic.append(dynamic_started.wait(timeout=5.0))
        return _run(_tf_doc(("a", "b", 0)))

    cap = _capture(
        monkeypatch,
        echo,  # type: ignore[arg-type]
        {"/tf_static": _info("/tf_static", 1), "/tf": _info("/tf", 1)},
    )
    assert static_saw_dynamic == [True]
    assert len(cap.static) == 1 and len(cap.dynamic) == 1 and cap.notes == []


def test_static_deadline_follows_the_window_when_longer(monkeypatch: pytest.MonkeyPatch) -> None:
    echo = FakeEcho({"/tf_static": _run(), "/tf": _run()})
    _capture(
        monkeypatch,
        echo,
        {"/tf_static": _info("/tf_static", 1), "/tf": _info("/tf", 1)},
        window_s=15.0,
    )
    assert echo.call("/tf_static")[1]["deadline_s"] == 15.0
    assert echo.call("/tf")[1]["deadline_s"] == 15.0


def test_static_deadline_is_clamped_by_the_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    echo = FakeEcho({"/tf_static": _run(), "/tf": _run()})
    with tf_capture.call_budget.deadline_at(tf_capture.time.monotonic() + 8.0):
        _capture(
            monkeypatch,
            echo,
            {"/tf_static": _info("/tf_static", 1), "/tf": _info("/tf", 1)},
            window_s=20.0,
        )
    static_wait = echo.call("/tf_static")[1]["deadline_s"]
    assert 0 < static_wait <= 8.0 - tf_capture._STOP_RESERVE_S


def test_worker_sees_the_callers_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[float | None] = []

    def echo(cmd: list[str], **_kw: Any) -> EchoRun:
        if cmd[-1] == "/tf_static":
            seen.append(tf_capture.call_budget.remaining())
        return _run()

    with tf_capture.call_budget.deadline_at(tf_capture.time.monotonic() + 30.0):
        _capture(
            monkeypatch,
            echo,  # type: ignore[arg-type]
            {"/tf_static": _info("/tf_static", 1), "/tf": _info("/tf", 1)},
        )
    assert seen and seen[0] is not None and seen[0] > 0


def test_a_static_worker_exception_becomes_a_note(monkeypatch: pytest.MonkeyPatch) -> None:
    echo = FakeEcho(
        {
            "/tf_static": AdapterError("could not start `ros2`: boom"),
            "/tf": _run(_tf_doc(("odom", "base_link", 1))),
        }
    )
    cap = _capture(
        monkeypatch, echo, {"/tf_static": _info("/tf_static", 1), "/tf": _info("/tf", 1)}
    )
    assert len(cap.dynamic) == 1 and cap.static == []
    assert any("/tf_static could not be read" in n and "boom" in n for n in cap.notes)


def test_static_worker_failure_plus_dynamic_failure_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    echo = FakeEcho({"/tf_static": RuntimeError("worker died")})
    with pytest.raises(AdapterError, match="worker died"):
        _capture(
            monkeypatch,
            echo,
            {"/tf_static": _info("/tf_static", 1), "/tf": AdapterError("daemon down")},
        )


def test_a_dynamic_crash_still_joins_the_static_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    finished = threading.Event()

    def echo(cmd: list[str], **_kw: Any) -> EchoRun:
        if cmd[-1] == "/tf":
            raise AdapterError("could not start `ros2`: nope")
        finished.set()
        return _run()

    with pytest.raises(AdapterError, match="nope"):
        _capture(
            monkeypatch,
            echo,  # type: ignore[arg-type]
            {"/tf_static": _info("/tf_static", 1), "/tf": _info("/tf", 1)},
        )
    assert finished.is_set()


def test_parse_grace_drops_the_oldest_documents_first(monkeypatch: pytest.MonkeyPatch) -> None:
    docs = [_tf_doc(("odom", "base_link", i)) for i in range(1, 6)]
    run = _run(*docs)
    ticks = iter([0.0, 0.0, 99.0, 99.0, 99.0, 99.0])
    monkeypatch.setattr(tf_capture.time, "monotonic", lambda: next(ticks))
    samples, dropped = tf_capture._decode(run, parse_until=1.0)
    assert dropped == 3 and len(samples) == 2
    assert [s.received_ns for s in samples] == sorted(s.received_ns for s in samples)
    assert samples[-1].received_ns == 5 * NS


# ----- declared against published (/robot_description) -----


def test_mock_report_compares_the_urdf_with_the_frames() -> None:
    report = Inspector(MockAdapter()).inspect_tf()
    assert report.urdf_source == "robot_description" and report.urdf_link_count == 6
    assert report.declared_frames_missing == ["gripper_link"]
    assert report.frames_not_in_urdf == ["map", "odom"]
    assert report.urdf_note is None
    finding = report.findings[-1]
    assert finding.kind == "declared_not_published" and finding.frames == ["gripper_link"]
    assert "/joint_states" in finding.text  # gripper_link hangs from a revolute joint


def test_the_mock_urdf_is_served_on_a_latched_topic() -> None:
    mock = MockAdapter()
    info = mock.get_topic_info("/robot_description")
    assert info.message_type == "std_msgs/msg/String"
    assert info.publisher_qos is not None and info.publisher_qos.durability == "transient_local"
    assert info.publisher_nodes == ["/robot_state_publisher"]
    sample = mock.sample_messages("/robot_description", 1, max_array_length=None).samples[0]
    assert sample.payload["data"].startswith("<?xml") and "gripper_link" in sample.payload["data"]


class _UrdfAdapter(MockAdapter):
    """The mock robot with a scripted answer for `/robot_description`."""

    def __init__(self, answer: Any) -> None:
        self._answer = answer
        self.sample_calls: list[tuple[Any, ...]] = []

    def sample_messages(self, topic: str, count: int, **kwargs: Any) -> Any:
        if topic != "/robot_description":
            return super().sample_messages(topic, count, **kwargs)
        self.sample_calls.append((topic, count, kwargs))
        if isinstance(self._answer, Exception):
            raise self._answer
        return self._answer


def _string_result(text: str | None, note: str | None = None) -> Any:
    from topicforge.models import MessageSample, SampleResult

    samples = (
        []
        if text is None
        else [
            MessageSample(
                topic="/robot_description",
                message_type="std_msgs/msg/String",
                timestamp_ns=0,
                stamp_source="none",
                payload={"data": text},
            )
        ]
    )
    return SampleResult(
        topic="/robot_description",
        count=len(samples),
        samples=samples,
        mode_effective="mock",
        note=note,
    )


def test_a_missing_robot_description_topic_is_a_note_not_an_error() -> None:
    adapter = _UrdfAdapter(AdapterError("Topic not found or empty info: '/robot_description'"))
    report = Inspector(adapter).inspect_tf()
    assert report.urdf_source == "none" and report.urdf_link_count is None
    assert "robot_state_publisher" in (report.urdf_note or "")
    assert report.declared_frames_missing == [] and report.frames_not_in_urdf == []
    assert [f.kind for f in report.findings] == ["stale"]


def test_the_urdf_is_read_once_uncut_and_within_six_seconds() -> None:
    adapter = _UrdfAdapter(_string_result("<robot><link name='base_link'/></robot>"))
    Inspector(adapter).inspect_tf()
    ((_, count, kwargs),) = adapter.sample_calls
    assert count == 1 and kwargs["max_array_length"] is None and kwargs["timeout_s"] <= 6.0


def test_a_silent_publisher_keeps_the_adapter_note() -> None:
    adapter = _UrdfAdapter(_string_result(None, "0 of 1 messages within 6 s"))
    report = Inspector(adapter).inspect_tf()
    assert report.urdf_source == "none" and "0 of 1 messages" in (report.urdf_note or "")


def test_a_robot_description_with_a_doctype_is_refused_with_a_note() -> None:
    bomb = '<!DOCTYPE robot [<!ENTITY a "aaaa">]><robot><link name="x"/></robot>'
    report = Inspector(_UrdfAdapter(_string_result(bomb))).inspect_tf()
    assert report.urdf_source == "none" and "DOCTYPE" in (report.urdf_note or "")


def test_a_failure_other_than_absence_is_still_a_note() -> None:
    report = Inspector(_UrdfAdapter(AdapterError("busy: retry"))).inspect_tf()
    assert report.urdf_source == "none" and "busy" in (report.urdf_note or "")


def test_no_budget_left_skips_the_urdf_read(monkeypatch: pytest.MonkeyPatch) -> None:
    from topicforge import budget

    adapter = _UrdfAdapter(_string_result("<robot><link name='x'/></robot>"))
    with budget.deadline_at(time.monotonic() + 1.0):
        report = Inspector(adapter).inspect_tf()
    assert adapter.sample_calls == []
    assert report.urdf_source == "none" and "budget" in (report.urdf_note or "")


def test_an_empty_tree_does_not_report_every_link_missing() -> None:
    from topicforge.adapters.common.tf_tree import build_tf_report
    from topicforge.adapters.common.urdf import parse_urdf
    from topicforge.models import TfCapture

    empty = TfCapture(window_actual_s=1.0)
    report = build_tf_report(
        empty, 2.0, None, None, urdf=parse_urdf("<robot><link name='a'/></robot>")
    )
    assert report.declared_frames_missing == [] and "not compared" in (report.urdf_note or "")
    assert all(f.kind != "declared_not_published" for f in report.findings)


def test_the_inspect_tf_result_over_mcp_carries_the_urdf_fields() -> None:
    data = asyncio.run(_app().call_tool("inspect_tf", {})).structured_content
    assert data["urdf_source"] == "robot_description"
    assert data["declared_frames_missing"] == ["gripper_link"]
    assert data["frames_not_in_urdf"] == ["map", "odom"]


# ----- URDF held only as a parameter (0.8.1) -----

_URDF = "<robot><link name='base_link'/><link name='laser'/></robot>"


class _ParamUrdfAdapter(_UrdfAdapter):
    """No `/robot_description` topic; a scripted robot_state_publisher parameter read."""

    def __init__(self, answer: tuple[str | None, str]) -> None:
        super().__init__(AdapterError("Topic not found or empty info: '/robot_description'"))
        self._param = answer
        self.param_waits: list[float] = []

    def robot_description_parameter(self, timeout_s: float) -> tuple[str | None, str]:
        self.param_waits.append(timeout_s)
        return self._param


def test_without_the_topic_the_parameter_is_compared_and_named() -> None:
    adapter = _ParamUrdfAdapter((_URDF, "/robot_state_publisher"))
    report = Inspector(adapter).inspect_tf()
    assert report.urdf_source == "robot_description" and report.urdf_link_count == 2
    assert "parameter of /robot_state_publisher" in (report.urdf_note or "")
    assert adapter.param_waits and adapter.param_waits[0] <= 6.0


def test_a_failed_parameter_read_is_a_note() -> None:
    report = Inspector(
        _ParamUrdfAdapter((None, "no robot_state_publisher node is running"))
    ).inspect_tf()
    assert report.urdf_source == "none"
    assert "no robot_state_publisher node is running" in (report.urdf_note or "")


def _dump(text: str) -> str:
    body = "\n".join("      " + line for line in text.splitlines())
    return f"/ns/robot_state_publisher:\n  ros__parameters:\n    robot_description: |\n{body}\n"


def test_live_reads_robot_description_from_the_dump(monkeypatch: pytest.MonkeyPatch) -> None:
    from topicforge.adapters.ros2_live.process_runner import ProcessResult

    adapter = Ros2CliAdapter()
    calls: list[list[str]] = []
    monkeypatch.setattr(adapter, "_node_names", lambda: ["/lidar", "/ns/robot_state_publisher"])

    def fake_exec(cmd: list[str], timeout: float, **_: Any) -> Any:
        calls.append(cmd)
        return ProcessResult(0, _dump(_URDF), "", timed_out=False, truncated=False), timeout

    monkeypatch.setattr(adapter, "_exec", fake_exec)
    text, node = adapter.robot_description_parameter(5.0)
    assert node == "/ns/robot_state_publisher" and text is not None and "laser" in text
    assert calls == [[adapter._exe, "param", "dump", "/ns/robot_state_publisher"]]


def test_live_without_robot_state_publisher_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = Ros2CliAdapter()
    monkeypatch.setattr(adapter, "_node_names", lambda: ["/lidar"])
    assert adapter.robot_description_parameter(5.0) == (
        None,
        "no robot_state_publisher node is running",
    )


def test_composite_routes_the_parameter_read_to_the_ros_half() -> None:
    class _Ros(MockAdapter):
        def robot_description_parameter(self, timeout_s: float) -> tuple[str | None, str]:
            return _URDF, "/robot_state_publisher"

    composite = CompositeAdapter(_Ros(), MockAdapter())
    assert composite.robot_description_parameter(1.0) == (_URDF, "/robot_state_publisher")
    assert (
        CompositeAdapter(MockAdapter(), MockAdapter()).robot_description_parameter(1.0)[0] is None
    )
