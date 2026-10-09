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

    range: float = Field(description="Measured distance in meters.")
    bearing: float = Field(
        description=(
            "Direction of the beam in radians in the sensor frame (`frame_id` of the summary): "
            "0 is the sensor's +x axis, positive counter-clockwise, within -pi..pi."
        )
    )
    beam_index: int = Field(ge=0, description="Index of the beam in `ranges`.")


class ScanSector(BaseModel):
    """The closest valid return inside one angular sector of the scan."""

    model_config = _CONFIG

    beam_count: int = Field(
        ge=0,
        description=(
            "Number of beams of the scan whose bearing falls inside the sector. 0 means the "
            "scan does not cover the sector (for example the rear of a scan that spans 270 degrees)."
        ),
    )
    closest: ScanReturn | None = Field(
        description="The closest valid return in the sector; `null` with the sector `note`."
    )
    note: str | None = Field(
        default=None,
        description=(
            "Why `closest` is `null`: the scan does not cover the sector, or no beam "
            "in it has a valid return. `null` when `closest` is set."
        ),
    )


class ScanSectors(BaseModel):
    """Sector minima, defined by bearing in the sensor frame."""

    model_config = _CONFIG

    front: ScanSector = Field(
        description="Bearings within +-45 degrees (+-0.785 rad) of the sensor's +x axis."
    )
    left: ScanSector = Field(
        description="Bearings above 45 and up to 135 degrees (0.785 to 2.356 rad)."
    )
    right: ScanSector = Field(
        description="Bearings from -135 up to -45 degrees (-2.356 to -0.785 rad)."
    )
    rear: ScanSector = Field(
        description=(
            "Bearings beyond +-135 degrees (|bearing| above 2.356 rad, the sensor's -x side)."
        )
    )


class LaserScanSummary(BaseModel):
    """Summary of a `sensor_msgs/msg/LaserScan`."""

    model_config = _CONFIG

    summary_type: Literal["laser_scan"]
    frame_id: str | None = Field(
        description=(
            "`header.frame_id` of the scan. Every angle below is in this sensor frame, not in "
            "the robot base frame; `null` when the message has no frame id."
        )
    )
    beam_count: int = Field(ge=0, description="Number of beams in `ranges`.")
    angle_min: float | None = Field(description="Bearing of beam 0 in radians.")
    angle_max: float | None = Field(description="Bearing of the last beam in radians.")
    angle_increment: float | None = Field(description="Angle between two beams in radians.")
    range_min: float | None = Field(description="Smallest valid range in meters.")
    range_max: float | None = Field(description="Largest valid range in meters.")
    finite_count: int = Field(ge=0, description="Beams whose range is a finite number.")
    inf_count: int = Field(ge=0, description="Beams whose range is `+inf` (nothing in range).")
    neg_inf_count: int = Field(ge=0, description="Beams whose range is `-inf`.")
    nan_count: int = Field(ge=0, description="Beams whose range is `nan` (no measurement).")
    out_of_range_count: int = Field(
        ge=0,
        description=(
            "Finite ranges outside `range_min`..`range_max`: they are not valid returns and "
            "are left out of `closest_obstacle` and `sectors`."
        ),
    )
    closest_obstacle: ScanReturn | None = Field(
        description="The closest valid return of the whole scan; `null` with the summary `note`."
    )
    sectors: ScanSectors = Field(
        description="The closest valid return in the front, left, right and rear sectors."
    )
    note: str | None = Field(
        default=None,
        description=(
            "One sentence when the summary is partial (no valid return, no ranges, no angle "
            "geometry); `null` otherwise."
        ),
    )


class OdometrySummary(BaseModel):
    """Summary of a `nav_msgs/msg/Odometry`."""

    model_config = _CONFIG

    summary_type: Literal["odometry"]
    frame_id: str | None = Field(description="`header.frame_id`: the frame of the pose.")
    child_frame_id: str | None = Field(
        description="`child_frame_id`: the frame the twist is expressed in (usually the robot base)."
    )
    linear_speed: float | None = Field(
        description="Norm of `twist.twist.linear` in meters per second; `null` with the `note`."
    )
    angular_z: float | None = Field(
        description="`twist.twist.angular.z` in radians per second (yaw rate)."
    )
    position_x: float | None = Field(description="`pose.pose.position.x` in meters.")
    position_y: float | None = Field(description="`pose.pose.position.y` in meters.")
    position_z: float | None = Field(description="`pose.pose.position.z` in meters.")
    yaw: float | None = Field(
        description="Heading in radians from the pose quaternion, within -pi..pi; `null` with the `note`."
    )
    note: str | None = Field(
        default=None,
        description="One sentence when a value is missing or not a finite number; else `null`.",
    )


class ImuSummary(BaseModel):
    """Summary of a `sensor_msgs/msg/Imu`."""

    model_config = _CONFIG

    summary_type: Literal["imu"]
    frame_id: str | None = Field(description="`header.frame_id` of the IMU.")
    orientation_valid: bool = Field(
        description=(
            "False when the IMU publishes no orientation estimate (quaternion all zeros, "
            "`orientation_covariance[0]` -1, or not finite): `roll`, `pitch` and `yaw` are then `null`."
        )
    )
    roll: float | None = Field(description="Rotation about x in radians, within -pi..pi.")
    pitch: float | None = Field(description="Rotation about y in radians, within -pi/2..pi/2.")
    yaw: float | None = Field(description="Rotation about z in radians, within -pi..pi.")
    angular_velocity_norm: float | None = Field(
        description="Norm of `angular_velocity` in radians per second."
    )
    linear_acceleration_norm: float | None = Field(
        description="Norm of `linear_acceleration` in meters per second squared."
    )
    note: str | None = Field(
        default=None,
        description="One sentence when the orientation or a vector is unusable; else `null`.",
    )


