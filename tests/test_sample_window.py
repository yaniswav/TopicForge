"""`sample_messages` over a time window: the helper's stride, the rate, the refusals, the mock."""

from __future__ import annotations

import importlib.util
import itertools
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

from topicforge.adapters.base import AdapterError
from topicforge.adapters.common.sampler_frames import (
    KIND_ARRIVAL,
    KIND_HELLO,
    KIND_MESSAGE,
    Frame,
    encode_frame,
    read_frame,
)
from topicforge.adapters.ros2_live.echo_stream import EchoDocument, EchoRun
from topicforge.adapters.ros2_live.helper_stream import (
    HELPER_PATH,
    SamplingBackend,
    _add_frame,
    helper_command,
    stream_helper,
)
from topicforge.adapters.ros2_live.sampler import _keep_evenly, _rate, sample_messages
from topicforge.adapters.ros2_mock import MockAdapter
from topicforge.models import MessageSample, TopicInfo
from topicforge.services.inspector import Inspector

_FAKE = Path(__file__).parent / "fixtures" / "fake_rclpy"
_TYPE = "std_msgs/msg/String"


def _helper_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_sampler_helper_window", HELPER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fake_rclpy(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setenv("PYTHONPATH", str(_FAKE))
    return monkeypatch


def _info(topic: str, timeout: float) -> TopicInfo:
    return TopicInfo(
        name=topic, message_type=_TYPE, publisher_count=1, subscriber_count=0, mode_effective="live"
    )


def _no_exe() -> str:
    raise AssertionError("a window must not reach the CLI")


def _sample(backend: SamplingBackend, count: int, window_s: float, **kw: object) -> object:
    return sample_messages(
        "/t",
        count,
        topic_info=_info,
        resolve_exe=_no_exe,
        mode="live",
        max_message_chars=1_000_000,
        backend=backend,
        window_s=window_s,
        **kw,  # type: ignore[arg-type]
    )


_HELPER = SamplingBackend("rclpy", python=sys.executable)


# ----- the stride -----


def test_the_stride_sends_a_bounded_evenly_spaced_subset() -> None:
    stride = _helper_module().WindowStride(5)
    kept: list[int] = []
    most = 0
    for _ in range(100_000):
        index = stride.next_index()
        if index % stride.stride == 0:
            kept.append(index)
            stride.sent()
            kept = [k for k in kept if k % stride.stride == 0]
        most = max(most, len(kept))
    assert most <= 10
    assert len(kept) >= 5
    assert {b - a for a, b in itertools.pairwise(kept)} == {stride.stride}


def test_frames_thin_the_documents_and_keep_every_arrival() -> None:
    run = EchoRun(window=True)
    _add_frame(run, Frame(KIND_HELLO, {"wall_ns": 500}))
    assert run.started_ns == 500 and run.hello_mono_ns > 0
    for index in range(4):
        meta = {"wall_ns": 1000 + index, "encoding": "yaml", "index": index, "stride": 1}
        _add_frame(run, Frame(KIND_MESSAGE, meta, b"data: x\n"))
    _add_frame(run, Frame(KIND_ARRIVAL, {"wall_ns": 2000}))
    meta = {"wall_ns": 3000, "encoding": "yaml", "index": 4, "stride": 2}
    _add_frame(run, Frame(KIND_MESSAGE, meta, b"data: x\n"))
    assert [d.index for d in run.documents] == [0, 2, 4]
    assert sorted(run.unkept_received_ns) == [1001, 1003, 2000]


def test_a_hello_outside_a_window_leaves_the_span_alone() -> None:
    run = EchoRun(started_ns=7)
    _add_frame(run, Frame(KIND_HELLO, {"wall_ns": 500}))
    assert run.started_ns == 7 and run.hello_mono_ns == 0


def test_keep_evenly_keeps_both_ends_and_counts_the_rest() -> None:
    run = EchoRun(window=True, documents=[EchoDocument("x", i, i) for i in range(10)])
    _keep_evenly(run, 3)
    assert run.documents[0].index == 0 and run.documents[-1].index == 9
    assert len(run.documents) == 3 and len(run.unkept_received_ns) == 7


def test_the_arrival_frame_round_trips() -> None:
    import io

    frame = read_frame(io.BytesIO(encode_frame(KIND_ARRIVAL, {"wall_ns": 9})))
    assert frame is not None and frame.kind == KIND_ARRIVAL and frame.wall_ns == 9


# ----- the helper as a process -----


def test_the_helper_stays_for_the_window_and_reports_every_arrival(
    fake_rclpy: pytest.MonkeyPatch,
) -> None:
    fake_rclpy.setenv("FAKE_RCLPY_MESSAGES", "300")
    cmd = helper_command(
        sys.executable, "/t", _TYPE, ["--count", "5", "--window", "1.0", "--deadline", "4"]
    )
    started = time.monotonic()
    run = stream_helper(cmd, count=5, deadline_s=4.0, window=True)
    assert time.monotonic() - started >= 1.0
    assert run.exit_code is None, run.stderr_tail
    assert len(run.documents) + len(run.unkept_received_ns) == 300
    assert 5 <= len(run.documents) <= 10
    assert all(d.index % run.stride == 0 for d in run.documents)
    assert run.ended_ns > run.started_ns


