"""`/rosout` filter (`min_log_level`, `node`) and bag ranges: pure rules, mock, live, bag."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from topicforge.adapters.base import AdapterError
from topicforge.adapters.common.bag_range import range_note, validate_range, window_ns
from topicforge.adapters.common.log_filter import (
    LOG_READER_DEPTH,
    LOG_STREAM_CAP,
    LogFilter,
    level_threshold,
    matches_log,
    normalize_logger_name,
    normalize_node_filter,
)
from topicforge.adapters.ros2_live.adapter import Ros2CliAdapter
from topicforge.adapters.ros2_live.echo_stream import EchoDocument, EchoRun
from topicforge.adapters.ros2_mock.adapter import MockAdapter
from topicforge.services import Inspector

_MODULE = "topicforge.adapters.ros2_live.adapter"
_LOG_INFO = (
    "Type: rcl_interfaces/msg/Log\nPublisher count: 2\nSubscription count: 0\n"
    "\nNode name: bench_robot\nEndpoint type: PUBLISHER\nReliability: RELIABLE\n"
    "Durability: TRANSIENT_LOCAL\n"
)


# ----- pure rules -----


def test_level_thresholds_are_the_ros_constants() -> None:
    assert [level_threshold(n) for n in ("debug", "info", "warn", "error", "fatal")] == [
        10,
        20,
        30,
        40,
        50,
    ]
    assert level_threshold(None) is None
    with pytest.raises(AdapterError, match="min_log_level"):
        level_threshold("warning")


@pytest.mark.parametrize("name", ["/robot1/driver", "robot1/driver", "robot1.driver"])
def test_node_forms_normalize_to_the_logger_name(name: str) -> None:
    assert normalize_logger_name(name) == "robot1.driver"


@pytest.mark.parametrize("bad", ["", "/", "//", "a//b", "a."])
def test_a_node_that_names_nothing_is_rejected(bad: str) -> None:
    with pytest.raises(AdapterError, match="node"):
        normalize_node_filter(bad)


def test_node_match_is_exact_or_child_never_a_prefix() -> None:
    def hit(name: str, node: str) -> bool:
        return matches_log(
            {"level": 40, "name": name}, threshold=None, logger=normalize_node_filter(node)
        )

    assert hit("driver", "/driver")
    assert hit("driver.camera", "/driver")
    assert hit("robot1.driver", "/robot1/driver")
    assert not hit("driver2", "/driver")
    assert not hit("drive", "/driver")
    assert not hit("driver", "/drive")
    assert not hit("Driver", "/driver")  # case-sensitive
    assert not hit("robot1.driver", "/driver")


def test_level_filter_is_greater_or_equal_and_needs_a_numeric_level() -> None:
    assert matches_log({"level": 30}, threshold=30, logger=None)
    assert matches_log({"level": 50}, threshold=30, logger=None)
    assert not matches_log({"level": 20}, threshold=30, logger=None)
    assert not matches_log({}, threshold=30, logger=None)
    assert not matches_log({"level": True}, threshold=1, logger=None)


def test_log_filter_of_nothing_is_none() -> None:
    assert LogFilter.of(None, None) is None
    assert str(LogFilter.of("error", "/a")) == "min_log_level=error, node=/a"


def test_range_rules() -> None:
    validate_range(None, None)
    validate_range(0, 2.5)
    for bad in ((-1, None), (None, -0.5), (3, 3), (4, 2), (float("nan"), None)):
        with pytest.raises(AdapterError):
            validate_range(*bad)
    assert window_ns(1_000, 1.5, 3) == (1_500_001_000, 3_000_001_000)
    assert window_ns(1_000, None, None) == (None, None)
    assert "past the end" in (range_note(100, None, duration_s=10, got=0) or "")
    assert range_note(None, None, duration_s=10, got=0) is None


# ----- mock -----


def _mock() -> Inspector:
    return Inspector(MockAdapter())


def test_mock_filter_returns_the_newest_matches_in_stamp_order() -> None:
    result = _mock().sample_messages("/rosout", 2, min_log_level="error")
    names = [s.payload["name"] for s in result.samples]
    # Errors and fatal in the fixture: camera_driver, lidar_driver, base_controller(50),
    # nav_planner.costmap. The newest two, oldest first.
    assert names == ["base_controller", "nav_planner.costmap"]
    assert [s.timestamp_ns for s in result.samples] == sorted(
        s.timestamp_ns for s in result.samples
    )
    assert result.note is not None
    assert "4 of 10 log messages read matched min_log_level=error" in result.note
    assert "newest 2" in result.note


def test_mock_node_filter_takes_children_and_not_prefixes() -> None:
    result = _mock().sample_messages("/rosout", 10, node="/nav_planner")
    assert {s.payload["name"] for s in result.samples} == {"nav_planner", "nav_planner.costmap"}
    assert _mock().sample_messages("/rosout", 10, node="/nav").samples == []


def test_mock_empty_match_still_explains_itself() -> None:
    result = _mock().sample_messages("/rosout", 5, node="/bench_blocked")
    assert result.count == 0
    assert result.note is not None and "0 of 10" in result.note and "Nothing matched" in result.note


def test_mock_without_a_filter_returns_the_first_messages() -> None:
    result = _mock().sample_messages("/rosout", 3)
    assert result.samples[0].payload["msg"] == "planner started"
    assert result.count == 3


def test_a_filter_on_a_non_log_topic_is_an_error() -> None:
    with pytest.raises(AdapterError, match="rcl_interfaces/msg/Log"):
        _mock().sample_messages("/odom", 3, min_log_level="error")
    with pytest.raises(AdapterError, match="min_log_level"):
        _mock().sample_messages("/rosout", 3, min_log_level="loud")
    with pytest.raises(AdapterError, match="node"):
        _mock().sample_messages("/rosout", 3, node="/")


def test_mock_bag_range_and_filter() -> None:
    insp = _mock()
    all_logs = insp.peek_bag_samples("/tmp/demo.mcap", "/rosout", 50)
    assert all_logs.count == 10
    ranged = insp.peek_bag_samples("/tmp/demo.mcap", "/rosout", 50, start_s=1, end_s=2.5)
    assert [s.payload["msg"] for s in ranged.samples] == [
        s.payload["msg"] for s in all_logs.samples[2:5]
    ]
    errors = insp.peek_bag_samples("/tmp/demo.mcap", "/rosout", 2, min_log_level="error")
    assert [s.payload["level"] for s in errors.samples] == [40, 40]
    assert errors.samples[0].payload["name"] == "camera_driver"  # first matches, bag order
    past = insp.peek_bag_samples("/tmp/demo.mcap", "/cmd_vel", 5, start_s=500)
    assert past.count == 0 and past.note and "past the end" in past.note
    with pytest.raises(AdapterError, match="rcl_interfaces"):
        insp.peek_bag_samples("/tmp/demo.mcap", "/cmd_vel", 5, node="/x")


def test_mock_analyze_bag_has_start_ns() -> None:
    assert MockAdapter().analyze_bag("/tmp/demo.mcap").start_ns == 1_700_000_000_000_000_000


# ----- live -----


def _log_doc(sec: int, level: int, name: str, msg: str = "m") -> EchoDocument:
    text = (
        f"stamp:\n  sec: {sec}\n  nanosec: 0\nlevel: {level}\nname: {name}\nmsg: {msg}\n"
        "file: f.cpp\nfunction: fn\nline: 3\n"
    )
    return EchoDocument(text, sec * 1_000_000_000)


def _stub_live(
    monkeypatch: pytest.MonkeyPatch, run: EchoRun
) -> tuple[list[list[str]], list[dict[str, Any]]]:
    cmds: list[list[str]] = []
    kws: list[dict[str, Any]] = []
    monkeypatch.setattr(f"{_MODULE}.shutil.which", lambda name: f"/fake/bin/{name}")
    monkeypatch.setattr(
        f"{_MODULE}.run_process",
        lambda *_a, **_k: SimpleNamespace(
            timed_out=False, returncode=0, stdout=_LOG_INFO, stderr=""
        ),
    )

    def fake_stream(cmd: list[str], **kw: Any) -> EchoRun:
        cmds.append(cmd)
        kws.append(kw)
        return run

    monkeypatch.setattr(f"{_MODULE}.stream_echo", fake_stream)
    return cmds, kws


def test_live_log_topic_gets_a_deep_reader_even_without_a_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = EchoRun(documents=[_log_doc(1, 20, "a"), _log_doc(2, 40, "b")])
    cmds, kws = _stub_live(monkeypatch, run)
    result = Ros2CliAdapter().sample_messages("/rosout", 2)
    cmd = cmds[0]
    assert cmd[cmd.index("--qos-history") + 1] == "keep_last"
    assert cmd[cmd.index("--qos-depth") + 1] == str(LOG_READER_DEPTH)
    assert kws[0]["count"] == 2  # unchanged semantics: first count
    assert [s.payload["name"] for s in result.samples] == ["a", "b"]


def test_live_non_log_topic_has_no_depth_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    cmds, _ = _stub_live(monkeypatch, EchoRun())
    monkeypatch.setattr(
        f"{_MODULE}.run_process",
        lambda *_a, **_k: SimpleNamespace(
            timed_out=False,
            returncode=0,
            stdout="Type: std_msgs/msg/String\nPublisher count: 1\n",
            stderr="",
        ),
    )
    Ros2CliAdapter().sample_messages("/chatter", 1)
    assert "--qos-depth" not in cmds[0]


def test_live_filter_streams_to_the_cap_and_keeps_the_newest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docs = [_log_doc(i, 40 if i % 2 else 20, "bench_robot") for i in range(1, 11)]
    docs.append(_log_doc(11, 40, "other"))
    docs.append(_log_doc(12, 50, "bench_robot.sub"))
    _, kws = _stub_live(monkeypatch, EchoRun(documents=docs))
    result = Ros2CliAdapter().sample_messages(
        "/rosout", 3, min_log_level="error", node="/bench_robot"
    )
    assert kws[0]["count"] == LOG_STREAM_CAP
    stamps = [s.timestamp_ns // 1_000_000_000 for s in result.samples]
    assert stamps == [7, 9, 12]  # errors of bench_robot and children, newest 3, oldest first
    assert result.rate is None
    assert result.note is not None
    assert "6 of 12 log messages read matched min_log_level=error, node=/bench_robot" in result.note
    assert "the newest 3 are returned" in result.note


def test_live_filter_with_nothing_read_reports_why(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_live(monkeypatch, EchoRun())
    result = Ros2CliAdapter().sample_messages("/rosout", 3, node="/bench_blocked", timeout_s=2)
    assert result.count == 0
    assert result.note is not None and "0 of 0 log messages" in result.note


def test_live_filter_on_a_non_log_topic_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_live(monkeypatch, EchoRun())
    monkeypatch.setattr(
        f"{_MODULE}.run_process",
        lambda *_a, **_k: SimpleNamespace(
            timed_out=False,
            returncode=0,
            stdout="Type: std_msgs/msg/String\nPublisher count: 1\n",
            stderr="",
        ),
    )
    with pytest.raises(AdapterError, match="rcl_interfaces/msg/Log"):
        Ros2CliAdapter().sample_messages("/chatter", 3, min_log_level="warn")


# ----- bag (real rosbags reader) -----

_BASE = 1_700_000_000_000_000_000


def _write_bag(path: Path) -> Path:
    pytest.importorskip("rosbags")
    from rosbags.rosbag2 import Writer  # type: ignore[import-not-found]
    from rosbags.typesys import Stores, get_typestore  # type: ignore[import-not-found]

    ts = get_typestore(Stores.LATEST)
    log_t, clock_t, time_t = (
        "rcl_interfaces/msg/Log",
        "rosgraph_msgs/msg/Clock",
        "builtin_interfaces/msg/Time",
    )
    with Writer(path, version=Writer.VERSION_LATEST) as writer:
        logs = writer.add_connection("/rosout", log_t, typestore=ts)
        clock = writer.add_connection("/clock", clock_t, typestore=ts)
        events = [(i * 500_000_000, "log", i) for i in range(12)]
        events += [(i * 100_000_000, "clock", i) for i in range(60)]
        for offset, kind, i in sorted(events):
            when = _BASE + offset
            stamp = ts.types[time_t](sec=when // 1_000_000_000, nanosec=when % 1_000_000_000)
            if kind == "log":
                msg = ts.types[log_t](
                    stamp=stamp,
                    level=[20, 40][i % 2],
                    name="bench_robot" if i % 3 else "other",
                    msg=f"line {i}",
                    file="f",
                    function="fn",
                    line=i,
                )
                writer.write(logs, when, ts.serialize_cdr(msg, log_t))
            else:
                msg = ts.types[clock_t](clock=stamp)
                writer.write(clock, when, ts.serialize_cdr(msg, clock_t))
    return path


def test_bag_range_is_bag_relative_half_open(tmp_path: Path) -> None:
    from topicforge.services.bag_service import BagService

    bag = _write_bag(tmp_path / "bag")
    result = BagService().peek_samples(str(bag), "/clock", 50, start_s=2, end_s=4)
    got = [s.recorded_ns - _BASE for s in result.samples]  # type: ignore[operator]
    assert got == [i * 100_000_000 for i in range(20, 40)]  # 2.0 s inclusive, 4.0 s exclusive
    first = BagService().peek_samples(str(bag), "/clock", 5, start_s=2)
    assert first.count == 5 and first.samples[0].recorded_ns == _BASE + 2_000_000_000
    past = BagService().peek_samples(str(bag), "/clock", 5, start_s=100)
    assert past.count == 0 and past.note and "past the end" in past.note


def test_bag_log_filter_returns_the_first_matches_in_bag_order(tmp_path: Path) -> None:
    from topicforge.services.bag_service import BagService

    bag = _write_bag(tmp_path / "bag")
    result = BagService().peek_samples(
        str(bag), "/rosout", 3, log_filter=LogFilter("error", "/bench_robot")
    )
    # i odd -> error, i % 3 != 0 -> bench_robot: i in 1, 5, 7, 11
    assert [s.payload["line"] for s in result.samples] == [1, 5, 7]
    assert result.note is not None and "first matches" in result.note
    ranged = BagService().peek_samples(
        str(bag), "/rosout", 10, start_s=3, log_filter=LogFilter("error", None)
    )
    assert [s.payload["line"] for s in ranged.samples] == [7, 9, 11]
    with pytest.raises(AdapterError, match="rcl_interfaces"):
        BagService().peek_samples(str(bag), "/clock", 3, log_filter=LogFilter("error", None))


def test_bag_start_ns_is_the_metadata_starting_time(tmp_path: Path) -> None:
    from topicforge.services.bag_service import BagService, read_start_ns

    bag = _write_bag(tmp_path / "bag")
    assert read_start_ns(bag) == _BASE
    assert BagService().analyze(str(bag)).start_ns == _BASE
    assert read_start_ns(tmp_path / "missing") is None
