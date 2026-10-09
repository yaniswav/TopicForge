"""Per-type message summaries, on hand-built payloads (no ROS 2, no bag)."""

from __future__ import annotations

import math
from typing import Any

import pytest

from topicforge.services.summaries import (
    needs_whole_arrays,
    summarize_message,
    supports_summary,
)

SCAN = "sensor_msgs/msg/LaserScan"
DEG = math.pi / 180.0


def _scan(ranges: list[Any], *, angle_min: float = -math.pi / 2, step: float = DEG) -> dict:
    return {
        "header": {"frame_id": "laser"},
        "angle_min": angle_min,
        "angle_max": angle_min + step * (len(ranges) - 1),
        "angle_increment": step,
        "range_min": 0.1,
        "range_max": 10.0,
        "ranges": ranges,
    }


def _quat(yaw: float = 0.0, pitch: float = 0.0, roll: float = 0.0) -> dict[str, float]:
    cr, sr = math.cos(roll / 2), math.sin(roll / 2)
    cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
    cy, sy = math.cos(yaw / 2), math.sin(yaw / 2)
    return {
        "x": sr * cp * cy - cr * sp * sy,
        "y": cr * sp * cy + sr * cp * sy,
        "z": cr * cp * sy - sr * sp * cy,
        "w": cr * cp * cy + sr * sp * sy,
    }


# ---- registry ---------------------------------------------------------------


def test_registry_knows_the_five_types_in_ros2_and_ros1_spelling() -> None:
    for name in ("LaserScan", "Imu", "Image", "PointCloud2"):
        assert supports_summary(f"sensor_msgs/msg/{name}")
        assert supports_summary(f"sensor_msgs/{name}")
    assert supports_summary("nav_msgs/msg/Odometry")
    assert not supports_summary("geometry_msgs/msg/Twist")
    assert summarize_message("geometry_msgs/msg/Twist", {"linear": {}}) is None


def test_only_the_scan_needs_its_arrays_whole() -> None:
    assert needs_whole_arrays(SCAN)
    assert not needs_whole_arrays("sensor_msgs/msg/Image")
    assert not needs_whole_arrays("sensor_msgs/msg/PointCloud2")


def test_a_summarizer_bug_never_raises() -> None:
    assert summarize_message(SCAN, {"ranges": [object()], "angle_min": 0.0}) is not None
    assert summarize_message("nav_msgs/msg/Odometry", {"pose": 3, "twist": []}) is not None


# ---- LaserScan --------------------------------------------------------------


def test_scan_geometry_counts_and_closest_obstacle() -> None:
    ranges = [5.0] * 181
    ranges[90] = 1.25  # bearing 0 (angle_min -90 deg, 1 deg steps)
    ranges[10] = "inf"
    ranges[11] = "-inf"
    ranges[12] = "nan"
    summary = summarize_message(SCAN, _scan(ranges))
    assert summary is not None and summary.summary_type == "laser_scan"
    assert summary.frame_id == "laser"
    assert summary.beam_count == 181
    assert (summary.finite_count, summary.inf_count) == (178, 1)
    assert (summary.neg_inf_count, summary.nan_count) == (1, 1)
    assert summary.closest_obstacle is not None
    assert summary.closest_obstacle.range == 1.25
    assert summary.closest_obstacle.beam_index == 90
    assert summary.closest_obstacle.bearing == pytest.approx(0.0, abs=1e-12)
    assert summary.angle_increment == pytest.approx(DEG)
    assert summary.note is None


def test_scan_sectors_follow_the_bearing_in_the_sensor_frame() -> None:
    # 360 beams, one per degree from -180 to +179.
    ranges = [9.0] * 360
    for bearing_deg, value in ((0, 2.0), (90, 3.0), (-90, 4.0), (180 - 360, 5.0), (170, 6.0)):
        ranges[bearing_deg + 180] = value
    summary = summarize_message(SCAN, _scan(ranges, angle_min=-math.pi))
    assert summary is not None
    sectors = summary.sectors
    assert sectors.front.closest.range == 2.0  # type: ignore[union-attr]
    assert sectors.left.closest.range == 3.0  # type: ignore[union-attr]
    assert sectors.right.closest.range == 4.0  # type: ignore[union-attr]
    # -180 and 170 degrees are both behind the sensor; the nearer one wins.
    assert sectors.rear.closest.range == 5.0  # type: ignore[union-attr]
    assert sectors.rear.closest.beam_index == 0  # type: ignore[union-attr]
    assert (
        sectors.front.beam_count
        + sectors.left.beam_count
        + sectors.right.beam_count
        + (sectors.rear.beam_count)
        == 360
    )