class ImageSummary(BaseModel):
    """Summary of a `sensor_msgs/msg/Image`. Pixels are never read."""

    model_config = _CONFIG

    summary_type: Literal["image"]
    frame_id: str | None = Field(description="`header.frame_id` of the camera.")
    width: int | None = Field(description="Image width in pixels.")
    height: int | None = Field(description="Image height in pixels.")
    encoding: str | None = Field(description="Pixel encoding, for example `rgb8`.")
    step: int | None = Field(description="Row length in bytes.")
    is_bigendian: bool | None = Field(description="Whether multi-byte pixels are big endian.")
    data_length: int | None = Field(
        description="Size of the pixel buffer in bytes; `data_length_basis` says how it was found."
    )
    data_length_basis: Literal["payload", "step_x_height"] | None = Field(
        description=(
            "`payload` when counted on the data array, `step_x_height` when the array was cut "
            "or summarized and the length is `step` times `height`; `null` when `data_length` is."
        )
    )
    note: str | None = Field(default=None, description="One sentence when partial; else `null`.")


class PointFieldSummary(BaseModel):
    """One field of a point cloud (`sensor_msgs/msg/PointField`)."""

    model_config = _CONFIG

    name: str = Field(description="Field name, for example `x` or `intensity`.")
    datatype: str = Field(description="Element type, for example `FLOAT32`.")
    offset: int | None = Field(description="Byte offset of the field in a point.")
    count: int | None = Field(description="Elements per point for this field.")


class PointCloud2Summary(BaseModel):
    """Summary of a `sensor_msgs/msg/PointCloud2`. Points are never read."""

    model_config = _CONFIG

    summary_type: Literal["point_cloud2"]
    frame_id: str | None = Field(description="`header.frame_id` of the cloud.")
    width: int | None = Field(description="Points per row (the point count when `height` is 1).")
    height: int | None = Field(description="Rows; 1 for an unorganized cloud.")
    point_count: int | None = Field(description="`width` times `height`.")
    point_step: int | None = Field(description="Bytes per point.")
    row_step: int | None = Field(description="Bytes per row.")
    is_dense: bool | None = Field(description="True when the cloud has no invalid (nan) points.")
    fields: list[PointFieldSummary] = Field(description="The point layout.")
    note: str | None = Field(default=None, description="One sentence when partial; else `null`.")


MessageSummary = Annotated[
    LaserScanSummary | OdometrySummary | ImuSummary | ImageSummary | PointCloud2Summary,
    Field(discriminator="summary_type"),
]


class TopicRate(BaseModel):
    """How a topic delivered its messages during one sampling call, with a verdict."""

    model_config = _CONFIG

    basis: Literal["received_ns", "recorded_ns"] = Field(
        description=(
            "The clock the rate is measured on: `received_ns` (the wall clock when the live "
            "CLI printed each message, so it measures the delivery the caller gets) or "
            "`recorded_ns` (the bag record time)."
        )
    )
    message_count: int = Field(
        ge=0, description="Messages the rate is computed from, including any dropped for size."
    )
    window_s: float = Field(
        ge=0,
        description=(
            "Length of the observation in seconds: for a live call the time spent collecting; "
            "for a bag the span from the first to the last message."
        ),
    )
    mean_interval_s: float | None = Field(
        description="Mean time between consecutive messages; `null` with fewer than 2 messages."
    )
    interval_median_s: float | None = Field(
        description="Median time between consecutive messages; `null` with fewer than 2 messages."
    )
    max_gap_s: float | None = Field(
        description="Longest time between consecutive messages; `null` with fewer than 2 messages."
    )
    interval_cv: float | None = Field(
        description=(
            "Coefficient of variation of the intervals (standard deviation over mean, "
            ">= 0); `null` with fewer than 2 messages."
        )
    )
    observed_frequency_hz: float | None = Field(
        description=(
            "Messages per second on `basis`: (count - 1) over the time from the first to the "
            "last message; `null` with fewer than 2 messages. For a live call this is the rate "
            "the caller received, which can be below the publish rate."
        )
    )
    sim_frequency_hz: float | None = Field(
        description=(
            "Same computation on the messages' own stamps (`timestamp_ns`), so it is the "
            "publisher's clock: sim time on a simulation. `null` when the messages carry no "
            "stamp (`stamp_source` `none` or `recorded`), the stamps do not advance, or "
            "there are fewer than 2 messages."
        )
    )
    trailing_gap_s: float | None = Field(
        description=(
            "Time between the last message and the end of the live observation. `null` when "
            "collection stopped on `count` and for bags, where the window has no end to measure."
        )
    )
    verdict: RateVerdict = Field(
        description=(
            "First match of: `silent` (no message), `insufficient` (fewer than 5), "
            "`intermittent` (an interval, trailing one included, longer than 3 times the "
            "median), `stable` (`interval_cv` < 0.2), `jittery` (0.2 to 0.5), `erratic` (0.5 "
            "or more). It describes what was observed in this window, not a diagnosis: a "
            "`silent` topic may be latched, a topic with several publishers such as `/tf` "
            "reads `erratic` without being broken, and a simulated or bridged sensor whose "
            "intervals alternate reads `jittery` while healthy: compare `max_gap_s` with "
            "`interval_median_s` before suspecting a lost message."
        )
    )
    verdict_note: str = Field(description="One sentence that states the verdict in plain words.")
