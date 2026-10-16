"""`inspect_tf`: the echo parser, the live capture (stubbed stream), mock, service and MCP layer.

No ROS 2: the live tests replace `stream_echo`. The Docker bench runs the same tool
against a real graph (`tests/integration/ros2/`).
"""

from __future__ import annotations

import asyncio
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

    def __init__(self, runs: dict[str, EchoRun]) -> None:
        self.runs = runs
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, cmd: list[str], **kw: Any) -> EchoRun:
        self.calls.append((cmd, kw))
        return self.runs[cmd[-1]]


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
    static_cmd, static_kw = echo.calls[0]
    assert static_cmd[-1] == "/tf_static" and static_kw["count"] == 2
    assert "transient_local" in static_cmd and "--full-length" in static_cmd
    assert static_kw["deadline_s"] <= 3.0
    tf_cmd, tf_kw = echo.calls[1]
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
    assert echo.calls[0][1]["deadline_s"] <= 2.0


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
    assert echo.calls[0][0][0] == "/bin/ros2"


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


def test_mock_report_has_exactly_one_stale_finding() -> None:
    report = Inspector(MockAdapter()).inspect_tf()
    assert [f.kind for f in report.findings] == ["stale"]
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
    assert clear.structured_content["findings"] == []


def test_inspect_tf_errors_reach_the_client() -> None:
    with pytest.raises(ToolError):
        asyncio.run(_app().call_tool("inspect_tf", {"source_frame": "laser"}))
    with pytest.raises(ToolError):
        asyncio.run(_app().call_tool("inspect_tf", {"window_s": 99}))