def test_sector_edges_belong_to_the_narrower_sector() -> None:
    # Beams exactly at +-45 are front; exactly at +-135 are left / right.
    ranges = [9.0] * 360
    ranges[45 + 180] = 1.0
    ranges[135 + 180] = 2.0
    ranges[-135 + 180] = 3.0
    summary = summarize_message(SCAN, _scan(ranges, angle_min=-math.pi))
    assert summary is not None
    assert summary.sectors.front.closest.range == 1.0  # type: ignore[union-attr]
    assert summary.sectors.left.closest.range == 2.0  # type: ignore[union-attr]
    assert summary.sectors.right.closest.range == 3.0  # type: ignore[union-attr]


def test_a_scan_that_does_not_cover_the_rear_says_so() -> None:
    summary = summarize_message(SCAN, _scan([3.0] * 181))  # -90..+90 degrees
    assert summary is not None
    rear = summary.sectors.rear
    assert rear.beam_count == 0 and rear.closest is None
    assert rear.note == "The scan does not cover the rear sector."
    assert summary.sectors.front.note is None


def test_an_all_inf_scan_has_counts_but_no_obstacle() -> None:
    summary = summarize_message(SCAN, _scan(["inf"] * 181))
    assert summary is not None
    assert (summary.finite_count, summary.inf_count) == (0, 181)
    assert summary.closest_obstacle is None
    assert summary.note is not None and "no beam has a valid return" in summary.note.lower()
    assert summary.sectors.front.closest is None
    assert summary.sectors.front.note == "No valid return in the front sector."


def test_an_all_nan_scan_counts_nans() -> None:
    summary = summarize_message(SCAN, _scan(["nan"] * 10))
    assert summary is not None and summary.nan_count == 10 and summary.finite_count == 0


def test_float_non_finite_values_count_like_their_strings() -> None:
    summary = summarize_message(SCAN, _scan([math.inf, -math.inf, math.nan, 2.0]))
    assert summary is not None
    assert (summary.inf_count, summary.neg_inf_count, summary.nan_count) == (1, 1, 1)
    assert summary.finite_count == 1


def test_ranges_outside_the_sensor_limits_are_not_returns() -> None:
    ranges = [5.0] * 181
    ranges[90] = 0.0  # drivers publish 0 for "no return"
    ranges[91] = 99.0
    summary = summarize_message(SCAN, _scan(ranges))
    assert summary is not None
    assert summary.out_of_range_count == 2
    assert summary.finite_count == 181
    assert summary.closest_obstacle is not None and summary.closest_obstacle.range == 5.0


def test_an_empty_scan_is_reported_not_crashed() -> None:
    summary = summarize_message(SCAN, _scan([]))
    assert summary is not None and summary.beam_count == 0
    assert summary.note == "The scan has no ranges."


def test_a_scan_without_geometry_keeps_counts_and_says_so() -> None:
    summary = summarize_message(SCAN, {"ranges": [1.0, "inf", 2.0]})
    assert summary is not None
    assert (summary.finite_count, summary.inf_count) == (2, 1)
    assert summary.closest_obstacle is None
    assert summary.note is not None and "no angle geometry" in summary.note


def test_a_missing_increment_is_derived_from_the_angle_span() -> None:
    payload = {"angle_min": -1.0, "angle_max": 1.0, "ranges": [1.0, 2.0, 3.0]}
    summary = summarize_message(SCAN, payload)
    assert summary is not None and summary.angle_increment == pytest.approx(1.0)


def test_a_scan_with_text_arrays_or_cut_ranges_has_no_summary() -> None:
    assert summarize_message(SCAN, {"ranges": "<sequence type: float, length: 541>"}) is None
    assert summarize_message(SCAN, _scan([1.0] * 5), cut_fields=["ranges"]) is None


# ---- Odometry ---------------------------------------------------------------


