"""Deterministic fake-robot fixtures used by `MockAdapter`.

These model a small differential-drive mobile robot with a 2D LIDAR, an
RGB camera, and a TF tree. The data is rich enough to make demos and
screenshots believable, and stable enough for tests to assert on exact
values.

If you change a value here, expect to update tests under `tests/`.
"""

from __future__ import annotations

from topicforge.models import BagAnalysis, BagTopicStats, MessageSample, TopicInfo

MOCK_TOPICS: tuple[TopicInfo, ...] = (
    TopicInfo(
        name="/cmd_vel",
        message_type="geometry_msgs/msg/Twist",
        publisher_count=1,
        subscriber_count=1,
        qos_reliability="reliable",
        mode_effective="mock",
    ),
    TopicInfo(
        name="/odom",
        message_type="nav_msgs/msg/Odometry",
        publisher_count=1,
        subscriber_count=2,
        qos_reliability="reliable",
        mode_effective="mock",
    ),
    TopicInfo(
        name="/scan",
        message_type="sensor_msgs/msg/LaserScan",
        publisher_count=1,
        subscriber_count=1,
        qos_reliability="best_effort",
        mode_effective="mock",
    ),
    TopicInfo(
        name="/tf",
        message_type="tf2_msgs/msg/TFMessage",
        publisher_count=3,
        subscriber_count=2,
        qos_reliability="reliable",
        mode_effective="mock",
    ),
    TopicInfo(
        name="/camera/image_raw",
        message_type="sensor_msgs/msg/Image",
        publisher_count=1,
        subscriber_count=1,
        qos_reliability="best_effort",
        mode_effective="mock",
    ),
)


_BASE_TS_NS = 1_700_000_000_000_000_000


_MOCK_SAMPLES: dict[str, list[MessageSample]] = {
    "/cmd_vel": [
        MessageSample(
            topic="/cmd_vel",
            message_type="geometry_msgs/msg/Twist",
            timestamp_ns=_BASE_TS_NS + i * 100_000_000,
            payload={
                "linear": {"x": 0.20 + i * 0.01, "y": 0.0, "z": 0.0},
                "angular": {"x": 0.0, "y": 0.0, "z": 0.05 * i},
            },
        )
        for i in range(5)
    ],
    "/odom": [
        MessageSample(
            topic="/odom",
            message_type="nav_msgs/msg/Odometry",
            timestamp_ns=_BASE_TS_NS + i * 100_000_000,
            payload={
                "header": {"frame_id": "odom", "stamp_sec": 1_700_000_000 + i},
                "pose": {"position": {"x": 0.1 * i, "y": 0.0, "z": 0.0}},
                "twist": {"linear": {"x": 0.2}, "angular": {"z": 0.0}},
            },
        )
        for i in range(5)
    ],
    "/scan": [
        MessageSample(
            topic="/scan",
            message_type="sensor_msgs/msg/LaserScan",
            timestamp_ns=_BASE_TS_NS + i * 50_000_000,
            payload={
                "header": {"frame_id": "laser", "stamp_sec": 1_700_000_000 + i},
                "angle_min": -3.14,
                "angle_max": 3.14,
                "range_min": 0.05,
                "range_max": 12.0,
                "ranges_summary": {"min": 0.32, "max": 11.5, "n": 720},
            },
        )
        for i in range(3)
    ],
    "/tf": [
        MessageSample(
            topic="/tf",
            message_type="tf2_msgs/msg/TFMessage",
            timestamp_ns=_BASE_TS_NS + i * 100_000_000,
            payload={
                "transforms": [
                    {"frame_id": "odom", "child_frame_id": "base_link"},
                    {"frame_id": "base_link", "child_frame_id": "laser"},
                ]
            },
        )
        for i in range(2)
    ],
    "/camera/image_raw": [
        MessageSample(
            topic="/camera/image_raw",
            message_type="sensor_msgs/msg/Image",
            timestamp_ns=_BASE_TS_NS,
            payload={
                "header": {"frame_id": "camera", "stamp_sec": 1_700_000_000},
                "width": 640,
                "height": 480,
                "encoding": "rgb8",
                "data_summary": "<binary 921600 bytes elided>",
            },
        )
    ],
}


def mock_samples_for(topic: str, count: int) -> list[MessageSample]:
    """Return up to `count` deterministic samples for `topic`. Empty if unknown."""
    return list(_MOCK_SAMPLES.get(topic, [])[:count])


MOCK_BAG_ANALYSIS = BagAnalysis(
    path="<mock>",
    storage_format="mcap",
    duration_seconds=42.5,
    message_count=1287,
    topics=[
        BagTopicStats(
            name="/cmd_vel",
            message_type="geometry_msgs/msg/Twist",
            message_count=425,
            frequency_hz=10.0,
        ),
        BagTopicStats(
            name="/odom",
            message_type="nav_msgs/msg/Odometry",
            message_count=425,
            frequency_hz=10.0,
        ),
        BagTopicStats(
            name="/scan",
            message_type="sensor_msgs/msg/LaserScan",
            message_count=425,
            frequency_hz=10.0,
        ),
        BagTopicStats(
            name="/tf",
            message_type="tf2_msgs/msg/TFMessage",
            message_count=12,
            frequency_hz=0.28,
        ),
    ],
    # TODO(roadmap): bag anomaly detection — replace these canned strings with
    # output from a real anomaly detector (clock jumps, frame drops, TF gaps).
    anomalies=[
        "/scan: 3 frames dropped between t=10.1s and t=10.4s",
        "/tf: static transforms only — no dynamic updates during recording",
    ],
    mode_effective="mock",
)
