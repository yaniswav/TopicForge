"""Which stream `sample_messages` runs on, and the fallback rules. No ROS 2, no process."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from topicforge.adapters.base import AdapterError
from topicforge.adapters.common.ros_discovery import (
    cli_ghost_note,
    cli_pid_of_node,
    flag_topicforge_cli,
    is_own_node,
    is_sampler_node,
)
from topicforge.adapters.composite import CompositeAdapter
from topicforge.adapters.ros2_live import Ros2CliAdapter
from topicforge.adapters.ros2_live.echo_stream import EchoDocument, EchoRun
from topicforge.adapters.ros2_live.helper_stream import (
    EXIT_BAD_FRAMES,
    HELPER_LABEL,
    HELPER_PATH,
    SamplingBackend,
)
from topicforge.adapters.ros2_mock import MockAdapter
from topicforge.config import Settings
from topicforge.config.settings import load_settings
from topicforge.models import ParticipantInfo, TopicInfo
from topicforge.services import factory
from topicforge.services.health import HealthService

_SAMPLER = "topicforge.adapters.ros2_live.sampler"
_YAML = "data: hello\n"


def _info(message_type: str = "std_msgs/msg/String", publishers: int = 1) -> TopicInfo:
    return TopicInfo(
        name="/t",
        message_type=message_type,
        publisher_count=publishers,
        subscriber_count=0,
        mode_effective="live",
    )


class _Streams:
    """Records which stream ran, and returns the run scripted for it."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, helper: EchoRun | Exception) -> None:
        self.helper_cmds: list[list[str]] = []
        self.echo_cmds: list[list[str]] = []
        self.echo_kwargs: dict[str, Any] = {}
        self.helper_result = helper
        monkeypatch.setattr(f"{_SAMPLER}.stream_helper", self._helper)
        monkeypatch.setattr(f"{_SAMPLER}.stream_echo", self._echo)

    def _helper(self, cmd: list[str], **_kwargs: object) -> EchoRun:
        self.helper_cmds.append(cmd)
        if isinstance(self.helper_result, Exception):
            raise self.helper_result
        return self.helper_result

    def _echo(self, cmd: list[str], **kwargs: object) -> EchoRun:
        self.echo_cmds.append(cmd)
        self.echo_kwargs = dict(kwargs)
        return EchoRun(documents=[EchoDocument(_YAML, 5)], started_ns=1, ended_ns=9)


def _adapter(
    monkeypatch: pytest.MonkeyPatch, backend: SamplingBackend, info: TopicInfo | None = None
) -> Ros2CliAdapter:
    adapter = Ros2CliAdapter()
    adapter.sampling = backend
    monkeypatch.setattr(adapter, "_topic_info", lambda _topic, _timeout: info or _info())
    monkeypatch.setattr(adapter, "_resolve_exe", lambda: "/opt/ros2")
    return adapter


def _good_run() -> EchoRun:
    return EchoRun(documents=[EchoDocument(_YAML, 5)], started_ns=1, ended_ns=9, tool=HELPER_LABEL)


_RCLPY = SamplingBackend("rclpy", python="/usr/bin/python3")
_AUTO = SamplingBackend("rclpy", python="/usr/bin/python3", fallback=True)


def test_the_default_backend_is_the_cli_and_nothing_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    streams = _Streams(monkeypatch, AssertionError("no helper"))
    result = _adapter(monkeypatch, SamplingBackend()).sample_messages("/t", 1)
    assert result.count == 1
    assert streams.helper_cmds == []
    assert streams.echo_cmds[0][:4] == ["/opt/ros2", "topic", "echo", "--no-lost-messages"]