def _odom(**over: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "header": {"frame_id": "odom"},
        "child_frame_id": "base_link",
        "pose": {"pose": {"position": {"x": 1.0, "y": 2.0, "z": 0.5}, "orientation": _quat(0.6)}},
        "twist": {
            "twist": {
                "linear": {"x": 3.0, "y": 4.0, "z": 0.0},
                "angular": {"x": 0, "y": 0, "z": -0.2},
            }
        },
    }
    payload.update(over)
    return payload


def test_odometry_speed_position_and_yaw() -> None:
    summary = summarize_message("nav_msgs/msg/Odometry", _odom())
    assert summary is not None and summary.summary_type == "odometry"
    assert summary.linear_speed == pytest.approx(5.0)
    assert summary.angular_z == pytest.approx(-0.2)
    assert (summary.position_x, summary.position_y, summary.position_z) == (1.0, 2.0, 0.5)
    assert summary.yaw == pytest.approx(0.6)
    assert (summary.frame_id, summary.child_frame_id) == ("odom", "base_link")
    assert summary.note is None


def test_odometry_with_a_nan_quaternion_keeps_the_rest() -> None:
    pose = {
        "pose": {
            "position": {"x": 1.0, "y": 0, "z": 0},
            "orientation": {"x": "nan", "y": 0, "z": 0, "w": 1},
        }
    }
    summary = summarize_message("nav_msgs/msg/Odometry", _odom(pose=pose))
    assert summary is not None
    assert summary.yaw is None and summary.linear_speed == pytest.approx(5.0)
    assert summary.note is not None and "non-finite" in summary.note


def test_odometry_with_an_unset_quaternion_is_not_a_zero_yaw() -> None:
    pose = {"pose": {"position": {}, "orientation": {"x": 0, "y": 0, "z": 0, "w": 0}}}
    summary = summarize_message("nav_msgs/msg/Odometry", _odom(pose=pose))
    assert summary is not None and summary.yaw is None
    assert summary.note is not None and "all zeros" in summary.note


def test_odometry_with_infinite_twist_has_no_speed() -> None:
    twist = {"twist": {"linear": {"x": "inf", "y": 0, "z": 0}, "angular": {"z": 0}}}
    summary = summarize_message("nav_msgs/msg/Odometry", _odom(twist=twist))
    assert summary is not None and summary.linear_speed is None
    assert summary.note is not None and "twist.twist.linear" in summary.note


# ---- Imu --------------------------------------------------------------------


def _imu(**over: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "header": {"frame_id": "imu_link"},
        "orientation": _quat(yaw=0.5, pitch=0.2, roll=-0.1),
        "orientation_covariance": [0.01] + [0.0] * 8,
        "angular_velocity": {"x": 0.0, "y": 3.0, "z": 4.0},
        "linear_acceleration": {"x": 0.0, "y": 0.0, "z": 9.81},
    }
    payload.update(over)
    return payload


def test_imu_attitude_and_norms() -> None:
    summary = summarize_message("sensor_msgs/msg/Imu", _imu())
    assert summary is not None and summary.summary_type == "imu"
    assert summary.orientation_valid is True
    assert summary.roll == pytest.approx(-0.1)
    assert summary.pitch == pytest.approx(0.2)
    assert summary.yaw == pytest.approx(0.5)
    assert summary.angular_velocity_norm == pytest.approx(5.0)
    assert summary.linear_acceleration_norm == pytest.approx(9.81)
    assert summary.frame_id == "imu_link"
    assert summary.note is None


def test_imu_with_a_non_unit_quaternion_is_normalized() -> None:
    q = {k: v * 2 for k, v in _quat(yaw=0.5).items()}
    summary = summarize_message("sensor_msgs/msg/Imu", _imu(orientation=q))
    assert summary is not None and summary.yaw == pytest.approx(0.5)


@pytest.mark.parametrize(
    ("over", "reason"),
    [
        ({"orientation": {"x": 0, "y": 0, "z": 0, "w": 0}}, "all zeros"),
        ({"orientation_covariance": [-1.0] + [0.0] * 8}, "orientation_covariance[0] is -1"),
        ({"orientation": {"x": "nan", "y": 0, "z": 0, "w": 1}}, "non-finite"),
        ({"orientation": {}}, "missing"),
    ],
)
def test_imu_without_a_usable_orientation_has_no_angles(over: dict, reason: str) -> None:
    summary = summarize_message("sensor_msgs/msg/Imu", _imu(**over))
    assert summary is not None and summary.orientation_valid is False
    assert (summary.roll, summary.pitch, summary.yaw) == (None, None, None)
    assert summary.note is not None and reason in summary.note
    assert summary.angular_velocity_norm == pytest.approx(5.0)


