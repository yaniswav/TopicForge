"""Subprocess error-path coverage for the live adapter.

These tests stub `run_process` (and `shutil.which`) so they exercise the
error translation logic in `Ros2CliAdapter._run` without needing a real
ROS2 install. They complement `test_live_adapter_parse.py`, which covers
the pure parsers.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from topicforge.adapters.base import AdapterError
from topicforge.adapters.ros2_live.adapter import Ros2CliAdapter
from topicforge.adapters.ros2_live.echo_stream import EchoDocument, EchoRun

_MODULE = "topicforge.adapters.ros2_live.adapter"


def _stub_which_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(f"{_MODULE}.shutil.which", lambda name: f"/fake/bin/{name}")


def _stub_run(monkeypatch: pytest.MonkeyPatch, behavior) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(f"{_MODULE}.run_process", behavior)


def test_run_raises_when_executable_not_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(f"{_MODULE}.shutil.which", lambda name: None)
    adapter = Ros2CliAdapter()
    with pytest.raises(AdapterError, match="not found on PATH"):
        adapter.list_topics()


def test_run_translates_filenotfound_to_adapter_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_which_resolves(monkeypatch)

    def raise_fnf(*_args: object, **_kwargs: object) -> object:
        raise FileNotFoundError("vanished mid-call")

    _stub_run(monkeypatch, raise_fnf)
    adapter = Ros2CliAdapter()
    with pytest.raises(AdapterError, match="not found on PATH"):
        adapter.list_topics()


def test_run_translates_timeout_to_adapter_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_which_resolves(monkeypatch)

    def raise_timeout(cmd: list[str], **kwargs: object) -> object:
        return SimpleNamespace(timed_out=True, returncode=None, stdout="", stderr="")

    _stub_run(monkeypatch, raise_timeout)
    adapter = Ros2CliAdapter()
    with pytest.raises(AdapterError, match="timed out"):
        adapter.list_topics()


def test_run_translates_nonzero_exit_with_stderr_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_which_resolves(monkeypatch)
    result = SimpleNamespace(
        timed_out=False,
        returncode=1,
        stdout="",
        stderr="warming up\nfatal: rmw not initialized\n",
    )
    _stub_run(monkeypatch, lambda *_a, **_kw: result)

    adapter = Ros2CliAdapter()
    with pytest.raises(AdapterError, match=r"exit 1.*rmw not initialized"):
        adapter.list_topics()


def test_run_translates_nonzero_exit_without_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_which_resolves(monkeypatch)
    result = SimpleNamespace(timed_out=False, returncode=2, stdout="", stderr="")
    _stub_run(monkeypatch, lambda *_a, **_kw: result)

    adapter = Ros2CliAdapter()
    with pytest.raises(AdapterError, match=r"exit 2.*no stderr"):
        adapter.list_topics()


def test_analyze_bag_raises_for_missing_path(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Path-existence check fires before any subprocess invocation."""
    missing = tmp_path / "does_not_exist.mcap"
    adapter = Ros2CliAdapter()
    with pytest.raises(AdapterError, match="Bag path does not exist"):
        adapter.analyze_bag(str(missing))


def _stub_echo(monkeypatch: pytest.MonkeyPatch, run: EchoRun) -> list[list[str]]:
    """Replace the echo stream with a canned run; returns the commands it was given."""
    seen: list[list[str]] = []

    def fake_stream(cmd: list[str], **_kw: object) -> EchoRun:
        seen.append(cmd)
        return run

    monkeypatch.setattr(f"{_MODULE}.stream_echo", fake_stream)
    return seen


def _doc(text: str, received_ns: int = 1) -> EchoDocument:
    return EchoDocument(text, received_ns)


_IMU_INFO = "Type: sensor_msgs/msg/Imu\nPublisher count: 1\nSubscription count: 0\n"
_IMU_DOC = "header:\n  stamp:\n    sec: 1715600000\n    nanosec: 123456789\n  frame_id: base_link\n"


def test_sample_messages_without_a_message_explains_the_silence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_which_resolves(monkeypatch)
    _stub_run(
        monkeypatch,
        lambda *_a, **_k: SimpleNamespace(
            timed_out=False,
            returncode=0,
            stdout="Type: geometry_msgs/msg/Twist\nPublisher count: 1\n",
            stderr="",
        ),
    )
    _stub_echo(monkeypatch, EchoRun())
    result = Ros2CliAdapter().sample_messages("/cmd_vel", count=5)
    assert result.samples == [] and result.count == 0
    assert result.note is not None and "0 of 5 messages within 10 s" in result.note
    assert "publisher exists" in result.note