def test_a_window_returns_count_samples_and_rates_all_that_arrived(
    fake_rclpy: pytest.MonkeyPatch,
) -> None:
    fake_rclpy.setenv("FAKE_RCLPY_MESSAGES", "200")
    result = _sample(_HELPER, 4, 1.0)
    assert result.count == 4  # type: ignore[attr-defined]
    assert result.rate.message_count == 200  # type: ignore[attr-defined]
    assert 0.9 <= result.rate.window_s < 3.0  # type: ignore[attr-defined]
    assert result.note.startswith("200 messages received over ")  # type: ignore[attr-defined]
    assert result.rate.trailing_gap_s is not None  # type: ignore[attr-defined]


def test_the_sim_rate_of_a_thinned_window_counts_what_arrived_between() -> None:
    docs = [EchoDocument("x", 5_000 + i, i * 10) for i in range(3)]
    run = EchoRun(window=True, documents=docs, started_ns=5_000, ended_ns=5_002)
    samples = [
        MessageSample(
            topic="/t",
            message_type=_TYPE,
            timestamp_ns=i * 1_000_000_000,
            stamp_source="header",
            received_ns=5_000 + i,
            payload={},
        )
        for i in range(3)
    ]
    assert _rate(run, 3, samples, 0).sim_frequency_hz == 10.0


def test_a_window_longer_than_timeout_s_still_runs_whole(fake_rclpy: pytest.MonkeyPatch) -> None:
    fake_rclpy.setenv("FAKE_RCLPY_MESSAGES", "3")
    started = time.monotonic()
    result = _sample(_HELPER, 3, 1.5, timeout_s=1.0)
    assert time.monotonic() - started >= 1.5
    assert result.rate.message_count == 3  # type: ignore[attr-defined]


def test_a_silent_window_says_so(fake_rclpy: pytest.MonkeyPatch) -> None:
    result = _sample(_HELPER, 3, 0.5)
    assert result.count == 0  # type: ignore[attr-defined]
    assert result.rate.verdict == "silent"  # type: ignore[attr-defined]
    assert result.note.startswith("0 messages received")  # type: ignore[attr-defined]


def test_a_failed_helper_is_an_error_even_with_a_fallback(fake_rclpy: pytest.MonkeyPatch) -> None:
    fake_rclpy.setenv("FAKE_RCLPY_FAIL", "boom")
    backend = SamplingBackend("rclpy", python=sys.executable, fallback=True)
    with pytest.raises(AdapterError, match="boom"):
        _sample(backend, 3, 0.5)


@pytest.mark.parametrize(
    "backend",
    [SamplingBackend("cli", note="TOPICFORGE_ROS_SAMPLER=cli"), SamplingBackend("cli")],
)
def test_the_cli_backend_refuses_a_window_naming_the_setting(backend: SamplingBackend) -> None:
    with pytest.raises(AdapterError, match="TOPICFORGE_ROS_SAMPLER"):
        _sample(backend, 3, 1.0)


def test_an_unavailable_helper_raises_its_reason() -> None:
    with pytest.raises(AdapterError, match="not here"):
        _sample(SamplingBackend("unavailable", note="not here"), 3, 1.0)


# ----- the service and the mock -----


@pytest.mark.parametrize("window_s", [0.1, 31.0, True])
def test_the_service_refuses_a_bad_window(window_s: float) -> None:
    with pytest.raises(AdapterError, match="window_s"):
        Inspector(MockAdapter()).sample_messages("/scan", 3, window_s=window_s)


def test_the_service_refuses_a_window_with_a_log_filter() -> None:
    with pytest.raises(AdapterError, match="does not combine"):
        Inspector(MockAdapter()).sample_messages("/rosout", 3, window_s=1.0, min_log_level="WARN")


def test_the_mock_window_is_deterministic_and_rates_the_whole_window() -> None:
    first = Inspector(MockAdapter()).sample_messages("/scan", 3, window_s=5.0)
    again = Inspector(MockAdapter()).sample_messages("/scan", 3, window_s=5.0)
    assert first == again
    assert first.rate is not None
    assert first.rate.message_count == 50
    assert first.rate.window_s == 5.0
    assert first.note and first.note.startswith("50 messages received over 5.00 s")
    times = [s.received_ns for s in first.samples]
    assert times == sorted(times)


def test_a_composite_and_the_dds_adapter_take_the_argument() -> None:
    import inspect

    from topicforge.adapters.base import MiddlewareAdapter
    from topicforge.adapters.composite import CompositeAdapter

    for cls in (MiddlewareAdapter, CompositeAdapter, MockAdapter):
        assert "window_s" in inspect.signature(cls.sample_messages).parameters