def test_imu_vectors_with_nan_have_no_norm() -> None:
    summary = summarize_message(
        "sensor_msgs/msg/Imu", _imu(angular_velocity={"x": "nan", "y": 0, "z": 0})
    )
    assert summary is not None and summary.angular_velocity_norm is None
    assert summary.linear_acceleration_norm == pytest.approx(9.81)


# ---- Image ------------------------------------------------------------------


def _image(**over: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "header": {"frame_id": "camera"},
        "width": 4,
        "height": 2,
        "encoding": "rgb8",
        "is_bigendian": 0,
        "step": 12,
        "data": list(range(24)),
    }
    payload.update(over)
    return payload


def test_image_geometry_and_buffer_length() -> None:
    summary = summarize_message("sensor_msgs/msg/Image", _image())
    assert summary is not None and summary.summary_type == "image"
    assert (summary.width, summary.height, summary.step) == (4, 2, 12)
    assert summary.encoding == "rgb8" and summary.is_bigendian is False
    assert (summary.data_length, summary.data_length_basis) == (24, "payload")
    assert summary.note is None


def test_image_with_a_cut_buffer_uses_step_times_height() -> None:
    summary = summarize_message("sensor_msgs/msg/Image", _image(data=[0] * 5), cut_fields=["data"])
    assert summary is not None
    assert (summary.data_length, summary.data_length_basis) == (24, "step_x_height")


def test_image_with_a_text_buffer_reads_the_length_from_the_text() -> None:
    summary = summarize_message(
        "sensor_msgs/msg/Image", _image(data="<sequence type: uint8, length: 921600>")
    )
    assert summary is not None
    assert (summary.data_length, summary.data_length_basis) == (921600, "payload")


def test_image_missing_fields_are_named() -> None:
    summary = summarize_message("sensor_msgs/msg/Image", {"encoding": "mono8"})
    assert summary is not None and summary.data_length is None
    assert summary.note == "Missing fields: width, height, step."


# ---- PointCloud2 ------------------------------------------------------------


def _cloud(**over: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "header": {"frame_id": "lidar"},
        "width": 100,
        "height": 4,
        "fields": [
            {"name": "x", "offset": 0, "datatype": 7, "count": 1},
            {"name": "y", "offset": 4, "datatype": 7, "count": 1},
            {"name": "ring", "offset": 8, "datatype": 4, "count": 1},
        ],
        "is_bigendian": False,
        "point_step": 12,
        "row_step": 1200,
        "data": [0] * 128,
        "is_dense": True,
    }
    payload.update(over)
    return payload


def test_point_cloud_count_layout_and_density() -> None:
    summary = summarize_message("sensor_msgs/msg/PointCloud2", _cloud())
    assert summary is not None and summary.summary_type == "point_cloud2"
    assert summary.point_count == 400
    assert (summary.point_step, summary.row_step, summary.is_dense) == (12, 1200, True)
    assert [(f.name, f.datatype, f.offset) for f in summary.fields] == [
        ("x", "FLOAT32", 0),
        ("y", "FLOAT32", 4),
        ("ring", "UINT16", 8),
    ]
    assert summary.note is None


def test_an_empty_cloud_is_reported() -> None:
    summary = summarize_message("sensor_msgs/msg/PointCloud2", _cloud(width=0, height=1, data=[]))
    assert summary is not None and summary.point_count == 0
    assert summary.note == "The cloud has no points."


def test_a_cloud_with_text_fields_keeps_the_count_and_says_so() -> None:
    summary = summarize_message(
        "sensor_msgs/msg/PointCloud2", _cloud(fields="<sequence type: PointField, length: 3>")
    )
    assert summary is not None and summary.point_count == 400 and summary.fields == []
    assert summary.note == "The point layout (fields) was not read."


def test_an_unknown_point_field_type_is_labelled() -> None:
    cloud = _cloud(fields=[{"name": "z", "offset": 0, "datatype": 42, "count": 1}])
    summary = summarize_message("sensor_msgs/msg/PointCloud2", cloud)
    assert summary is not None and summary.fields[0].datatype == "UNKNOWN"
