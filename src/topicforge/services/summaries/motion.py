"""Summaries of `nav_msgs/msg/Odometry` and `sensor_msgs/msg/Imu`."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from topicforge.models.summaries import ImuSummary, OdometrySummary
from topicforge.services.summaries._common import (
    as_float,
    euler_from_quaternion,
    frame_id,
    norm,
    section,
    sentence,
    text,
    vector,
)


def summarize_odometry(
    payload: Mapping[str, Any], cut_fields: frozenset[str] = frozenset()
) -> OdometrySummary | None:
    """Speed, position and heading of a decoded `Odometry`."""
    pose = section(payload, "pose", "pose")
    twist = section(payload, "twist", "twist")
    position = section(pose, "position")
    linear = vector(section(twist, "linear"))
    angle = euler_from_quaternion(section(pose, "orientation"))
    problems: list[str] = []
    if linear is None:
        problems.append("the linear twist (twist.twist.linear) is missing or not finite")
    if isinstance(angle, str):
        problems.append(f"yaw is unknown because {angle}")
    return OdometrySummary(
        summary_type="odometry",
        frame_id=frame_id(payload),
        child_frame_id=text(payload.get("child_frame_id")),
        linear_speed=None if linear is None else norm(linear),
        angular_z=as_float(section(twist, "angular").get("z")),
        position_x=as_float(position.get("x")),
        position_y=as_float(position.get("y")),
        position_z=as_float(position.get("z")),
        yaw=None if isinstance(angle, str) else angle[2],
        note=sentence(problems),
    )


def summarize_imu(
    payload: Mapping[str, Any], cut_fields: frozenset[str] = frozenset()
) -> ImuSummary | None:
    """Attitude and vector norms of a decoded `Imu`."""
    problems: list[str] = []
    angle = _imu_orientation(payload)
    if isinstance(angle, str):
        problems.append(angle)
    gyro = vector(section(payload, "angular_velocity"))
    accel = vector(section(payload, "linear_acceleration"))
    if gyro is None:
        problems.append("angular_velocity is missing or not finite")
    if accel is None:
        problems.append("linear_acceleration is missing or not finite")
    valid = not isinstance(angle, str)
    return ImuSummary(
        summary_type="imu",
        frame_id=frame_id(payload),
        orientation_valid=valid,
        roll=angle[0] if not isinstance(angle, str) else None,
        pitch=angle[1] if not isinstance(angle, str) else None,
        yaw=angle[2] if not isinstance(angle, str) else None,
        angular_velocity_norm=None if gyro is None else norm(gyro),
        linear_acceleration_norm=None if accel is None else norm(accel),
        note=sentence(problems),
    )


def _imu_orientation(payload: Mapping[str, Any]) -> tuple[float, float, float] | str:
    """Roll, pitch, yaw, or the reason the IMU gives no orientation estimate."""
    covariance = payload.get("orientation_covariance")
    if isinstance(covariance, list) and covariance and as_float(covariance[0]) == -1.0:
        return "the IMU publishes no orientation (orientation_covariance[0] is -1)"
    return euler_from_quaternion(section(payload, "orientation"))
