"""Summaries and rate through the live adapter (stubbed CLI), the parser, and the MCP layer.

The correctness constraint under test: a summary is computed on the whole message even
when the returned payload is cut at `max_array_length`.
"""

from __future__ import annotations

import asyncio
import io
import math
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from topicforge.adapters.ros2_live.adapter import Ros2CliAdapter
from topicforge.adapters.ros2_live.echo_parser import cut_payload, parse_echo_document
from topicforge.adapters.ros2_live.echo_stream import EchoDocument, EchoRun, stream_echo
from topicforge.adapters.ros2_live.sample_extras import WHOLE_ARRAY_STREAM_MAX_CHARS
from topicforge.config import Settings
from topicforge.server import build_app

_MODULE = "topicforge.adapters.ros2_live.adapter"
S = 1_000_000_000
T0 = 1_760_000_000 * S


def _info(message_type: str) -> str:
    return f"Type: {message_type}\nPublisher count: 1\nSubscription count: 0\n"


def _scan_payload(stamp_sec: int = 5) -> dict[str, Any]:
    ranges: list[float] = [float(i % 7 + 1) for i in range(541)]
    ranges[270] = 0.75
    ranges[0] = math.inf
    ranges[1] = math.nan
    return {
        "header": {"stamp": {"sec": stamp_sec, "nanosec": 0}, "frame_id": "base_laser"},
        "angle_min": -2.35,
        "angle_max": 2.35,
        "angle_increment": 4.7 / 540,
        "range_min": 0.1,
        "range_max": 20.0,
        "ranges": ranges,
    }


def _dump(payload: dict[str, Any]) -> str:
    return yaml.safe_dump(payload, sort_keys=False)


def _cut_dump(payload: dict[str, Any], n: int = 128) -> str:
    """What `ros2 topic echo --truncate-length n` prints: arrays cut with a `...` element."""
    cut = {
        k: ([*v[:n], "..."] if isinstance(v, list) and len(v) > n else v)
        for k, v in payload.items()
    }
    return yaml.safe_dump(cut, sort_keys=False)