def test_sample_messages_without_a_publisher_says_so_and_waits_less(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_which_resolves(monkeypatch)
    _stub_run(
        monkeypatch,
        lambda *_a, **_k: SimpleNamespace(
            timed_out=False,
            returncode=0,
            stdout="Type: geometry_msgs/msg/Twist\nPublisher count: 0\n",
            stderr="",
        ),
    )
    deadlines: list[float] = []

    def fake_stream(cmd: list[str], *, deadline_s: float, **_kw: object) -> EchoRun:
        deadlines.append(deadline_s)
        return EchoRun()

    monkeypatch.setattr(f"{_MODULE}.stream_echo", fake_stream)
    result = Ros2CliAdapter().sample_messages("/cmd_vel", count=2, timeout_s=30)
    assert len(deadlines) == 1 and 2.5 < deadlines[0] <= 3.0
    assert result.note is not None and "No publisher" in result.note
    assert "within 3 s" in result.note


def test_sample_messages_surfaces_a_cli_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_which_resolves(monkeypatch)
    _stub_run(
        monkeypatch,
        lambda *_a, **_k: SimpleNamespace(
            timed_out=False, returncode=0, stdout=_IMU_INFO, stderr=""
        ),
    )
    _stub_echo(monkeypatch, EchoRun(exit_code=1, stderr_tail="rcl not initialized"))
    with pytest.raises(AdapterError, match=r"exit 1.*rcl not initialized"):
        Ros2CliAdapter().sample_messages("/imu", count=1)


def test_sample_messages_streams_yaml_and_extracts_the_header_stamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_which_resolves(monkeypatch)
    _stub_run(
        monkeypatch,
        lambda *_a, **_k: SimpleNamespace(
            timed_out=False, returncode=0, stdout=_IMU_INFO, stderr=""
        ),
    )
    seen = _stub_echo(monkeypatch, EchoRun(documents=[_doc(_IMU_DOC, 42)]))
    result = Ros2CliAdapter().sample_messages("/imu", count=1)

    assert seen[0][:4] == ["/fake/bin/ros2", "topic", "echo", "--no-lost-messages"]
    assert result.count == 1 and result.note is None and result.mode_effective == "live"
    sample = result.samples[0]
    assert sample.timestamp_ns == 1715600000 * 1_000_000_000 + 123456789
    assert sample.stamp_source == "header"
    assert sample.received_ns == 42
    assert sample.message_type == "sensor_msgs/msg/Imu"
    assert sample.payload["header"] == {
        "stamp": {"sec": 1715600000, "nanosec": 123456789},
        "frame_id": "base_link",
    }


def test_sample_messages_count_zero_validates_the_topic_without_starting_the_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_which_resolves(monkeypatch)
    _stub_run(
        monkeypatch,
        lambda *_a, **_k: SimpleNamespace(
            timed_out=False, returncode=0, stdout=_IMU_INFO, stderr=""
        ),
    )
    seen = _stub_echo(monkeypatch, EchoRun())
    result = Ros2CliAdapter().sample_messages("/imu", count=0)
    assert result.count == 0 and result.note is None and seen == []


def test_sample_messages_count_zero_on_an_unknown_topic_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_which_resolves(monkeypatch)
    _stub_run(
        monkeypatch,
        lambda *_a, **_k: SimpleNamespace(timed_out=False, returncode=0, stdout="", stderr=""),
    )
    with pytest.raises(AdapterError, match="not found"):
        Ros2CliAdapter().sample_messages("/nope", count=0)


def test_list_topics_safe_counts_default_to_zero_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing `topic info` for one topic must not break `list_topics`."""
    _stub_which_resolves(monkeypatch)
    list_stdout = "/cmd_vel [geometry_msgs/msg/Twist]\n"
    failing = SimpleNamespace(timed_out=False, returncode=1, stdout="", stderr="boom\n")
    success = SimpleNamespace(timed_out=False, returncode=0, stdout=list_stdout, stderr="")
    call_count = {"n": 0}

    def run_stub(*_a: object, **_kw: object) -> object:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return success  # `topic list -t`
        return failing  # `topic info <name>`: fails

    _stub_run(monkeypatch, run_stub)
    adapter = Ros2CliAdapter()
    topics = adapter.list_topics()
    assert len(topics) == 1
    assert topics[0].publisher_count == 0
    assert topics[0].subscriber_count == 0
    assert topics[0].mode_effective == "live"


def test_effective_mode_is_live() -> None:
    """The live adapter declares `effective_mode == "live"` regardless of
    whether `ros2` is actually installed: the property is a static contract,
    not a runtime probe."""
    assert Ros2CliAdapter().effective_mode == "live"


def test_get_topic_info_marks_response_as_live(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_which_resolves(monkeypatch)
    info_stdout = "Type: geometry_msgs/msg/Twist\nPublisher count: 2\nSubscription count: 3\n"
    _stub_run(
        monkeypatch,
        lambda *_a, **_kw: SimpleNamespace(
            timed_out=False, returncode=0, stdout=info_stdout, stderr=""
        ),
    )

    info = Ros2CliAdapter().get_topic_info("/cmd_vel")
    assert info.message_type == "geometry_msgs/msg/Twist"
    assert info.mode_effective == "live"


def test_analyze_bag_marks_response_as_live(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    _stub_which_resolves(monkeypatch)
    bag = tmp_path / "demo.mcap"
    bag.write_bytes(b"")  # existence check only
    bag_stdout = "Storage id: mcap\nDuration: 1.000s\nMessages: 3\n"
    _stub_run(
        monkeypatch,
        lambda *_a, **_kw: SimpleNamespace(
            timed_out=False, returncode=0, stdout=bag_stdout, stderr=""
        ),
    )

    result = Ros2CliAdapter().analyze_bag(str(bag))
    assert result.mode_effective == "live"


def test_sample_messages_envelope_marks_response_as_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through the Inspector, sample_messages returns a SampleResult
    envelope whose `mode_effective` matches the adapter's `effective_mode`."""
    from topicforge.services import Inspector

    _stub_which_resolves(monkeypatch)
    _stub_run(
        monkeypatch,
        lambda *_a, **_k: SimpleNamespace(
            timed_out=False, returncode=0, stdout=_IMU_INFO, stderr=""
        ),
    )
    _stub_echo(monkeypatch, EchoRun(documents=[_doc(_IMU_DOC)]))
    result = Inspector(Ros2CliAdapter()).sample_messages("/imu", count=1)
    assert result.mode_effective == "live"
    assert result.count == 1
    assert result.samples[0].message_type == "sensor_msgs/msg/Imu"
