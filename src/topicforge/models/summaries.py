"""Per-type message summaries and the observed-rate block of a sample result.

A summary condenses one decoded message into a few numbers a client can read
at once (closest obstacle of a scan, speed of an odometry message). It is
always computed on the whole message, even when `payload` was cut at
`max_array_length`. Units follow REP 103 and are stated in the descriptions:
distances in meters, angles in radians, speeds in meters per second, angular
rates in radians per second. The unit-less names mirror the ROS field names
(`angle_min`, `range_max`).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

_CONFIG = ConfigDict(extra="forbid", frozen=True)

RateVerdict = Literal["silent", "insufficient", "intermittent", "stable", "jittery", "erratic"]


class ScanReturn(BaseModel):
    """One beam of a laser scan: the shortest valid return of a set of beams."""

    model_config = _CONFIG

    range: float = Field(description="Distance, meters.")
    bearing: float = Field(
        description="Beam direction in radians, sensor frame: 0 is +x, counter-clockwise positive."
    )
    beam_index: int = Field(ge=0, description="Index in `ranges`.")


class ScanSector(BaseModel):
    """The closest valid return inside one angular sector of the scan."""

    model_config = _CONFIG

    beam_count: int = Field(
        ge=0,
        description="Beams inside the sector (0: not covered).",
    )
    closest: ScanReturn | None = Field(
        description="Closest valid return in the sector; `null` with `note`."
    )
    note: str | None = Field(
        default=None,
        description="Why `closest` is `null`; `null` when it is set.",
    )


class ScanSectors(BaseModel):
    """Sector minima, defined by bearing in the sensor frame."""

    model_config = _CONFIG

    front: ScanSector = Field(description="Bearings within +-45 degrees of +x.")
    left: ScanSector = Field(description="Bearings from 45 to 135 degrees.")
    right: ScanSector = Field(description="Bearings from -135 to -45 degrees.")
    rear: ScanSector = Field(description="Bearings beyond +-135 degrees.")


class LaserScanSummary(BaseModel):
    """Summary of a `sensor_msgs/msg/LaserScan`."""

    model_config = _CONFIG

    summary_type: Literal["laser_scan"]
    frame_id: str | None = Field(
        description="`header.frame_id`; every angle is in this sensor frame, not the base frame."
    )
    beam_count: int = Field(ge=0, description="Beams in `ranges`.")
    angle_min: float | None = Field(description="Bearing of beam 0, rad.")
    angle_max: float | None = Field(description="Bearing of the last beam, rad.")
    angle_increment: float | None = Field(description="Angle between beams, rad.")
    range_min: float | None = Field(description="Smallest valid range, m.")
    range_max: float | None = Field(description="Largest valid range, m.")
    finite_count: int = Field(ge=0, description="Beams with a finite range.")
    inf_count: int = Field(ge=0, description="Beams at `+inf`.")
    neg_inf_count: int = Field(ge=0, description="Beams at `-inf`.")
    nan_count: int = Field(ge=0, description="Beams at `nan`.")
    out_of_range_count: int = Field(
        ge=0,
        description="Finite ranges outside `range_min`..`range_max` (not valid).",
    )
    closest_obstacle: ScanReturn | None = Field(
        description="Closest valid return of the scan; `null` with `note`."
    )
    sectors: ScanSectors = Field(description="Closest valid return per sector.")
    note: str | None = Field(
        default=None,
        description="One sentence when the summary is partial; else `null`.",
    )


class OdometrySummary(BaseModel):
    """Summary of a `nav_msgs/msg/Odometry`."""

    model_config = _CONFIG

    summary_type: Literal["odometry"]
    frame_id: str | None = Field(description="`header.frame_id` (frame of the pose).")
    child_frame_id: str | None = Field(description="`child_frame_id` (frame of the twist).")
    linear_speed: float | None = Field(description="Norm of `twist.twist.linear`, m/s.")
    angular_z: float | None = Field(description="`twist.twist.angular.z`, rad/s.")
    position_x: float | None = Field(description="`pose.pose.position.x`, m.")
    position_y: float | None = Field(description="`pose.pose.position.y`, m.")
    position_z: float | None = Field(description="`pose.pose.position.z`, m.")
    yaw: float | None = Field(description="Heading from the pose quaternion, radians.")
    note: str | None = Field(
        default=None,
        description="One sentence when a value is missing; else `null`.",
    )


class ImuSummary(BaseModel):
    """Summary of a `sensor_msgs/msg/Imu`."""

    model_config = _CONFIG

    summary_type: Literal["imu"]
    frame_id: str | None = Field(description="`header.frame_id` of the IMU.")
    orientation_valid: bool = Field(
        description="False when the IMU gives no orientation: `roll`, `pitch`, `yaw` are `null`."
    )
    roll: float | None = Field(description="Rotation about x, radians.")
    pitch: float | None = Field(description="Rotation about y, radians.")
    yaw: float | None = Field(description="Rotation about z, radians.")
    angular_velocity_norm: float | None = Field(description="Norm of `angular_velocity`, rad/s.")
    linear_acceleration_norm: float | None = Field(
        description="Norm of `linear_acceleration`, m/s^2."
    )
    note: str | None = Field(
        default=None,
        description="One sentence when a value is unusable; else `null`.",
    )


class ImageSummary(BaseModel):
    """Summary of a `sensor_msgs/msg/Image`. Pixels are never read."""

    model_config = _CONFIG

    summary_type: Literal["image"]
    frame_id: str | None = Field(description="`header.frame_id` of the camera.")
    width: int | None = Field(description="Pixels.")
    height: int | None = Field(description="Pixels.")
    encoding: str | None = Field(description="Pixel encoding, e.g. `rgb8`.")
    step: int | None = Field(description="Row length, bytes.")
    is_bigendian: bool | None = Field(description="Big-endian pixels.")
    data_length: int | None = Field(
        description="Pixel buffer size in bytes (see `data_length_basis`)."
    )
    data_length_basis: Literal["payload", "step_x_height"] | None = Field(
        description="`payload` (counted) or `step_x_height` (array cut, so step times height)."
    )
    note: str | None = Field(default=None, description="One sentence when partial; else `null`.")


class PointFieldSummary(BaseModel):
    """One field of a point cloud (`sensor_msgs/msg/PointField`)."""

    model_config = _CONFIG

    name: str = Field(description="Field name, e.g. `x`.")
    datatype: str = Field(description="Element type, e.g. `FLOAT32`.")
    offset: int | None = Field(description="Byte offset in a point.")
    count: int | None = Field(description="Elements per point.")


class PointCloud2Summary(BaseModel):
    """Summary of a `sensor_msgs/msg/PointCloud2`. Points are never read."""

    model_config = _CONFIG

    summary_type: Literal["point_cloud2"]
    frame_id: str | None = Field(description="`header.frame_id` of the cloud.")
    width: int | None = Field(description="Points per row.")
    height: int | None = Field(description="Rows (1: unorganized).")
    point_count: int | None = Field(description="`width` times `height`.")
    point_step: int | None = Field(description="Bytes per point.")
    row_step: int | None = Field(description="Bytes per row.")
    is_dense: bool | None = Field(description="True when no point is invalid.")
    fields: list[PointFieldSummary] = Field(description="Point layout.")
    note: str | None = Field(default=None, description="One sentence when partial; else `null`.")


MessageSummary = Annotated[
    LaserScanSummary | OdometrySummary | ImuSummary | ImageSummary | PointCloud2Summary,
    Field(discriminator="summary_type"),
]


class TopicRate(BaseModel):
    """How a topic delivered its messages during one sampling call, with a verdict."""

    model_config = _CONFIG

    basis: Literal["received_ns", "recorded_ns"] = Field(
        description=("Clock the rate is measured on: `received_ns` (live) or `recorded_ns` (bag).")
    )
    message_count: int = Field(ge=0, description="Messages the rate is computed from.")
    startup_burst_count: int = Field(
        ge=0,
        description=(
            "Leading messages set aside as a start-up drain (live only); still in `message_count`."
        ),
    )
    window_s: float = Field(
        ge=0,
        description="Observation length, seconds.",
    )
    mean_interval_s: float | None = Field(description="Mean interval; `null` under 2 messages.")
    interval_median_s: float | None = Field(description="Median interval; `null` under 2 messages.")
    max_gap_s: float | None = Field(description="Longest interval; `null` under 2 messages.")
    interval_cv: float | None = Field(
        description="Interval standard deviation over mean; `null` under 2 messages."
    )
    observed_frequency_hz: float | None = Field(
        description=(
            "Messages per second on `basis`, drain excluded; the rate the caller "
            "received. `null` under 2 messages."
        )
    )
    sim_frequency_hz: float | None = Field(
        description=(
            "Same rate on the messages' own stamps (publisher clock); `null` without "
            "advancing stamps."
        )
    )
    trailing_gap_s: float | None = Field(
        description="Time from the last message to the end of a live observation; else `null`."
    )
    verdict: RateVerdict = Field(
        description=(
            "First match: `silent` (none), `insufficient` (under 5), `intermittent` (an"
            " interval over 3x the median), `stable` (cv < 0.2), `jittery` (< 0.5), "
            "`erratic`. Describes this window, not a diagnosis."
        )
    )
    verdict_note: str = Field(description="The verdict in plain words.")