class _Harness:
    """Stubs the CLI side of `Ros2CliAdapter.sample_messages` and records the stream call."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, message_type: str, run: EchoRun) -> None:
        self.cmd: list[str] = []
        self.kwargs: dict[str, Any] = {}
        monkeypatch.setattr(f"{_MODULE}.shutil.which", lambda name: f"/fake/bin/{name}")
        monkeypatch.setattr(
            f"{_MODULE}.run_process",
            lambda *_a, **_k: SimpleNamespace(
                timed_out=False, returncode=0, stdout=_info(message_type), stderr=""
            ),
        )

        def fake_stream(cmd: list[str], **kw: Any) -> EchoRun:
            self.cmd, self.kwargs = cmd, kw
            return run

        monkeypatch.setattr(f"{_MODULE}.stream_echo", fake_stream)

    def sample(self, topic: str = "/x", count: int = 5, **options: Any) -> Any:
        return Ros2CliAdapter().sample_messages(topic, count, **options)


def _run(texts: list[str], step_ns: int = S // 5, **extra: Any) -> EchoRun:
    docs = [EchoDocument(t, T0 + i * step_ns) for i, t in enumerate(texts)]
    return EchoRun(
        documents=docs,
        started_ns=T0 - S,
        ended_ns=T0 + len(texts) * step_ns,
        **extra,
    )


# ---- whole-array fetch for the scan -----------------------------------------


def test_a_scan_is_streamed_whole_then_cut_and_the_summary_sees_all_beams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = _Harness(monkeypatch, "sensor_msgs/msg/LaserScan", _run([_dump(_scan_payload())]))
    sample = h.sample("/scan", 1).samples[0]
    assert "--full-length" in h.cmd and "--truncate-length" not in h.cmd
    assert h.kwargs["max_document_chars"] == WHOLE_ARRAY_STREAM_MAX_CHARS
    # The payload is cut exactly as the CLI default would have cut it ...
    assert len(sample.payload["ranges"]) == 128
    assert sample.payload["_truncated_fields"] == ["ranges"]
    # ... while the summary reads beam 270, which is past the cut.
    summary = sample.summary
    assert summary is not None and summary.beam_count == 541
    assert summary.closest_obstacle.range == 0.75  # type: ignore[union-attr]
    assert summary.closest_obstacle.beam_index == 270  # type: ignore[union-attr]
    assert (summary.inf_count, summary.nan_count) == (1, 1)
    assert sample.timestamp_ns == 5 * S and sample.stamp_source == "header"


def test_a_scan_with_a_custom_cut_is_cut_at_that_length(monkeypatch: pytest.MonkeyPatch) -> None:
    h = _Harness(monkeypatch, "sensor_msgs/msg/LaserScan", _run([_dump(_scan_payload())]))
    sample = h.sample("/scan", 1, max_array_length=10).samples[0]
    assert len(sample.payload["ranges"]) == 10
    assert sample.payload["_truncated_fields"] == ["ranges"]
    assert sample.summary is not None and sample.summary.beam_count == 541


def test_a_scan_with_no_cut_streams_with_the_normal_size_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = _Harness(monkeypatch, "sensor_msgs/msg/LaserScan", _run([_dump(_scan_payload())]))
    sample = h.sample("/scan", 1, max_array_length=None).samples[0]
    assert "--full-length" in h.cmd
    assert h.kwargs["max_document_chars"] == 1024 * 1024
    assert len(sample.payload["ranges"]) == 541 and "_truncated_fields" not in sample.payload
    assert sample.summary is not None and sample.summary.beam_count == 541


def test_arrays_summary_only_streams_normally_and_gives_no_scan_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {**_scan_payload(), "ranges": "<sequence type: float, length: 541>"}
    h = _Harness(monkeypatch, "sensor_msgs/msg/LaserScan", _run([_dump(payload)]))
    sample = h.sample("/scan", 1, arrays_summary_only=True).samples[0]
    assert "--no-arr" in h.cmd and "--full-length" not in h.cmd
    assert sample.summary is None
    assert sample.payload["ranges"] == "<sequence type: float, length: 541>"


# ---- types that never need their arrays -------------------------------------


def test_an_image_is_never_streamed_whole_and_reports_its_buffer_from_the_geometry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = {
        "header": {"stamp": {"sec": 1, "nanosec": 0}, "frame_id": "camera"},
        "height": 480,
        "width": 640,
        "encoding": "rgb8",
        "is_bigendian": 0,
        "step": 1920,
        "data": [0] * 129,
    }
    h = _Harness(monkeypatch, "sensor_msgs/msg/Image", _run([_cut_dump(image)]))
    sample = h.sample("/camera/image_raw", 1).samples[0]
    assert "--full-length" not in h.cmd and "--truncate-length" not in h.cmd
    assert sample.payload["_truncated_fields"] == ["data"]
    summary = sample.summary
    assert summary is not None and summary.summary_type == "image"
    assert (summary.width, summary.height, summary.encoding) == (640, 480, "rgb8")
    assert (summary.data_length, summary.data_length_basis) == (921600, "step_x_height")


def test_odometry_is_summarized_from_the_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    odom = {
        "header": {"stamp": {"sec": 1, "nanosec": 0}, "frame_id": "odom"},
        "child_frame_id": "base_link",
        "pose": {
            "pose": {
                "position": {"x": 1.0, "y": 0.0, "z": 0.0},
                "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
            },
            "covariance": [0.0] * 36,
        },
        "twist": {"twist": {"linear": {"x": 0.3, "y": 0.4, "z": 0.0}, "angular": {"z": 0.1}}},
    }
    h = _Harness(monkeypatch, "nav_msgs/msg/Odometry", _run([_dump(odom)]))
    summary = h.sample("/odom", 1).samples[0].summary
    assert summary is not None and summary.summary_type == "odometry"
    assert summary.linear_speed == pytest.approx(0.5) and summary.yaw == 0.0


def test_an_unsupported_type_has_no_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    h = _Harness(monkeypatch, "geometry_msgs/msg/Twist", _run([_dump({"linear": {"x": 1.0}})]))
    assert h.sample("/cmd_vel", 1).samples[0].summary is None


# ---- the rate block ---------------------------------------------------------


def test_rate_is_measured_on_arrival_times_and_carries_the_sim_rate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    texts = [_dump(_scan_payload(stamp_sec=i)) for i in range(10)]
    h = _Harness(monkeypatch, "sensor_msgs/msg/LaserScan", _run(texts))
    rate = h.sample("/scan", 10).rate
    assert rate is not None and rate.basis == "received_ns"
    assert rate.verdict == "stable" and rate.message_count == 10
    assert rate.observed_frequency_hz == pytest.approx(5.0)
    assert rate.sim_frequency_hz == pytest.approx(1.0)  # stamps advance 1 s per message
    assert rate.trailing_gap_s is None  # stopped on count


def test_rate_counts_the_trailing_gap_when_the_deadline_ended_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    texts = [_dump({"data": float(i)}) for i in range(6)]
    run = _run(texts)
    run.ended_ns = run.documents[-1].received_ns + 5 * S  # a publisher that stopped
    h = _Harness(monkeypatch, "std_msgs/msg/Float64", run)
    rate = h.sample("/x", 20).rate  # asked for 20, got 6: the deadline ended it
    assert rate is not None
    assert rate.trailing_gap_s == pytest.approx(5.0)
    assert rate.verdict == "intermittent"
    assert rate.sim_frequency_hz is None  # no stamps on std_msgs/Float64


def test_a_silent_topic_reports_a_silent_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    run = EchoRun(started_ns=T0, ended_ns=T0 + 3 * S)
    h = _Harness(monkeypatch, "std_msgs/msg/Float64", run)
    result = h.sample("/x", 5)
    assert result.count == 0
    assert result.rate is not None and result.rate.verdict == "silent"
    assert result.rate.window_s == 3.0


def test_messages_dropped_for_size_still_count_in_the_rate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kept = [EchoDocument(_dump({"data": 1.0}), T0 + i * 2 * (S // 5)) for i in range(5)]
    run = EchoRun(
        documents=kept,
        oversized=5,
        oversized_received_ns=[T0 + (2 * i + 1) * (S // 5) for i in range(5)],
        started_ns=T0,
        ended_ns=T0 + 2 * S,
    )
    h = _Harness(monkeypatch, "std_msgs/msg/Float64", run)
    rate = h.sample("/x", 10).rate
    assert rate is not None and rate.message_count == 10
    assert rate.observed_frequency_hz == pytest.approx(5.0)
    assert rate.verdict == "stable"


# ---- parser and stream pieces -----------------------------------------------


def test_cut_payload_equals_what_the_cli_truncation_produces() -> None:
    payload = _scan_payload()
    whole = parse_echo_document(_dump(payload), truncate_length=None).payload
    via_cli = parse_echo_document(_cut_dump(payload), truncate_length=128).payload
    assert cut_payload(whole, 128) == via_cli
    assert via_cli["_truncated_fields"] == ["ranges"]


def test_cut_payload_cuts_long_strings_like_the_cli() -> None:
    cut = cut_payload({"text": "x" * 300, "short": "ok"}, 128)
    assert cut["text"] == "x" * 128 + "..." and cut["short"] == "ok"
    assert cut["_truncated_fields"] == ["text"]


def test_cut_payload_with_no_limit_returns_the_payload_untouched() -> None:
    payload = {"a": list(range(500))}
    assert cut_payload(payload, None) is payload


def test_cut_payload_records_nested_paths_once() -> None:
    cut = cut_payload({"items": [{"v": list(range(5))}, {"v": list(range(5))}]}, 3)
    assert cut["_truncated_fields"] == ["items.v"]
    assert cut["items"] == [{"v": [0, 1, 2]}, {"v": [0, 1, 2]}]


class _FakeProc:
    """A finished process whose stdout is `text`.

    It reports itself as exited, and its pid is never a real process group:
    the stream's tree kill must not signal anything on the host.
    """

    pid = -1

    def __init__(self, text: str) -> None:
        self.stdout = io.StringIO(text)
        self.stderr = io.StringIO("")

    def poll(self) -> int | None:
        return 0

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        pass


def test_the_stream_keeps_the_arrival_time_of_a_dropped_message() -> None:
    text = (
        "a: 1"
        + chr(10)
        + "---"
        + chr(10)
        + "b: "
        + "x" * 100
        + chr(10)
        + "---"
        + chr(10)
        + "c: 3"
        + chr(10)
        + "---"
        + chr(10)
    )
    run = stream_echo(
        ["x"],
        count=2,
        deadline_s=5.0,
        max_document_chars=50,
        popen=lambda *_a, **_k: _FakeProc(text),
    )
    assert [d.text for d in run.documents] == ["a: 1" + chr(10), "c: 3" + chr(10)]
    assert run.oversized == 1 and len(run.oversized_received_ns) == 1
    assert run.started_ns > 0 and run.ended_ns >= run.started_ns


# ---- through the MCP layer, in mock mode ------------------------------------


def _call(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    app = build_app(
        Settings(mode="mock", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False)
    )
    result = asyncio.run(app.call_tool(tool, args))
    assert isinstance(result.structured_content, dict)
    return result.structured_content


def test_sample_messages_returns_summary_and_rate_over_mcp() -> None:
    out = _call("sample_messages", {"topic": "/scan", "count": 5})
    summary = out["samples"][0]["summary"]
    assert summary["summary_type"] == "laser_scan" and summary["beam_count"] == 720
    assert summary["sectors"]["right"]["closest"]["range"] == pytest.approx(1.5, abs=0.01)
    assert summary["sectors"]["front"]["closest"]["beam_index"] >= 0
    assert len(out["samples"][0]["payload"]["ranges"]) == 128
    rate = out["rate"]
    assert rate["verdict"] == "stable" and rate["basis"] == "received_ns"
    assert rate["observed_frequency_hz"] == pytest.approx(10.0)
    assert rate["trailing_gap_s"] is None


def test_odometry_and_imu_summaries_over_mcp() -> None:
    odom = _call("sample_messages", {"topic": "/odom", "count": 5})
    assert odom["samples"][0]["summary"]["linear_speed"] == pytest.approx(0.2)
    imu = _call("sample_messages", {"topic": "/imu/data", "count": 5})
    assert imu["samples"][0]["summary"]["summary_type"] == "imu"
    assert imu["samples"][0]["summary"]["linear_acceleration_norm"] == pytest.approx(9.81)


def test_peek_bag_samples_returns_summary_and_a_recorded_rate_over_mcp() -> None:
    out = _call("peek_bag_samples", {"path": "/tmp/demo.mcap", "topic": "/odom", "count": 5})
    assert out["samples"][0]["summary"]["summary_type"] == "odometry"
    assert out["rate"]["basis"] == "recorded_ns" and out["rate"]["verdict"] == "stable"


def test_an_unsupported_type_has_a_null_summary_over_mcp() -> None:
    out = _call("sample_messages", {"topic": "/cmd_vel", "count": 5})
    assert out["samples"][0]["summary"] is None
    assert out["rate"]["verdict"] == "stable"