def test_rclpy_runs_the_helper_with_the_flags_echo_would_get(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    streams = _Streams(monkeypatch, _good_run())
    result = _adapter(monkeypatch, _RCLPY).sample_messages("/t", 2, max_array_length=10)
    assert streams.echo_cmds == []
    cmd = streams.helper_cmds[0]
    assert cmd[:2] == ["/usr/bin/python3", str(HELPER_PATH)]
    assert cmd[-1] == "/t"
    for flag in (
        ["--type", "std_msgs/msg/String"],
        ["--count", "2"],
        ["--qos-reliability", "best_effort"],
        ["--qos-durability", "volatile"],
        ["--truncate-length", "10"],
    ):
        at = cmd.index(flag[0])
        assert cmd[at : at + 2] == flag
    assert "--max-chars" in cmd
    assert result.samples[0].payload == {"data": "hello"}
    assert result.samples[0].received_ns == 5


@pytest.mark.parametrize(
    ("options", "absent", "present"),
    [
        ({"max_array_length": None}, "--truncate-length", "--full-length"),
        ({"arrays_summary_only": True}, "--full-length", "--no-arr"),
    ],
)
def test_array_flags_are_the_cli_ones(
    monkeypatch: pytest.MonkeyPatch, options: dict[str, Any], absent: str, present: str
) -> None:
    streams = _Streams(monkeypatch, _good_run())
    _adapter(monkeypatch, _RCLPY).sample_messages("/t", 1, **options)
    assert present in streams.helper_cmds[0]
    assert absent not in streams.helper_cmds[0]


def test_a_filtered_log_read_asks_for_the_deep_history(monkeypatch: pytest.MonkeyPatch) -> None:
    streams = _Streams(monkeypatch, EchoRun(tool=HELPER_LABEL))
    info = _info("rcl_interfaces/msg/Log")
    _adapter(monkeypatch, _RCLPY, info).sample_messages("/rosout", 1, min_log_level="error")
    cmd = streams.helper_cmds[0]
    assert cmd[cmd.index("--qos-depth") + 1] == "2000"
    assert cmd[cmd.index("--count") + 1] == "2000"


def test_unavailable_raises_the_reason_before_any_process(monkeypatch: pytest.MonkeyPatch) -> None:
    streams = _Streams(monkeypatch, AssertionError("no helper"))
    backend = SamplingBackend("unavailable", note="TOPICFORGE_ROS_SAMPLER=rclpy, but ImportError")
    with pytest.raises(AdapterError, match="ImportError"):
        _adapter(monkeypatch, backend).sample_messages("/t", 1)
    assert streams.echo_cmds == []


def test_an_unusual_type_string_stays_on_the_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    streams = _Streams(monkeypatch, AssertionError("no helper"))
    result = _adapter(monkeypatch, _RCLPY, _info("weird type")).sample_messages("/t", 1)
    assert result.count == 1
    assert streams.helper_cmds == []


# ---- a helper that fails during a call ----


def test_rclpy_does_not_fall_back(monkeypatch: pytest.MonkeyPatch) -> None:
    streams = _Streams(
        monkeypatch, EchoRun(exit_code=5, stderr_tail="ImportError: x", tool=HELPER_LABEL)
    )
    with pytest.raises(AdapterError) as caught:
        _adapter(monkeypatch, _RCLPY).sample_messages("/t", 1)
    assert str(caught.value) == "the sampling helper failed (exit 5): ImportError: x"
    assert "ros2 topic echo" not in str(caught.value)
    assert streams.echo_cmds == []


def test_rclpy_does_not_fall_back_on_a_failed_start(monkeypatch: pytest.MonkeyPatch) -> None:
    streams = _Streams(monkeypatch, AdapterError("could not start the sampling helper"))
    with pytest.raises(AdapterError, match="could not start"):
        _adapter(monkeypatch, _RCLPY).sample_messages("/t", 1)
    assert streams.echo_cmds == []


@pytest.mark.parametrize(
    "failure",
    [
        EchoRun(exit_code=5, stderr_tail="boom", tool=HELPER_LABEL),
        EchoRun(exit_code=EXIT_BAD_FRAMES, stderr_tail="bad output", tool=HELPER_LABEL),
        AdapterError("could not start the sampling helper"),
    ],
)
def test_auto_falls_back_to_the_cli_for_that_call(
    monkeypatch: pytest.MonkeyPatch, failure: EchoRun | Exception
) -> None:
    streams = _Streams(monkeypatch, failure)
    result = _adapter(monkeypatch, _AUTO).sample_messages("/t", 1)
    assert len(streams.helper_cmds) == len(streams.echo_cmds) == 1
    assert result.count == 1


def test_auto_keeps_what_the_helper_delivered(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _good_run()
    run.exit_code = -11
    streams = _Streams(monkeypatch, run)
    result = _adapter(monkeypatch, _AUTO).sample_messages("/t", 2)
    assert streams.echo_cmds == []
    assert result.count == 1
    assert "the sampling helper exited early (exit -11)" in (result.note or "")


def test_auto_does_not_retry_an_empty_run_that_ended_as_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    streams = _Streams(monkeypatch, EchoRun(tool=HELPER_LABEL))
    result = _adapter(monkeypatch, _AUTO).sample_messages("/t", 1, timeout_s=1)
    assert result.count == 0
    assert streams.echo_cmds == []


def test_the_fallback_keeps_the_time_left(monkeypatch: pytest.MonkeyPatch) -> None:
    streams = _Streams(monkeypatch, EchoRun(exit_code=5, tool=HELPER_LABEL))
    _adapter(monkeypatch, _AUTO).sample_messages("/t", 1, timeout_s=4)
    assert 0.5 <= streams.echo_kwargs["deadline_s"] <= 4


# ---- what the notes say on each stream ----


def test_the_cli_notes_are_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(*_args: object, **_kwargs: object) -> EchoRun:
        return EchoRun(documents=[EchoDocument(_YAML, 5)], exit_code=1, stderr_tail="x")

    monkeypatch.setattr(f"{_SAMPLER}.stream_echo", run)
    result = _adapter(monkeypatch, SamplingBackend()).sample_messages("/t", 2)
    assert "the `ros2` CLI exited early (exit 1): x." in (result.note or "")


@pytest.mark.parametrize("options", [{}, {"max_array_length": 10}, {"max_array_length": None}])
def test_the_same_text_gives_the_same_result_on_both_streams(
    monkeypatch: pytest.MonkeyPatch, options: dict[str, Any]
) -> None:
    """Nothing after the stream depends on the backend: payload, cuts, summary, stamp, rate."""
    fixture = Path(__file__).parent / "fixtures" / "ros2_echo" / "scan_trunc128.yaml"
    text = fixture.read_text(encoding="utf-8").split("---\n")[0]
    docs = [EchoDocument(text, 100), EchoDocument(text, 200)]

    def echo(*_args: object, **_kwargs: object) -> EchoRun:
        return EchoRun(documents=list(docs), started_ns=50, ended_ns=250)

    def helper(*_args: object, **_kwargs: object) -> EchoRun:
        return EchoRun(documents=list(docs), started_ns=50, ended_ns=250, tool=HELPER_LABEL)

    monkeypatch.setattr(f"{_SAMPLER}.stream_echo", echo)
    monkeypatch.setattr(f"{_SAMPLER}.stream_helper", helper)
    info = _info("sensor_msgs/msg/LaserScan")
    cli = _adapter(monkeypatch, SamplingBackend(), info).sample_messages("/t", 2, **options)
    rclpy = _adapter(monkeypatch, _RCLPY, info).sample_messages("/t", 2, **options)
    assert rclpy.model_dump() == cli.model_dump()
    assert cli.samples[0].summary is not None


# ---- settings ----


def test_settings_default_and_values() -> None:
    assert load_settings({}).ros_sampler == "auto"
    assert load_settings({}).ros_python is None
    settings = load_settings(
        {"TOPICFORGE_ROS_SAMPLER": " RCLPY ", "TOPICFORGE_ROS_PYTHON": " /usr/bin/python3 "}
    )
    assert settings.ros_sampler == "rclpy"
    assert settings.ros_python == "/usr/bin/python3"


def test_settings_refuse_an_unknown_sampler() -> None:
    with pytest.raises(ValueError, match="TOPICFORGE_ROS_SAMPLER"):
        load_settings({"TOPICFORGE_ROS_SAMPLER": "fast"})


# ---- factory and health ----


def _settings(**changes: Any) -> Settings:
    base: dict[str, Any] = {
        "mode": "live",
        "log_level": "INFO",
        "ros2_executable": "ros2",
        "telemetry_enabled": False,
    }
    return Settings(**{**base, **changes})


def _probe_recorder(
    monkeypatch: pytest.MonkeyPatch, backend: SamplingBackend
) -> Callable[[], list[tuple[Any, ...]]]:
    calls: list[tuple[Any, ...]] = []

    def choose(*args: Any) -> SamplingBackend:
        calls.append(args)
        return backend

    monkeypatch.setattr(factory, "choose_backend", choose)
    return lambda: calls


def test_the_factory_probes_once_and_hands_the_choice_to_the_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Ros2CliAdapter, "is_available", lambda _self: True)
    calls = _probe_recorder(monkeypatch, _AUTO)
    adapter = factory.build_adapter(_settings(ros_sampler="auto", ros_python="/p"))
    assert isinstance(adapter, Ros2CliAdapter)
    assert adapter.sampling == _AUTO
    assert calls() == [("auto", "ros2", "/p")]


def test_no_ros2_means_no_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Ros2CliAdapter, "is_available", lambda _self: False)
    calls = _probe_recorder(monkeypatch, _AUTO)
    assert isinstance(factory.build_adapter(_settings()), MockAdapter)
    assert calls() == []


