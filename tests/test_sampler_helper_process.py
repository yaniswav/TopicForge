"""The sampler helper as a real process, on stand-ins for `rclpy` (no ROS 2 needed).

These pin the process contract: frames on a pipe, the stop by stdin EOF with a clean node
shutdown, the deadline on a silent topic, a dead parent, the probe. Whether a clean exit really
leaves the writers without a ghost reader takes a real DDS graph: see
`tests/integration/ros2/test_live_adapter.py`.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from topicforge.adapters.base import AdapterError
from topicforge.adapters.common.sampler_frames import KIND_HELLO, read_frame
from topicforge.adapters.ros2_live import Ros2CliAdapter
from topicforge.adapters.ros2_live.echo_stream import EchoDocument, EchoRun
from topicforge.adapters.ros2_live.helper_stream import (
    EXIT_STALLED,
    HELPER_LABEL,
    HELPER_PATH,
    SamplingBackend,
    choose_backend,
    helper_command,
    stream_helper,
)
from topicforge.models import TopicInfo

_FAKE = Path(__file__).parent / "fixtures" / "fake_rclpy"
_TYPE = "std_msgs/msg/String"


@pytest.fixture
def trace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Put the stand-ins on `PYTHONPATH`, as a sourced setup file does; returns their call log."""
    path = tmp_path / "trace.txt"
    monkeypatch.setenv("PYTHONPATH", str(_FAKE))
    monkeypatch.setenv("FAKE_RCLPY_TRACE", str(path))
    return path


def _cmd(*flags: str, deadline: float = 5.0, count: int = 1, topic: str = "/t") -> list[str]:
    return helper_command(
        sys.executable,
        topic,
        _TYPE,
        ["--count", str(count), "--deadline", str(deadline), *flags],
    )


def _lines(trace: Path) -> list[str]:
    return trace.read_text(encoding="utf-8").splitlines()


