"""Ros2CliAdapter against a real ROS 2 graph (runs inside the bench container).

Deselected on the host by `-m "not integration"`; skipped when `ros2` is not
on PATH.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

from topicforge.adapters.base import AdapterError
from topicforge.adapters.ros2_live import Ros2CliAdapter

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("ros2") is None, reason="ros2 not on PATH"),
]

N_BEAMS = 541
BAG_PATH = Path(os.environ.get("TOPICFORGE_BENCH_BAG", "/bench/bag"))
IS_HUMBLE = os.environ.get("ROS_DISTRO") == "humble"
# Header stamps come from a simulated clock that starts at 0.
MAX_SIM_NS = 600 * 1_000_000_000


@pytest.fixture(scope="module")
def adapter() -> Ros2CliAdapter:
    return Ros2CliAdapter()


def _echo_processes() -> list[str]:
    """Command lines of running `ros2 topic echo` processes (Linux `/proc`)."""
    found = []
    for cmdline in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            text = cmdline.read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        if "topic echo" in text and "pytest" not in text:
            found.append(text)
    return found


def _ranges(payload: dict[str, object]) -> list[float]:
    """The `ranges` field of a sample payload as floats (`inf` and `nan` come as strings)."""
    ranges = payload["ranges"]
    assert isinstance(ranges, list), ranges
    return [float(v) for v in ranges]


def test_list_topics_reports_types_and_counts(adapter: Ros2CliAdapter) -> None:
    topics = {t.name: t for t in adapter.list_topics()}
    assert topics["/scan"].message_type == "sensor_msgs/msg/LaserScan"
    assert topics["/scan"].publisher_count == 1
    assert topics["/cmd_vel_out"].message_type == "geometry_msgs/msg/Twist"
    assert topics["/camera/image_raw"].message_type == "sensor_msgs/msg/Image"
    assert topics["/robot_description_lite"].message_type == "std_msgs/msg/String"
    assert topics["/clock"].message_type == "rosgraph_msgs/msg/Clock"


def test_get_topic_info_counts(adapter: Ros2CliAdapter) -> None:
    info = adapter.get_topic_info("/scan")
    assert info.message_type == "sensor_msgs/msg/LaserScan"
    assert info.publisher_count == 1


def test_get_topic_info_reports_qos_reliability(adapter: Ros2CliAdapter) -> None:
    qos = adapter.get_topic_info("/scan").publisher_qos
    assert qos is not None and qos.reliability == "reliable"


def test_get_topic_info_reports_latched_durability(adapter: Ros2CliAdapter) -> None:
    qos = adapter.get_topic_info("/robot_description_lite").publisher_qos
    assert qos is not None and qos.durability == "transient_local"


def test_get_topic_info_unknown_topic_raises(adapter: Ros2CliAdapter) -> None:
    with pytest.raises(AdapterError):
        adapter.get_topic_info("/does/not/exist")


def test_sample_scan_returns_a_message(adapter: Ros2CliAdapter) -> None:
    result = adapter.sample_messages("/scan", 1)
    assert result.count == 1 and result.note is None and result.mode_effective == "live"
    assert result.samples[0].message_type == "sensor_msgs/msg/LaserScan"


def test_sample_scan_delivers_all_541_ranges(adapter: Ros2CliAdapter) -> None:
    sample = adapter.sample_messages("/scan", 1, max_array_length=None).samples[0]
    ranges = _ranges(sample.payload)
    assert len(ranges) == N_BEAMS
    assert ranges[270] == pytest.approx(1.27, abs=1e-4)
    assert ranges[540] == pytest.approx(1.54, abs=1e-4)
    assert "_truncated_fields" not in sample.payload


def test_payload_has_named_nested_fields(adapter: Ros2CliAdapter) -> None:
    payload = adapter.sample_messages("/scan", 1).samples[0].payload
    assert payload["header"]["frame_id"] == "base_laser"  # type: ignore[index]
    assert payload["range_max"] == pytest.approx(30.0)
    assert not any(key.startswith("col_") for key in payload)
    assert "_raw_text" not in payload


def test_arrays_summary_only_replaces_arrays_by_a_summary(adapter: Ros2CliAdapter) -> None:
    payload = adapter.sample_messages("/scan", 1, arrays_summary_only=True).samples[0].payload
    assert payload["ranges"] == f"<sequence type: float, length: {N_BEAMS}>"
    assert payload["intensities"] == f"<sequence type: float, length: {N_BEAMS}>"
    assert payload["header"]["frame_id"] == "base_laser"  # type: ignore[index]
    assert payload["range_max"] == pytest.approx(30.0)


def test_default_array_cut_is_listed_in_the_payload(adapter: Ros2CliAdapter) -> None:
    payload = adapter.sample_messages("/scan", 1).samples[0].payload
    assert payload["_truncated_fields"] == ["ranges", "intensities"]
    assert len(_ranges(payload)) == 128


def test_custom_array_cut(adapter: Ros2CliAdapter) -> None:
    payload = adapter.sample_messages("/scan", 1, max_array_length=10).samples[0].payload
    assert len(_ranges(payload)) == 10
    assert "ranges" in payload["_truncated_fields"]  # type: ignore[operator]


def test_sample_scan_honours_count(adapter: Ros2CliAdapter) -> None:
    result = adapter.sample_messages("/scan", 3)
    assert result.count == 3 and result.note is None


def test_sample_five_messages_within_the_default_deadline(adapter: Ros2CliAdapter) -> None:
    started = time.monotonic()
    result = adapter.sample_messages("/scan", 5)
    assert result.count == 5 and result.note is None
    assert time.monotonic() - started < 10
    stamps = [s.timestamp_ns for s in result.samples]
    assert stamps == sorted(stamps) and len(set(stamps)) == 5


def test_sample_scan_timestamp_from_header_on_sim_time(adapter: Ros2CliAdapter) -> None:
    sample = adapter.sample_messages("/scan", 1).samples[0]
    assert 0 < sample.timestamp_ns < MAX_SIM_NS
    assert sample.stamp_source == "header"


def test_received_ns_is_the_wall_clock(adapter: Ros2CliAdapter) -> None:
    before = time.time_ns()
    sample = adapter.sample_messages("/scan", 1).samples[0]
    assert sample.received_ns is not None
    assert before <= sample.received_ns <= time.time_ns()


def test_timeout_returns_the_partial_result_with_a_note(adapter: Ros2CliAdapter) -> None:
    # The camera publishes at 2 Hz: 50 messages cannot arrive in 3 s.
    started = time.monotonic()
    result = adapter.sample_messages("/camera/image_raw", 50, arrays_summary_only=True, timeout_s=3)
    elapsed = time.monotonic() - started
    assert elapsed < 8, "the echo process must be killed at the deadline"
    assert 0 <= result.count < 50
    assert result.note is not None and f"{result.count} of 50 messages within 3 s" in result.note
    assert "publisher exists" in result.note


def test_no_echo_process_is_left_behind(adapter: Ros2CliAdapter) -> None:
    adapter.sample_messages("/scan", 2)
    adapter.sample_messages("/camera/image_raw", 50, arrays_summary_only=True, timeout_s=2)
    time.sleep(0.5)
    assert not _echo_processes()


def test_sample_latched_topic(adapter: Ros2CliAdapter) -> None:
    result = adapter.sample_messages("/robot_description_lite", 1)
    assert result.count == 1 and result.note is None
    assert result.samples[0].payload == {"data": "bench_robot: differential drive, 2D lidar"}


def test_sample_latched_topic_uses_explicit_qos(adapter: Ros2CliAdapter) -> None:
    """A latched message is only replayed to a transient_local reader, so getting it proves the QoS."""
    for _ in range(3):
        assert adapter.sample_messages("/robot_description_lite", 1).count == 1


def test_sample_headerless_twist(adapter: Ros2CliAdapter) -> None:
    sample = adapter.sample_messages("/cmd_vel_out", 1).samples[0]
    assert sample.timestamp_ns == 0 and sample.stamp_source == "none"
    assert sample.payload["linear"]["x"] == pytest.approx(0.25)  # type: ignore[index]
    assert sample.payload["angular"]["z"] == pytest.approx(0.5)  # type: ignore[index]


def test_sample_twist_honours_count(adapter: Ros2CliAdapter) -> None:
    assert adapter.sample_messages("/cmd_vel_out", 4).count == 4


def test_sample_nan_and_inf_come_as_strings(adapter: Ros2CliAdapter) -> None:
    sample = adapter.sample_messages("/scan_edge", 1).samples[0]
    assert sample.payload["ranges"] == ["inf", "nan", "-inf", 1.5]


def test_sample_large_image(adapter: Ros2CliAdapter) -> None:
    result = adapter.sample_messages("/camera/image_raw", 1, arrays_summary_only=True)
    assert result.count == 1
    sample = result.samples[0]
    assert sample.message_type == "sensor_msgs/msg/Image"
    assert sample.payload["width"] == 640 and sample.payload["height"] == 480
    assert sample.payload["data"] == "<sequence type: uint8, length: 921600>"


def test_sample_unknown_topic_raises(adapter: Ros2CliAdapter) -> None:
    with pytest.raises(AdapterError):
        adapter.sample_messages("/does/not/exist", 1)


@pytest.fixture(scope="module")
def bag() -> Path:
    assert BAG_PATH.exists(), f"bench bag missing: {BAG_PATH}"
    return BAG_PATH


def test_analyze_bag_summary(adapter: Ros2CliAdapter, bag: Path) -> None:
    result = adapter.analyze_bag(str(bag))
    assert result.duration_s == pytest.approx(8.0, abs=2.0)
    by_name = {t.name: t for t in result.topics}
    assert set(by_name) >= {"/clock", "/scan", "/cmd_vel_out", "/camera/image_raw"}
    assert by_name["/scan"].message_type == "sensor_msgs/msg/LaserScan"
    assert 60 <= by_name["/scan"].message_count <= 100
    assert result.storage_format == ("sqlite3" if IS_HUMBLE else "mcap")


def test_analyze_bag_frequency_of_full_span_topic(adapter: Ros2CliAdapter, bag: Path) -> None:
    by_name = {t.name: t for t in adapter.analyze_bag(str(bag)).topics}
    assert by_name["/scan"].frequency_hz == pytest.approx(10.0, rel=0.2)


def test_analyze_bag_frequency_of_late_topic(adapter: Ros2CliAdapter, bag: Path) -> None:
    # The camera starts several seconds into the recording at 2 Hz.
    by_name = {t.name: t for t in adapter.analyze_bag(str(bag)).topics}
    assert by_name["/camera/image_raw"].frequency_hz == pytest.approx(2.0, rel=0.2)


def test_analyze_bag_reports_topic_span_basis_and_latching(
    adapter: Ros2CliAdapter, bag: Path
) -> None:
    by_name = {t.name: t for t in adapter.analyze_bag(str(bag)).topics}
    assert by_name["/scan"].frequency_basis == "topic_span"
    assert by_name["/scan"].latched is False
    assert by_name["/robot_description_lite"].latched is True


def test_peek_bag_samples_decodes_scan(adapter: Ros2CliAdapter, bag: Path) -> None:
    result = adapter.peek_bag_samples(str(bag), "/scan", 2)
    assert result.count == 2
    assert result.samples[0].message_type == "sensor_msgs/msg/LaserScan"
    payload = result.samples[0].payload
    ranges = _ranges(payload)
    assert len(ranges) == N_BEAMS, str(payload)[:400]
    assert ranges[270] == pytest.approx(1.27, abs=1e-4)


def test_peek_bag_samples_decodes_string(adapter: Ros2CliAdapter, bag: Path) -> None:
    result = adapter.peek_bag_samples(str(bag), "/robot_description_lite", 1)
    assert result.count == 1
    assert "bench_robot" in str(result.samples[0].payload)


def test_the_whole_call_stays_within_timeout_plus_two_seconds(adapter: Ros2CliAdapter) -> None:
    started = time.monotonic()
    adapter.sample_messages("/camera/image_raw", 50, arrays_summary_only=True, timeout_s=3)
    assert time.monotonic() - started < 3 + 2.5


def test_a_message_over_the_cap_is_dropped_with_a_note() -> None:
    small_cap = Ros2CliAdapter(max_message_chars=100_000)
    result = small_cap.sample_messages("/camera/image_raw", 1, max_array_length=65536, timeout_s=8)
    assert result.count == 0
    assert result.note is not None and "were dropped" in result.note


def test_a_short_latched_result_hints_count_one(adapter: Ros2CliAdapter) -> None:
    result = adapter.sample_messages("/robot_description_lite", 3, timeout_s=3)
    assert result.count == 1
    assert result.note is not None and "fewer than requested" in result.note
    assert "latched" in result.note


def test_peek_bag_samples_names_the_clock_of_each_timestamp(
    adapter: Ros2CliAdapter, bag: Path
) -> None:
    scan = adapter.peek_bag_samples(str(bag), "/scan", 1).samples[0]
    assert scan.stamp_source == "header"
    assert scan.recorded_ns is not None
    assert "__msgtype__" not in str(scan.payload) and "_msgtype" not in str(scan.payload)

    text = adapter.peek_bag_samples(str(bag), "/robot_description_lite", 1).samples[0]
    assert text.stamp_source == "recorded"
    assert text.timestamp_ns == text.recorded_ns


def test_health_reports_the_sim_clock_publisher(adapter: Ros2CliAdapter) -> None:
    from topicforge.config import Settings
    from topicforge.services import HealthService

    assert adapter.sim_clock_published() is True
    settings = Settings(
        mode="live", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False
    )
    assert HealthService(settings, adapter).report().sim_clock_published is True


# ---- summaries and rate (bench publisher: /scan 10 Hz, /cmd_vel_out 5 Hz, edge 2 Hz) ----


def test_scan_summary_reads_all_beams_while_the_payload_is_cut(adapter: Ros2CliAdapter) -> None:
    sample = adapter.sample_messages("/scan", 1).samples[0]
    assert len(_ranges(sample.payload)) == 128  # the default cut, as before
    summary = sample.summary
    assert summary is not None and summary.summary_type == "laser_scan"
    assert summary.beam_count == N_BEAMS and summary.frame_id == "base_laser"
    assert summary.finite_count == N_BEAMS and summary.inf_count == 0
    # ranges are 1.0 + i * 0.001. Index 0 (bearing -135 deg) is inside the 128 beams that the
    # payload keeps; the front sector minimum, at index 180, is past the cut.
    assert summary.closest_obstacle is not None
    assert summary.closest_obstacle.range == pytest.approx(1.0, abs=1e-4)
    assert summary.closest_obstacle.beam_index == 0
    front = summary.sectors.front.closest
    assert front is not None and front.beam_index == 180
    assert front.range == pytest.approx(1.18, abs=1e-4)
    assert summary.sectors.rear.beam_count == 0  # the scan spans exactly +-135 degrees


def test_scan_edge_summary_counts_nan_and_inf(adapter: Ros2CliAdapter) -> None:
    sample = adapter.sample_messages("/scan_edge", 1).samples[0]
    assert sample.payload["ranges"] == ["inf", "nan", "-inf", 1.5]
    summary = sample.summary
    assert summary is not None and summary.summary_type == "laser_scan"
    assert (summary.finite_count, summary.inf_count) == (1, 1)
    assert (summary.neg_inf_count, summary.nan_count) == (1, 1)
    # The publisher sets no angle geometry: counts only, and the summary says so.
    assert summary.closest_obstacle is None and summary.note is not None


def test_image_summary_never_needs_the_pixels(adapter: Ros2CliAdapter) -> None:
    summary = adapter.sample_messages("/camera/image_raw", 1).samples[0].summary
    assert summary is not None and summary.summary_type == "image"
    assert (summary.width, summary.height, summary.encoding) == (640, 480, "rgb8")
    assert (summary.data_length, summary.data_length_basis) == (921600, "step_x_height")


def test_scan_rate_is_close_to_the_publish_rate(adapter: Ros2CliAdapter) -> None:
    rate = adapter.sample_messages("/scan", 20).rate
    assert rate is not None and rate.basis == "received_ns" and rate.message_count == 20
    assert rate.observed_frequency_hz == pytest.approx(10.0, rel=0.1)
    # docs/CONTRACT.md section 4: a healthy sensor should read `stable`; `jittery` here would
    # mean the 0.2 threshold needs to move to 0.3.
    assert rate.verdict in ("stable", "jittery")
    assert rate.interval_cv is not None and rate.interval_cv < 0.3
    assert rate.trailing_gap_s is None  # stopped on count
    # Sim time runs on the wall clock in the bench, so the two rates agree.
    assert rate.sim_frequency_hz == pytest.approx(rate.observed_frequency_hz, rel=0.1)


def test_twist_rate_is_stable_at_five_hertz(adapter: Ros2CliAdapter) -> None:
    rate = adapter.sample_messages("/cmd_vel_out", 20).rate
    assert rate is not None
    assert rate.observed_frequency_hz == pytest.approx(5.0, rel=0.1)
    assert rate.verdict == "stable"
    assert rate.sim_frequency_hz is None  # a Twist has no stamp


def test_slow_topic_stopped_by_the_deadline_has_a_trailing_gap(adapter: Ros2CliAdapter) -> None:
    # The camera publishes at 2 Hz: 50 messages cannot arrive in 3 s.
    rate = adapter.sample_messages("/camera/image_raw", 50, timeout_s=3).rate
    assert rate is not None and rate.message_count < 50
    if rate.message_count:  # the camera may not have started yet
        assert rate.trailing_gap_s is not None and rate.trailing_gap_s < 1.5
