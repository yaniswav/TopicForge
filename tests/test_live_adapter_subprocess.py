"""Subprocess error-path coverage for the live adapter.

These tests stub `subprocess.run` (and `shutil.which`) so they exercise the
error translation logic in `Ros2CliAdapter._run` without needing a real
ROS2 install. They complement `test_live_adapter_parse.py`, which covers
the pure parsers.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from topicforge.adapters.base import AdapterError
from topicforge.adapters.ros2_live.adapter import Ros2CliAdapter

_MODULE = "topicforge.adapters.ros2_live.adapter"


def _stub_which_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(f"{_MODULE}.shutil.which", lambda name: f"/fake/bin/{name}")


def _stub_run(monkeypatch: pytest.MonkeyPatch, behavior) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(f"{_MODULE}.subprocess.run", behavior)


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
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=float(kwargs.get("timeout", 8.0)))  # type: ignore[arg-type]

    _stub_run(monkeypatch, raise_timeout)
    adapter = Ros2CliAdapter()
    with pytest.raises(AdapterError, match="timed out"):
        adapter.list_topics()


def test_run_translates_nonzero_exit_with_stderr_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_which_resolves(monkeypatch)
    result = SimpleNamespace(
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
    result = SimpleNamespace(returncode=2, stdout="", stderr="")
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


def test_sample_messages_swallows_echo_timeout_and_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`sample_messages` is expected to degrade gracefully when echo times out."""
    _stub_which_resolves(monkeypatch)

    info_stdout = "Type: geometry_msgs/msg/Twist\nPublisher count: 1\nSubscription count: 0\n"
    call_count = {"n": 0}

    def run_stub(cmd: list[str], **kwargs: object) -> object:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return SimpleNamespace(returncode=0, stdout=info_stdout, stderr="")
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=float(kwargs.get("timeout", 3.0)))  # type: ignore[arg-type]

    _stub_run(monkeypatch, run_stub)
    adapter = Ros2CliAdapter()
    assert adapter.sample_messages("/cmd_vel", count=5) == []


def test_sample_messages_invokes_csv_echo_and_extracts_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`sample_messages` must shell out with `--csv --once` and surface the
    `header.stamp`-derived `timestamp_ns` from the parser."""
    _stub_which_resolves(monkeypatch)

    info_stdout = "Type: sensor_msgs/msg/Imu\nPublisher count: 1\nSubscription count: 0\n"
    csv_stdout = "1715600000,123456789,base_link,0.0,0.0,0.0,1.0\n"
    captured_cmds: list[list[str]] = []

    def run_stub(cmd: list[str], **_kwargs: object) -> object:
        captured_cmds.append(list(cmd))
        if "info" in cmd:
            return SimpleNamespace(returncode=0, stdout=info_stdout, stderr="")
        return SimpleNamespace(returncode=0, stdout=csv_stdout, stderr="")

    _stub_run(monkeypatch, run_stub)
    adapter = Ros2CliAdapter()
    samples = adapter.sample_messages("/imu", count=1)

    # The echo invocation carries `--csv --once` in that order (per adapter
    # wiring); flipping it would still work with ros2cli, but the assertion
    # pins the deliberate shape so a future refactor doesn't silently drop
    # the flag that makes timestamps available.
    echo_cmd = next(c for c in captured_cmds if "echo" in c)
    assert "--csv" in echo_cmd
    assert "--once" in echo_cmd
    assert echo_cmd[echo_cmd.index("--csv") + 1] == "--once"

    assert len(samples) == 1
    assert samples[0].timestamp_ns == 1715600000 * 1_000_000_000 + 123456789
    assert samples[0].message_type == "sensor_msgs/msg/Imu"
    assert samples[0].payload["col_0"] == "base_link"


def test_list_topics_safe_counts_default_to_zero_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing `topic info` for one topic must not break `list_topics`."""
    _stub_which_resolves(monkeypatch)
    list_stdout = "/cmd_vel [geometry_msgs/msg/Twist]\n"
    failing = SimpleNamespace(returncode=1, stdout="", stderr="boom\n")
    success = SimpleNamespace(returncode=0, stdout=list_stdout, stderr="")
    call_count = {"n": 0}

    def run_stub(*_a: object, **_kw: object) -> object:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return success  # `topic list -t`
        return failing  # `topic info <name>` — fails

    _stub_run(monkeypatch, run_stub)
    adapter = Ros2CliAdapter()
    topics = adapter.list_topics()
    assert len(topics) == 1
    assert topics[0].publisher_count == 0
    assert topics[0].subscriber_count == 0