def test_mock_mode_never_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _probe_recorder(monkeypatch, _AUTO)
    assert isinstance(factory.build_adapter(_settings(mode="mock")), MockAdapter)
    assert calls() == []


def test_is_available_does_not_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("is_available started a process")

    monkeypatch.setattr("subprocess.Popen", refuse)
    Ros2CliAdapter().is_available()


@pytest.mark.parametrize(
    ("backend", "expected"),
    [
        (SamplingBackend("cli", note="why"), ("cli", "why")),
        (_AUTO, ("rclpy", None)),
        (SamplingBackend("unavailable", note="why"), ("unavailable", "why")),
    ],
)
def test_health_reports_the_backend_and_ros_backend_stays(
    backend: SamplingBackend, expected: tuple[str, str | None]
) -> None:
    adapter = Ros2CliAdapter()
    adapter.sampling = backend
    report = HealthService(_settings(), adapter).report()
    assert (report.sampling_backend, report.sampling_backend_note) == expected
    assert report.ros_backend == "ros2_cli"


class _Dds:
    name = "cyclone"

    def is_available(self) -> bool:
        return True


def test_health_reads_it_through_the_composite() -> None:
    ros = Ros2CliAdapter()
    ros.sampling = SamplingBackend("cli", note="why")
    composite = CompositeAdapter(ros, _Dds())  # type: ignore[arg-type]
    report = HealthService(_settings(), composite).report()
    assert (report.sampling_backend, report.sampling_backend_note) == ("cli", "why")
    assert report.ros_backend == "ros2_cli"


