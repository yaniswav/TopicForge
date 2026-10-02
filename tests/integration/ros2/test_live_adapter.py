"""Ros2CliAdapter against a real ROS 2 graph (runs inside the bench container).

Deselected on the host by `-m "not integration"`; skipped when `ros2` is not
on PATH. Assertions describe the correct behaviour: tests tagged `xfail`
name the bug that still breaks them.
"""

from __future__ import annotations

import os
import shutil
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


def _ranges(payload: dict[str, object]) -> list[float]:
    """Ranges from a sample payload, structured or flattened (`col_N`) alike."""
    ranges = payload.get("ranges")
    if isinstance(ranges, list):
        return [float(v) for v in ranges]
    # Flattened CSV, stamp columns removed: frame_id, 7 scalars, then ranges.
    cols = [str(payload[f"col_{i}"]) for i in range(len(payload)) if f"col_{i}" in payload]
    return [float(v) for v in cols[8 : 8 + N_BEAMS] if v not in ("", "...")]


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
    assert adapter.get_topic_info("/scan").qos_reliability == "reliable"


def test_get_topic_info_reports_latched_durability(adapter: Ros2CliAdapter) -> None:
    info = adapter.get_topic_info("/robot_description_lite")
    assert info.qos_durability == "transient_local"


def test_get_topic_info_unknown_topic_raises(adapter: Ros2CliAdapter) -> None:
    with pytest.raises(AdapterError):
        adapter.get_topic_info("/does/not/exist")


def test_sample_scan_returns_a_message(adapter: Ros2CliAdapter) -> None:
    samples = adapter.sample_messages("/scan", 1)
    assert len(samples) == 1
    assert samples[0].message_type == "sensor_msgs/msg/LaserScan"


@pytest.mark.xfail(
    strict=False, reason="D4: sim-time stamp columns stay in the CSV and shift ranges by two"
)
def test_sample_scan_delivers_all_541_ranges(adapter: Ros2CliAdapter) -> None:
    samples = adapter.sample_messages("/scan", 1, max_array_length=None)
    ranges = _ranges(samples[0].payload)
    assert len(ranges) == N_BEAMS
    assert ranges[270] == pytest.approx(1.27, abs=1e-4)
    assert ranges[540] == pytest.approx(1.54, abs=1e-4)


def test_arrays_summary_only_keeps_columns_aligned(adapter: Ros2CliAdapter) -> None:
    payload = adapter.sample_messages("/scan", 1, arrays_summary_only=True)[0].payload
    cols = [str(payload[f"col_{i}"]) for i in range(len(payload)) if f"col_{i}" in payload]
    frame = cols.index("base_laser")
    # frame_id, 7 scalars, then `ranges` and `intensities` as one cell each.
    assert len(cols) - frame == 1 + 7 + 2, cols
    summaries = cols[frame + 8 :]
    assert all(c.startswith("<sequence type:") and f"length: {N_BEAMS}>" in c for c in summaries)


def test_default_array_cut_is_listed_in_the_payload(adapter: Ros2CliAdapter) -> None:
    payload = adapter.sample_messages("/scan", 1)[0].payload
    assert payload.get("_truncated_after_columns"), payload


@pytest.mark.xfail(strict=False, reason="D3: count is ignored (--once)")
def test_sample_scan_honours_count(adapter: Ros2CliAdapter) -> None:
    assert len(adapter.sample_messages("/scan", 3)) == 3


@pytest.mark.xfail(strict=False, reason="D4: timestamp is 0 on simulated time")
def test_sample_scan_timestamp_from_header_on_sim_time(adapter: Ros2CliAdapter) -> None:
    ts = adapter.sample_messages("/scan", 1)[0].timestamp_ns
    assert 0 < ts < MAX_SIM_NS


def test_sample_latched_topic(adapter: Ros2CliAdapter) -> None:
    samples = adapter.sample_messages("/robot_description_lite", 1)
    assert len(samples) == 1
    assert "bench_robot" in str(samples[0].payload)


def test_sample_headerless_twist(adapter: Ros2CliAdapter) -> None:
    samples = adapter.sample_messages("/cmd_vel_out", 1)
    assert len(samples) == 1
    assert samples[0].timestamp_ns == 0 or samples[0].timestamp_ns > 0


@pytest.mark.xfail(strict=False, reason="D3: count is ignored (--once)")
def test_sample_twist_honours_count(adapter: Ros2CliAdapter) -> None:
    assert len(adapter.sample_messages("/cmd_vel_out", 4)) == 4


def test_sample_large_image(adapter: Ros2CliAdapter) -> None:
    samples = adapter.sample_messages("/camera/image_raw", 1)
    assert len(samples) == 1
    assert samples[0].message_type == "sensor_msgs/msg/Image"


@pytest.fixture(scope="module")
def bag() -> Path:
    assert BAG_PATH.exists(), f"bench bag missing: {BAG_PATH}"
    return BAG_PATH


def test_analyze_bag_summary(adapter: Ros2CliAdapter, bag: Path) -> None:
    result = adapter.analyze_bag(str(bag))
    assert result.duration_seconds == pytest.approx(8.0, abs=2.0)
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