def test_count_messages_arrive_and_the_node_is_shut_down(
    trace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_RCLPY_MESSAGES", "9")
    run = stream_helper(_cmd(count=3), count=3, deadline_s=5.0)
    assert [d.text.splitlines()[0] for d in run.documents] == ["index: 0", "index: 1", "index: 2"]
    assert run.exit_code is None and run.tool == HELPER_LABEL
    assert all(d.received_ns > 0 for d in run.documents)
    assert run.started_ns <= run.documents[0].received_ns
    assert _lines(trace)[-2:] == ["destroy_node", "shutdown"]


def test_the_document_is_the_text_message_to_yaml_returned(
    trace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_RCLPY_MESSAGES", "1")
    run = stream_helper(_cmd("--truncate-length", "128"), count=1, deadline_s=5.0)
    assert run.documents[0].text == "index: 0\nvalues: [0, 0]\nlabel: café\n"


def test_a_silent_topic_honours_the_deadline_and_stops_clean(trace: Path) -> None:
    started = time.monotonic()
    run = stream_helper(_cmd(deadline=2.0, count=2), count=2, deadline_s=2.0)
    elapsed = time.monotonic() - started
    assert run.documents == [] and run.exit_code is None, run.stderr_tail
    assert 1.5 <= elapsed < 5.0, elapsed
    assert _lines(trace)[-2:] == ["destroy_node", "shutdown"]


def test_the_parent_deadline_before_the_helper_stops_it_by_stdin_eof(trace: Path) -> None:
    started = time.monotonic()
    run = stream_helper(_cmd(deadline=30.0), count=1, deadline_s=2.0)
    assert time.monotonic() - started < 5.0
    assert run.exit_code is None, run.stderr_tail
    assert _lines(trace)[-2:] == ["destroy_node", "shutdown"]


def test_a_dead_parent_ends_the_helper(trace: Path) -> None:
    proc = subprocess.Popen(
        _cmd(deadline=30.0), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    try:
        assert proc.stdout is not None
        frame = read_frame(proc.stdout)
        assert frame is not None and frame.kind == KIND_HELLO
        assert frame.meta["type"] == _TYPE  # no pid check: a Windows venv python is a launcher
        assert proc.stdin is not None
        proc.stdin.close()  # what the OS does when the server dies
        proc.stdin = None  # `communicate` flushes it otherwise, and it is closed
        assert proc.wait(timeout=5) == 4
    finally:
        proc.kill()
        proc.communicate()
    assert _lines(trace)[-2:] == ["destroy_node", "shutdown"]


def test_a_shutdown_that_blocks_ends_hard_within_the_grace(
    trace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_RCLPY_HANG_DESTROY", "30")
    started = time.monotonic()
    run = stream_helper(_cmd(deadline=30.0), count=1, deadline_s=0.3)
    assert time.monotonic() - started < 4.0
    assert run.documents == []
    assert "destroy_node" not in _lines(trace)


def test_what_a_native_library_prints_to_stdout_does_not_break_the_frames(
    trace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_RCLPY_MESSAGES", "1")
    monkeypatch.setenv("FAKE_RCLPY_NATIVE_STDOUT", "[RTPS_TRANSPORT_SHM Error] noise\n")
    run = stream_helper(_cmd(), count=1, deadline_s=5.0)
    assert len(run.documents) == 1 and run.exit_code is None


def test_setup_failure_exits_5_with_one_line(trace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_RCLPY_FAIL", "no security enclave")
    run = stream_helper(_cmd(), count=1, deadline_s=5.0)
    assert run.exit_code == 5
    assert run.stderr_tail == "RuntimeError: no security enclave"


def test_an_unknown_message_package_is_a_setup_failure_and_the_node_still_goes(trace: Path) -> None:
    cmd = helper_command(sys.executable, "/t", "missing_pkg/msg/X", ["--deadline", "5"])
    run = stream_helper(cmd, count=1, deadline_s=5.0)
    assert run.exit_code == 5
    assert "missing_pkg" in run.stderr_tail
    assert _lines(trace)[-2:] == ["destroy_node", "shutdown"]


def test_an_oversized_message_is_counted_not_sent(
    trace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_RCLPY_MESSAGES", "3")
    run = stream_helper(_cmd("--max-chars", "10", deadline=1.0), count=1, deadline_s=1.0)
    assert run.documents == []
    assert run.oversized == 3 and len(run.oversized_received_ns) == 3


def test_a_multibyte_message_at_the_cap_is_kept_like_the_cli_counts_characters(
    trace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_RCLPY_MESSAGES", "1")
    text = "index: 0\nvalues: [0, 0]\nlabel: café\n"
    run = stream_helper(
        _cmd("--max-chars", str(len(text)), "--truncate-length", "128"), count=1, deadline_s=5.0
    )
    assert [d.text for d in run.documents] == [text]


def test_a_large_frame_survives_the_pipe(trace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_RCLPY_MESSAGES", "2")
    run = stream_helper(_cmd("--full-length", count=2), count=2, deadline_s=5.0)
    assert len(run.documents) == 2
    assert len(run.documents[1].text) > 10000


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        ([], "reliability=best_effort durability=volatile depth=5"),
        (
            ["--qos-reliability", "reliable", "--qos-durability", "transient_local"],
            "reliability=reliable durability=transient_local depth=5",
        ),
        (
            ["--qos-history", "keep_last", "--qos-depth", "2000"],
            "reliability=best_effort durability=volatile depth=2000",
        ),
    ],
)
def test_qos_flags_reach_the_subscription(trace: Path, flags: list[str], expected: str) -> None:
    stream_helper(_cmd(*flags, deadline=1.0), count=1, deadline_s=1.0)
    assert f"subscription /t {expected}" in _lines(trace)


def test_the_node_options_and_the_empty_argv(trace: Path) -> None:
    stream_helper(_cmd(deadline=1.0), count=1, deadline_s=1.0)
    lines = _lines(trace)
    assert lines[0] == "init []"
    assert lines[1].startswith("node _topicforge_sampler_")
    for option in ("enable_rosout", "start_parameter_services", "use_global_arguments"):
        assert option in lines[1]


def test_the_command_has_no_isolated_mode_and_starts_by_path() -> None:
    cmd = helper_command("/usr/bin/python3", "/scan", _TYPE, ["--deadline", "1"])
    assert cmd[:2] == ["/usr/bin/python3", str(HELPER_PATH)]
    assert "-I" not in cmd
    assert cmd[-1] == "/scan"
    assert Path(cmd[1]).is_file()


def test_a_missing_interpreter_is_an_adapter_error() -> None:
    cmd = helper_command(str(Path("no") / "such" / "python"), "/t", _TYPE, ["--deadline", "1"])
    with pytest.raises(AdapterError, match="could not start the sampling helper"):
        stream_helper(cmd, count=1, deadline_s=1.0)


# ---- the probe, for real on the stand-ins ----


@pytest.fixture
def no_ros2(monkeypatch: pytest.MonkeyPatch) -> None:
    """`ros2` does not resolve, so the choice is only the override."""
    monkeypatch.setattr(
        "topicforge.adapters.ros2_live.helper_stream.shutil.which",
        lambda name: sys.executable if name == sys.executable else None,
    )


def test_the_probe_passes_with_rclpy_importable(trace: Path, no_ros2: None) -> None:
    for requested in ("auto", "rclpy"):
        backend = choose_backend(requested, "ros2", sys.executable)
        assert backend == SamplingBackend(
            "rclpy", python=sys.executable, fallback=requested == "auto"
        )


def test_the_probe_loads_the_native_part(
    trace: Path, no_ros2: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_RCLPY_NATIVE_MISSING", "1")
    auto = choose_backend("auto", "ros2", sys.executable)
    assert auto.kind == "cli" and auto.python is None
    assert "_rclpy_pybind11.cpython-312 isn't present" in (auto.note or "")
    required = choose_backend("rclpy", "ros2", sys.executable)
    assert required.kind == "unavailable"
    assert "TOPICFORGE_ROS_SAMPLER=rclpy" in (required.note or "")
    assert "_rclpy_pybind11.cpython-312 isn't present" in (required.note or "")


def test_the_probe_without_rclpy(no_ros2: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.setitem(os.environ, "PYTHONNOUSERSITE", "1")
    backend = choose_backend("auto", "ros2", sys.executable)
    assert backend.kind == "cli"
    assert "rclpy" in (backend.note or "")


def test_a_failed_probe_says_it_runs_only_once(
    trace: Path, no_ros2: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_RCLPY_NATIVE_MISSING", "1")
    for requested in ("auto", "rclpy"):
        note = choose_backend(requested, "ros2", sys.executable).note or ""
        assert "Checked once at startup: restart the server" in note


def test_cli_is_not_probed(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("no probe for cli")

    monkeypatch.setattr("topicforge.adapters.ros2_live.helper_stream.run_process", refuse)
    assert choose_backend("cli", "ros2", None).kind == "cli"


def test_a_missing_interpreter_fails_the_probe(no_ros2: None) -> None:
    missing = str(Path("no") / "such" / "python")
    backend = choose_backend("auto", "ros2", missing)
    assert backend.kind == "cli"
    assert missing in (backend.note or "")


def test_no_interpreter_at_all(no_ros2: None) -> None:
    backend = choose_backend("rclpy", "ros2", None)
    assert backend.kind == "unavailable"
    assert "TOPICFORGE_ROS_PYTHON" in (backend.note or "")


# ---- through the adapter, with the real decoding ----


def test_the_adapter_decodes_what_a_real_helper_process_sends(
    trace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_RCLPY_MESSAGES", "2")
    adapter = Ros2CliAdapter()
    adapter.sampling = SamplingBackend("rclpy", python=sys.executable)
    info = TopicInfo(
        name="/t",
        message_type=_TYPE,
        publisher_count=1,
        subscriber_count=0,
        mode_effective="live",
    )
    monkeypatch.setattr(adapter, "_topic_info", lambda _topic, _timeout: info)
    monkeypatch.setattr(adapter, "_resolve_exe", lambda: "ros2")
    result = adapter.sample_messages("/t", 2, timeout_s=5)
    assert result.count == 2 and result.note is None
    assert [s.payload["index"] for s in result.samples] == [0, 1]
    assert result.samples[0].payload["label"] == "caf\u00e9"
    assert result.rate is not None
    assert _lines(trace)[-2:] == ["destroy_node", "shutdown"]


# ---- a helper that hangs before subscribing is not a silent topic ----


def _live_adapter(monkeypatch: pytest.MonkeyPatch, *, fallback: bool) -> Ros2CliAdapter:
    adapter = Ros2CliAdapter()
    adapter.sampling = SamplingBackend("rclpy", python=sys.executable, fallback=fallback)
    info = TopicInfo(
        name="/t",
        message_type=_TYPE,
        publisher_count=1,
        subscriber_count=0,
        mode_effective="live",
    )
    monkeypatch.setattr(adapter, "_topic_info", lambda _topic, _timeout: info)
    monkeypatch.setattr(adapter, "_resolve_exe", lambda: "ros2")
    return adapter


def test_a_hang_in_init_is_a_stall_within_the_bound(
    trace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_RCLPY_HANG_INIT", "30")
    started = time.monotonic()
    run = stream_helper(_cmd(deadline=30.0), count=1, deadline_s=20.0, hello_s=1.0)
    assert time.monotonic() - started < 5.0
    assert run.exit_code == EXIT_STALLED
    assert "stalled before subscribing" in run.stderr_tail


def test_a_helper_past_its_own_deadline_without_hello_is_a_stall(
    trace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_RCLPY_HANG_INIT", "30")
    run = stream_helper(_cmd(deadline=0.1), count=1, deadline_s=20.0)
    assert run.exit_code == EXIT_STALLED


def test_rclpy_raises_the_stall_and_never_says_silent_topic(
    trace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_RCLPY_HANG_INIT", "30")
    adapter = _live_adapter(monkeypatch, fallback=False)
    with pytest.raises(AdapterError, match="stalled before subscribing") as caught:
        adapter.sample_messages("/t", 1, timeout_s=1.5)
    assert "exit 71" not in str(caught.value)
    assert "A publisher exists" not in str(caught.value)


def test_auto_gives_the_cli_real_time_after_a_stall_and_says_so(
    trace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_RCLPY_HANG_INIT", "30")
    seen: dict[str, float] = {}

    def echo(_cmd: list[str], **kwargs: float) -> EchoRun:
        seen["deadline_s"] = kwargs["deadline_s"]
        return EchoRun(documents=[EchoDocument("data: x\n", 5)], started_ns=1, ended_ns=9)

    monkeypatch.setattr("topicforge.adapters.ros2_live.sampler.stream_echo", echo)
    adapter = _live_adapter(monkeypatch, fallback=True)
    result = adapter.sample_messages("/t", 1, timeout_s=9)
    assert seen["deadline_s"] > 3.0
    assert result.count == 1
    assert (result.note or "").startswith("Sampled with the `ros2` CLI for this call:")
    assert "stalled before subscribing" in (result.note or "")