def test_mock_reports_no_sampling_backend() -> None:
    report = HealthService(_settings(mode="mock"), MockAdapter()).report()
    assert report.sampling_backend is None and report.sampling_backend_note is None


def test_a_status_that_raises_does_not_break_health() -> None:
    class Broken(MockAdapter):
        def sampling_status(self) -> tuple[str, str | None]:
            raise RuntimeError("no")

    report = HealthService(_settings(), Broken()).report()
    assert report.sampling_backend is None


# ---- the helper's node is never one of the robot's ----


def _participant(guid: str, nodes: list[str], **changes: Any) -> ParticipantInfo:
    return ParticipantInfo(guid=guid, domain_id=0, vendor="fast", node_names=nodes, **changes)


@pytest.mark.parametrize(
    ("name", "pid", "sampler", "own"),
    [
        ("/_topicforge_sampler_42", 42, True, True),
        ("/ns/_topicforge_sampler_42", 42, True, True),
        ("/_ros2cli_42", 42, False, True),
        ("/_topicforge_sampler_x", None, False, False),
        ("/_ros2cli_daemon_0", None, False, False),
        ("/lidar", None, False, False),
    ],
)
def test_node_names(name: str, pid: int | None, sampler: bool, own: bool) -> None:
    assert cli_pid_of_node(name) == pid
    assert is_sampler_node(name) is sampler
    assert is_own_node(name) is own


def test_a_sampler_is_flagged_with_no_spawn_on_record_and_on_any_host() -> None:
    parts = [
        _participant("a.b.c.d", ["/_topicforge_sampler_9"], hostname="elsewhere"),
        _participant("e.f.g.h", ["/lidar"], hostname="elsewhere"),
    ]
    flagged = flag_topicforge_cli(parts, [], now_ns=10**18, local_host="here")
    assert [p.is_topicforge_cli for p in flagged] == [True, False]


def test_the_observer_is_never_flagged_and_cli_rules_are_unchanged() -> None:
    now = 10**18
    observer = _participant("o.b.s.r", ["/_topicforge_sampler_9"], is_observer=True)
    old_cli = _participant("a.b.c.d", ["/_ros2cli_7"])
    flagged = flag_topicforge_cli([observer, old_cli], [], now_ns=now)
    assert [p.is_topicforge_cli for p in flagged] == [False, False]
    fresh = flag_topicforge_cli([old_cli], [(7, now)], now_ns=now, local_host="here")
    assert fresh[0].is_topicforge_cli is True


def test_the_ghost_note_names_the_sampler() -> None:
    assert "sampler" in (cli_ghost_note(1) or "")
