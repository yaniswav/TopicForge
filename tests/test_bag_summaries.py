"""Summaries and rate from bags: the OmniSim Humble fixture and synthetic MCAP bags.

The fixture is a real simulated Husky (docs: `fixtures/bags/omnisim_humble/README.txt`):
the robot is parked, the 541-beam scan spans -135 to +135 degrees, and the walls stand at
distances known from the simulator: 2.7988 m ahead (beam 270), 2.0000 m on the left (beam
450) and 1.5000 m on the right (beam 90). The rear wall is outside the field of view.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from topicforge.services.bag_service import BagService

pytestmark = pytest.mark.requires_rosbags

BAG_DIR = Path(__file__).parent / "fixtures" / "bags" / "omnisim_humble"
TOLERANCE_M = 0.005


@pytest.fixture(scope="module")
def bag_path() -> str:
    pytest.importorskip("rosbags")
    return str(BAG_DIR)


def test_scan_closest_obstacle_and_sector_minima_match_the_known_walls(bag_path: str) -> None:
    result = BagService().peek_samples(bag_path, "/scan", 5)
    for sample in result.samples:
        summary = sample.summary
        assert summary is not None and summary.summary_type == "laser_scan"
        assert summary.frame_id == "base_laser" and summary.beam_count == 541
        front, left, right = summary.sectors.front, summary.sectors.left, summary.sectors.right
        assert front.closest.range == pytest.approx(2.7988, abs=TOLERANCE_M)  # type: ignore[union-attr]
        assert front.closest.beam_index == 270  # type: ignore[union-attr]
        assert front.closest.bearing == pytest.approx(0.0, abs=0.01)  # type: ignore[union-attr]
        assert left.closest.range == pytest.approx(2.0000, abs=TOLERANCE_M)  # type: ignore[union-attr]
        assert left.closest.beam_index == 450  # type: ignore[union-attr]
        assert left.closest.bearing == pytest.approx(math.pi / 2, abs=0.01)  # type: ignore[union-attr]
        assert right.closest.range == pytest.approx(1.5000, abs=TOLERANCE_M)  # type: ignore[union-attr]
        assert right.closest.beam_index == 90  # type: ignore[union-attr]
        assert right.closest.bearing == pytest.approx(-math.pi / 2, abs=0.01)  # type: ignore[union-attr]
        # The nearest thing in the room is the box on the right.
        assert summary.closest_obstacle is not None
        assert summary.closest_obstacle.range == pytest.approx(1.5000, abs=TOLERANCE_M)
        assert summary.closest_obstacle.beam_index == 90


def test_scan_rear_is_outside_the_field_of_view(bag_path: str) -> None:
    summary = BagService().peek_samples(bag_path, "/scan", 1).samples[0].summary
    assert summary is not None
    assert summary.sectors.rear.beam_count == 0
    assert summary.sectors.rear.closest is None
    assert summary.sectors.rear.note == "The scan does not cover the rear sector."
    assert summary.angle_min == pytest.approx(-2.3518, abs=1e-3)
    assert summary.angle_max == pytest.approx(2.3518, abs=1e-3)


def test_scan_counts_cover_every_beam(bag_path: str) -> None:
    summary = BagService().peek_samples(bag_path, "/scan", 1).samples[0].summary
    assert summary is not None
    total = summary.finite_count + summary.inf_count + summary.neg_inf_count + summary.nan_count
    assert total == 541
    assert summary.inf_count > 0  # beams that see nothing in range


def test_the_returned_scan_payload_keeps_non_finite_floats_as_strings(bag_path: str) -> None:
    result = BagService().peek_samples(bag_path, "/scan", 1)
    ranges = result.samples[0].payload["ranges"]
    assert "inf" in ranges
    assert all(isinstance(r, str) or math.isfinite(r) for r in ranges)
    assert "NaN" not in json.dumps(result.model_dump(mode="json"), allow_nan=False)


def test_odometry_of_a_parked_robot_has_no_speed(bag_path: str) -> None:
    summary = BagService().peek_samples(bag_path, "/odom", 3).samples[0].summary
    assert summary is not None and summary.summary_type == "odometry"
    assert summary.linear_speed == pytest.approx(0.0, abs=1e-3)
    assert summary.angular_z == pytest.approx(0.0, abs=1e-3)
    assert (summary.frame_id, summary.child_frame_id) == ("odom", "base_link")
    assert summary.yaw is not None and math.isfinite(summary.yaw)


def test_imu_attitude_is_finite_and_gravity_is_about_9_81(bag_path: str) -> None:
    summary = BagService().peek_samples(bag_path, "/imu/data", 3).samples[0].summary
    assert summary is not None and summary.summary_type == "imu"
    assert summary.orientation_valid is True
    for angle in (summary.roll, summary.pitch, summary.yaw):
        assert angle is not None and math.isfinite(angle)
    assert summary.linear_acceleration_norm == pytest.approx(9.81, abs=0.05)


def test_topics_without_a_summarizer_have_none(bag_path: str) -> None:
    for topic in ("/tf", "/clock", "/gps/local"):
        for sample in BagService().peek_samples(bag_path, topic, 2).samples:
            assert sample.summary is None, topic


def test_bag_rate_is_measured_on_the_record_time_and_has_no_trailing_gap(bag_path: str) -> None:
    result = BagService().peek_samples(bag_path, "/odom", 30)
    rate = result.rate
    assert rate is not None
    assert rate.basis == "recorded_ns" and rate.message_count == 30
    assert rate.observed_frequency_hz == pytest.approx(5.0, rel=0.1)
    assert rate.verdict == "stable"
    assert rate.trailing_gap_s is None
    # The simulator stamps its messages on its own clock, which runs faster than the wall.
    assert rate.sim_frequency_hz is not None and rate.sim_frequency_hz > rate.observed_frequency_hz  # type: ignore[operator]


def test_a_topic_without_a_header_still_gets_a_rate(bag_path: str) -> None:
    rate = BagService().peek_samples(bag_path, "/clock", 20).rate
    assert rate is not None and rate.observed_frequency_hz == pytest.approx(10.0, rel=0.1)


# ---- arrays past the 4096 cut ------------------------------------------------


def _write_scan_bag(directory: Path, beams: int) -> Path:
    import numpy as np
    from rosbags.rosbag2 import StoragePlugin, Writer
    from rosbags.typesys import Stores, get_typestore

    typestore = get_typestore(Stores.ROS2_HUMBLE)
    types = typestore.types
    bag = directory / "scan_bag"
    with Writer(bag, version=Writer.VERSION_LATEST, storage_plugin=StoragePlugin.MCAP) as writer:
        conn = writer.add_connection("/scan", "sensor_msgs/msg/LaserScan", typestore=typestore)
        ranges = np.full(beams, 5.0, dtype=np.float32)
        ranges[beams - 10] = 0.5  # past the 4096 cut
        ranges[3] = np.inf
        for i in range(3):
            header = types["std_msgs/msg/Header"](
                stamp=types["builtin_interfaces/msg/Time"](sec=i, nanosec=0), frame_id="lidar"
            )
            msg = types["sensor_msgs/msg/LaserScan"](
                header=header,
                angle_min=-math.pi,
                angle_max=math.pi,
                angle_increment=2 * math.pi / beams,
                time_increment=0.0,
                scan_time=0.1,
                range_min=0.1,
                range_max=20.0,
                ranges=ranges,
                intensities=np.zeros(0, dtype=np.float32),
            )
            writer.write(
                conn, i * 100_000_000, typestore.serialize_cdr(msg, "sensor_msgs/msg/LaserScan")
            )
    return bag


def test_a_scan_longer_than_the_array_cut_is_summarized_whole(tmp_path: Path) -> None:
    pytest.importorskip("rosbags")
    bag = _write_scan_bag(tmp_path, beams=6000)
    sample = BagService().peek_samples(str(bag), "/scan", 1).samples[0]
    assert len(sample.payload["ranges"]) == 4096  # the returned payload is cut ...
    summary = sample.summary
    assert summary is not None and summary.beam_count == 6000  # ... the summary is not
    assert summary.closest_obstacle is not None
    assert summary.closest_obstacle.range == 0.5
    assert summary.closest_obstacle.beam_index == 5990
    assert summary.inf_count == 1


def test_a_cut_image_buffer_is_sized_from_its_geometry(tmp_path: Path) -> None:
    pytest.importorskip("rosbags")
    import numpy as np
    from rosbags.rosbag2 import StoragePlugin, Writer
    from rosbags.typesys import Stores, get_typestore

    typestore = get_typestore(Stores.ROS2_HUMBLE)
    types = typestore.types
    bag = tmp_path / "image_bag"
    with Writer(bag, version=Writer.VERSION_LATEST, storage_plugin=StoragePlugin.MCAP) as writer:
        conn = writer.add_connection("/cam", "sensor_msgs/msg/Image", typestore=typestore)
        header = types["std_msgs/msg/Header"](
            stamp=types["builtin_interfaces/msg/Time"](sec=1, nanosec=0), frame_id="cam"
        )
        msg = types["sensor_msgs/msg/Image"](
            header=header,
            height=100,
            width=100,
            encoding="rgb8",
            is_bigendian=0,
            step=300,
            data=np.zeros(30000, dtype=np.uint8),
        )
        writer.write(conn, 0, typestore.serialize_cdr(msg, "sensor_msgs/msg/Image"))
    summary = BagService().peek_samples(str(bag), "/cam", 1).samples[0].summary
    assert summary is not None and summary.summary_type == "image"
    assert (summary.width, summary.height, summary.step) == (100, 100, 300)
    assert (summary.data_length, summary.data_length_basis) == (30000, "step_x_height")
