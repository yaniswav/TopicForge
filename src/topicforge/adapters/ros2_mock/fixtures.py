"""Deterministic fake-robot fixtures used by `MockAdapter`.

A small differential-drive robot with a 2D LIDAR, an RGB camera and a TF
tree. Tests assert on exact values, so changing one here means updating
`tests/`.
"""

from __future__ import annotations

import math

from topicforge.adapters.common.endpoints import build_endpoint_listing
from topicforge.adapters.common.metrics_buffer import MetricsBuffer
from topicforge.adapters.common.qos_scan import scan_endpoints
from topicforge.adapters.common.ros_names import ros_topic_of
from topicforge.models import (
    BagAnalysis,
    BagTopicStats,
    EndpointInfo,
    EndpointListing,
    MessageSample,
    MismatchScan,
    ParticipantEvent,
    ParticipantInfo,
    QosProfile,
    SampleResult,
    SideQos,
    TopicInfo,
    TopicListItem,
    TopicMetrics,
    TopicRate,
)


def _side(reliability: str, count: int, durability: str = "volatile") -> SideQos:
    return SideQos(
        reliability=reliability,  # type: ignore[arg-type]
        durability=durability,  # type: ignore[arg-type]
        endpoint_count=count,
    )


def _topic(
    name: str,
    message_type: str,
    reliability: str,
    publishers: tuple[str, ...],
    subscribers: tuple[str, ...],
) -> TopicInfo:
    """A mock topic whose both sides use `reliability` (volatile), one endpoint per node."""
    return TopicInfo(
        name=name,
        message_type=message_type,
        publisher_count=len(publishers),
        subscriber_count=len(subscribers),
        publisher_qos=_side(reliability, len(publishers)),
        subscription_qos=_side(reliability, len(subscribers)),
        publisher_nodes=list(publishers),
        subscriber_nodes=list(subscribers),
        mode_effective="mock",
    )


MOCK_TOPICS: tuple[TopicInfo, ...] = (
    _topic(
        "/cmd_vel",
        "geometry_msgs/msg/Twist",
        "reliable",
        ("/nav_planner",),
        ("/base_controller",),
    ),
    _topic(
        "/odom",
        "nav_msgs/msg/Odometry",
        "reliable",
        ("/base_controller",),
        ("/nav_planner", "/robot_state_publisher"),
    ),
    _topic(
        "/scan",
        "sensor_msgs/msg/LaserScan",
        "best_effort",
        ("/lidar_driver",),
        ("/nav_planner",),
    ),
    _topic(
        "/imu/data",
        "sensor_msgs/msg/Imu",
        "best_effort",
        ("/imu_driver",),
        ("/nav_planner",),
    ),
    _topic(
        "/tf",
        "tf2_msgs/msg/TFMessage",
        "reliable",
        ("/base_controller", "/robot_state_publisher", "/nav_planner"),
        ("/nav_planner", "/camera_driver"),
    ),
    _topic(
        "/camera/image_raw",
        "sensor_msgs/msg/Image",
        "best_effort",
        ("/camera_driver",),
        ("/nav_planner",),
    ),
)

MOCK_TOPIC_ITEMS: tuple[TopicListItem, ...] = tuple(
    TopicListItem(
        name=t.name,
        message_type=t.message_type,
        publisher_count=t.publisher_count,
        subscriber_count=t.subscriber_count,
    )
    for t in MOCK_TOPICS
)


_BASE_TS_NS = 1_700_000_000_000_000_000


def _stamp(ts_ns: int) -> dict[str, int]:
    """A `builtin_interfaces/Time` payload, as `ros2 topic echo` prints it."""
    return {"sec": ts_ns // 1_000_000_000, "nanosec": ts_ns % 1_000_000_000}


# The mock robot publishes /scan, /odom, /imu/data and /cmd_vel at 10 Hz; a mock
# message "arrives" this long after its stamp.
_MOCK_PERIOD_NS = 100_000_000
_MOCK_LATENCY_NS = 3_000_000


def _arrival(i: int) -> int:
    return _BASE_TS_NS + i * _MOCK_PERIOD_NS + _MOCK_LATENCY_NS


def _mock_scan_ranges() -> list[object]:
    """720 beams over -pi..pi (sensor frame, +x forward) in a walled room, with an open door.

    The robot stands 3.0 m from the front wall, 2.0 m from the left wall, 1.5 m from the
    right wall and 2.5 m from the rear wall. Beams between 100 and 110 degrees pass through
    a doorway and read `inf` (the string, as in a live payload).
    """
    ranges: list[object] = []
    for i in range(720):
        theta = -math.pi + i * (2 * math.pi / 720)
        dx, dy = math.cos(theta), math.sin(theta)
        distances = []
        if dx > 1e-9:
            distances.append(3.0 / dx)
        elif dx < -1e-9:
            distances.append(-2.5 / dx)
        if dy > 1e-9:
            distances.append(2.0 / dy)
        elif dy < -1e-9:
            distances.append(-1.5 / dy)
        through_door = 100.0 <= math.degrees(theta) <= 110.0
        ranges.append("inf" if through_door else round(min(distances), 4))
    return ranges


def _quaternion(yaw: float) -> dict[str, float]:
    return {"x": 0.0, "y": 0.0, "z": round(math.sin(yaw / 2), 6), "w": round(math.cos(yaw / 2), 6)}


_MOCK_SAMPLES: dict[str, list[MessageSample]] = {
    "/cmd_vel": [
        MessageSample(
            topic="/cmd_vel",
            message_type="geometry_msgs/msg/Twist",
            timestamp_ns=0,
            stamp_source="none",
            received_ns=_arrival(i),
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
            timestamp_ns=_BASE_TS_NS + i * _MOCK_PERIOD_NS,
            stamp_source="header",
            received_ns=_arrival(i),
            payload={
                "header": {"stamp": _stamp(_BASE_TS_NS + i * _MOCK_PERIOD_NS), "frame_id": "odom"},
                "child_frame_id": "base_link",
                "pose": {
                    "pose": {
                        "position": {"x": 0.02 * i, "y": 0.0, "z": 0.0},
                        "orientation": _quaternion(0.0),
                    },
                },
                "twist": {
                    "twist": {
                        "linear": {"x": 0.2, "y": 0.0, "z": 0.0},
                        "angular": {"x": 0.0, "y": 0.0, "z": 0.0},
                    },
                },
            },
        )
        for i in range(5)
    ],
    # The payload holds the whole scan; `mock_samples_for` summarizes it and cuts it.
    "/scan": [
        MessageSample(
            topic="/scan",
            message_type="sensor_msgs/msg/LaserScan",
            timestamp_ns=_BASE_TS_NS + i * _MOCK_PERIOD_NS,
            stamp_source="header",
            received_ns=_arrival(i),
            payload={
                "header": {"stamp": _stamp(_BASE_TS_NS + i * _MOCK_PERIOD_NS), "frame_id": "laser"},
                "angle_min": -math.pi,
                "angle_max": math.pi - 2 * math.pi / 720,
                "angle_increment": 2 * math.pi / 720,
                "time_increment": 0.0,
                "scan_time": 0.1,
                "range_min": 0.05,
                "range_max": 12.0,
                "ranges": _mock_scan_ranges(),
            },
        )
        for i in range(5)
    ],
    "/imu/data": [
        MessageSample(
            topic="/imu/data",
            message_type="sensor_msgs/msg/Imu",
            timestamp_ns=_BASE_TS_NS + i * _MOCK_PERIOD_NS,
            stamp_source="header",
            received_ns=_arrival(i),
            payload={
                "header": {"stamp": _stamp(_BASE_TS_NS + i * _MOCK_PERIOD_NS), "frame_id": "imu"},
                "orientation": _quaternion(0.0),
                "orientation_covariance": [0.0] * 9,
                "angular_velocity": {"x": 0.0, "y": 0.0, "z": 0.0},
                "angular_velocity_covariance": [0.0] * 9,
                "linear_acceleration": {"x": 0.0, "y": 0.0, "z": 9.81},
                "linear_acceleration_covariance": [0.0] * 9,
            },
        )
        for i in range(5)
    ],
    "/tf": [
        MessageSample(
            topic="/tf",
            message_type="tf2_msgs/msg/TFMessage",
            timestamp_ns=_BASE_TS_NS + i * _MOCK_PERIOD_NS,
            stamp_source="payload",
            received_ns=_arrival(i),
            payload={
                "transforms": [
                    {
                        "header": {
                            "stamp": _stamp(_BASE_TS_NS + i * _MOCK_PERIOD_NS),
                            "frame_id": a,
                        },
                        "child_frame_id": b,
                    }
                    for a, b in (("odom", "base_link"), ("base_link", "laser"))
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
            stamp_source="header",
            received_ns=_arrival(0),
            payload={
                "header": {"stamp": _stamp(_BASE_TS_NS), "frame_id": "camera"},
                "width": 640,
                "height": 480,
                "encoding": "rgb8",
                "is_bigendian": 0,
                "step": 1920,
                "data": "<sequence type: uint8, length: 921600>",
            },
        )
    ],
}


def mock_samples_for(
    topic: str,
    count: int,
    *,
    max_array_length: int | None = 128,
    arrays_summary_only: bool = False,
) -> list[MessageSample]:
    """Up to `count` deterministic samples for `topic`, as live mode would return them.

    The summary is computed on the whole message, then the payload is cut at
    `max_array_length` (or its long arrays shown as text with `arrays_summary_only`).
    Empty if the topic is unknown.
    """
    # Imported here: loading `topicforge.services` imports the adapters.
    from topicforge.adapters.ros2_live.echo_parser import cut_payload
    from topicforge.services.summaries import summarize_message

    out: list[MessageSample] = []
    for sample in _MOCK_SAMPLES.get(topic, [])[:count]:
        if arrays_summary_only:
            payload = _arrays_as_text(sample.payload)
            summary = summarize_message(sample.message_type, payload)
        else:
            summary = summarize_message(sample.message_type, sample.payload)
            payload = cut_payload(dict(sample.payload), max_array_length)
        out.append(sample.model_copy(update={"payload": payload, "summary": summary}))
    return out


def mock_sample_rate(samples: list[MessageSample]) -> TopicRate | None:
    """Rate block of mock samples, on their synthetic arrival times; `None` without any."""
    from topicforge.services.summaries import compute_rate

    arrivals = [s.received_ns for s in samples if s.received_ns is not None]
    if not arrivals:
        return None
    stamped = all(s.stamp_source in ("header", "payload") for s in samples)
    return compute_rate(
        arrivals,
        basis="received_ns",
        stamps_ns=[s.timestamp_ns for s in samples] if stamped else None,
    )


def _arrays_as_text(payload: dict[str, object]) -> dict[str, object]:
    """Show every array of more than 9 elements as the CLI's `<sequence ...>` text."""
    return {
        key: f"<sequence type: float, length: {len(value)}>"
        if isinstance(value, list) and len(value) > 9
        else value
        for key, value in payload.items()
    }


# DDS fixtures: participants on one domain, one well-matched topic and one
# with a Reliability mismatch (RELIABLE reader, BEST_EFFORT writer).

# Lifecycle timeline anchor (2024-01-01T00:00:00Z), so tests can assert exact
# timestamps without the system clock.
_LIFECYCLE_BASE_TS_NS = 1_704_067_200_000_000_000

MOCK_PARTICIPANTS: tuple[ParticipantInfo, ...] = (
    ParticipantInfo(
        guid="010f1c2a-3b4c-5d6e-7f80-000000000001",
        vendor="cyclone",
        hostname="mock-robot",
        name="lidar_driver",
        domain_id=0,
        first_seen_ns=_LIFECYCLE_BASE_TS_NS,
        last_seen_ns=_LIFECYCLE_BASE_TS_NS + 60_000_000_000,
        status="active",
        seen_count=3,
        vendor_source="guid_prefix",
    ),
    ParticipantInfo(
        guid="010f1c2a-3b4c-5d6e-7f80-000000000002",
        vendor="cyclone",
        hostname="mock-laptop",
        name="nav_planner",
        domain_id=0,
        first_seen_ns=_LIFECYCLE_BASE_TS_NS + 5_000_000_000,
        last_seen_ns=_LIFECYCLE_BASE_TS_NS + 55_000_000_000,
        status="active",
        seen_count=2,
        vendor_source="guid_prefix",
    ),
    # An eProsima Fast DDS participant next to Cyclone (multi-vendor demo).
    ParticipantInfo(
        guid="010f1c2a-3b4c-5d6e-7f80-000000000003",
        vendor="fast",
        hostname="mock-aerospace-node",
        name="camera_driver",
        domain_id=0,
        first_seen_ns=_LIFECYCLE_BASE_TS_NS + 10_000_000_000,
        last_seen_ns=_LIFECYCLE_BASE_TS_NS + 50_000_000_000,
        status="active",
        seen_count=2,
        vendor_source="guid_prefix",
    ),
    # A Dust DDS participant (S2E, vendor_id 01.14): three stacks on one bus.
    ParticipantInfo(
        guid="010f1c2a-3b4c-5d6e-7f80-000000000004",
        vendor="dust",
        hostname="mock-rust-node",
        domain_id=0,
        first_seen_ns=_LIFECYCLE_BASE_TS_NS + 15_000_000_000,
        last_seen_ns=_LIFECYCLE_BASE_TS_NS + 45_000_000_000,
        status="active",
        seen_count=2,
        vendor_source="guid_prefix",
    ),
)

# Lifecycle log for the same scenario: four discovery events, no `lost`.
MOCK_PARTICIPANT_EVENTS: tuple[ParticipantEvent, ...] = (
    ParticipantEvent(
        guid="010f1c2a-3b4c-5d6e-7f80-000000000001",
        event_type="discovered",
        vendor="cyclone",
        timestamp_ns=_LIFECYCLE_BASE_TS_NS,
        hostname="mock-robot",
        name="lidar_driver",
        domain_id=0,
    ),
    ParticipantEvent(
        guid="010f1c2a-3b4c-5d6e-7f80-000000000002",
        event_type="discovered",
        vendor="cyclone",
        timestamp_ns=_LIFECYCLE_BASE_TS_NS + 5_000_000_000,
        hostname="mock-laptop",
        name="nav_planner",
        domain_id=0,
    ),
    ParticipantEvent(
        guid="010f1c2a-3b4c-5d6e-7f80-000000000003",
        event_type="discovered",
        vendor="fast",
        timestamp_ns=_LIFECYCLE_BASE_TS_NS + 10_000_000_000,
        hostname="mock-aerospace-node",
        name="camera_driver",
        domain_id=0,
    ),
    ParticipantEvent(
        guid="010f1c2a-3b4c-5d6e-7f80-000000000004",
        event_type="discovered",
        vendor="dust",
        timestamp_ns=_LIFECYCLE_BASE_TS_NS + 15_000_000_000,
        hostname="mock-rust-node",
        domain_id=0,
    ),
)


def mock_participant_events_for(domain_id: int, lookback_s: int) -> list[ParticipantEvent]:
    """Mock events for `domain_id`, newest first.

    `now` is pinned two minutes after `_LIFECYCLE_BASE_TS_NS`, so a 60 s
    lookback drops older events and a 300 s lookback returns all of them.
    """
    now_ns = _LIFECYCLE_BASE_TS_NS + 120_000_000_000
    cutoff = now_ns - lookback_s * 1_000_000_000
    filtered = [
        e for e in MOCK_PARTICIPANT_EVENTS if e.domain_id == domain_id and e.timestamp_ns >= cutoff
    ]
    filtered.sort(key=lambda e: e.timestamp_ns, reverse=True)
    return filtered


# MOCK_DDS_TOPICS includes two user-topic fixtures for the decode paths of
# `peek_dds_samples`: `/dds/topicforge/example` (`_decode_status="full"`) and
# `/dds/topicforge/opaque` (`"raw"`). The other two topics have no
# `_decode_status` key.
MOCK_DDS_TOPICS: tuple[str, ...] = (
    "/dds/well_matched",
    "/dds/qos_mismatch",
    "/dds/topicforge/example",
    "/dds/topicforge/opaque",
)


def mock_qos_for(topic: str) -> QosProfile | None:
    """Return a deterministic QoS profile for a mock DDS topic, else None."""
    if topic == "/dds/well_matched":
        return QosProfile(
            reliability="RELIABLE",
            durability="VOLATILE",
            history="KEEP_LAST",
            history_depth=10,
            deadline_ns=None,
        )
    if topic == "/dds/qos_mismatch":
        return QosProfile(
            reliability="BEST_EFFORT",
            durability="VOLATILE",
            history="KEEP_LAST",
            history_depth=10,
            deadline_ns=None,
        )
    return None


def mock_dds_samples_for(topic: str, count: int) -> SampleResult:
    """Deterministic DDS samples for a mock DDS topic, wrapped in SampleResult."""
    if topic == "/dds/well_matched":
        samples = [
            MessageSample(
                topic=topic,
                message_type="dds/Heartbeat",
                timestamp_ns=_BASE_TS_NS + i * 100_000_000,
                stamp_source="dds_source",
                payload={"seq": i, "vendor": "cyclone"},
            )
            for i in range(min(count, 3))
        ]
    elif topic == "/dds/qos_mismatch":
        # The writer still produces samples; the mismatch only stops one
        # reader from matching.
        samples = [
            MessageSample(
                topic=topic,
                message_type="dds/Heartbeat",
                timestamp_ns=_BASE_TS_NS,
                stamp_source="dds_source",
                payload={"seq": 0, "vendor": "cyclone", "qos_note": "writer is BEST_EFFORT"},
            )
        ][:count]
    elif topic == "/dds/topicforge/example":
        # Fully decoded user topic: synthetic struct{ uint32 seq; string status;
        # float32 battery_pct; }.
        from topicforge.adapters.common.xtypes import annotate_full

        samples = [
            MessageSample(
                topic=topic,
                message_type="topicforge/Example",
                timestamp_ns=_BASE_TS_NS + i * 200_000_000,
                stamp_source="dds_source",
                payload=annotate_full(
                    {
                        "seq": i,
                        "status": f"ok-{i}",
                        "battery_pct": 92.5 - i * 1.5,
                    }
                ),
            )
            for i in range(min(count, 3))
        ]
    elif topic == "/dds/topicforge/opaque":
        # Undecoded user topic: bytes kept as hex with a diagnostic note.
        from topicforge.adapters.common.xtypes import annotate_raw

        synthetic_bytes = bytes.fromhex("deadbeefcafebabe")
        samples = [
            MessageSample(
                topic=topic,
                message_type="topicforge/Opaque",
                timestamp_ns=_BASE_TS_NS,
                stamp_source="dds_source",
                payload=annotate_raw(
                    synthetic_bytes,
                    note="binding XTypes unavailable (mock fallback fixture)",
                ),
            )
        ][:count]
    else:
        samples = []
    return SampleResult(
        topic=topic,
        count=len(samples),
        samples=samples,
        mode_effective="mock",
    )


# Endpoint fixtures (`list_endpoints`), same scenario as above: nav_planner writes
# `/dds/qos_mismatch` BEST_EFFORT while lidar_driver reads it RELIABLE (the
# `Reliability` mismatch), `/dds/topicforge/opaque` has a writer and no reader (an
# orphan), and the dust writer carries partition, manual liveliness and
# exclusive ownership so those fields are exercised.
MOCK_OBSERVER_GUID = "010f1c2a-3b4c-5d6e-7f80-000000000099"

_PARTICIPANT_NAMES: dict[str, str | None] = {p.guid: p.name for p in MOCK_PARTICIPANTS}
_PARTICIPANT_VENDORS: dict[str, str] = {p.guid: p.vendor for p in MOCK_PARTICIPANTS}


def _mock_endpoint(
    index: int,
    participant: int,
    role: str,
    topic: str,
    type_name: str,
    qos: QosProfile,
    announced_offset_s: int,
) -> dict[str, object]:
    participant_guid = f"010f1c2a-3b4c-5d6e-7f80-{participant:012d}"
    ros_topic, ros_topic_note = ros_topic_of(topic)
    return {
        "guid": f"010f1c2a-3b4c-5d6e-7f80-{participant:04d}{index:08d}",
        "role": role,
        "participant_guid": participant_guid,
        "participant_name": _PARTICIPANT_NAMES.get(participant_guid),
        "participant_vendor": _PARTICIPANT_VENDORS.get(participant_guid, "unknown"),
        "dds_topic": topic,
        "ros_topic": ros_topic,
        "ros_topic_note": ros_topic_note,
        "type_name": type_name,
        "type_id": None,
        "qos": qos,
        "announced_ns": _LIFECYCLE_BASE_TS_NS + announced_offset_s * 1_000_000_000,
        "is_observer": False,
    }


def _qos(reliability: str, **extra: object) -> QosProfile:
    extra.setdefault("partitions", [""])
    return QosProfile(
        reliability=reliability,  # type: ignore[arg-type]
        durability="VOLATILE",
        history="KEEP_LAST",
        history_depth=10,
        **extra,  # type: ignore[arg-type]
    )


_MOCK_ENDPOINT_RECORDS: tuple[dict[str, object], ...] = (
    _mock_endpoint(1, 2, "writer", "/dds/well_matched", "dds/Heartbeat", _qos("RELIABLE"), 6),
    _mock_endpoint(2, 1, "reader", "/dds/well_matched", "dds/Heartbeat", _qos("RELIABLE"), 2),
    _mock_endpoint(3, 2, "writer", "/dds/qos_mismatch", "dds/Heartbeat", _qos("BEST_EFFORT"), 7),
    _mock_endpoint(4, 1, "reader", "/dds/qos_mismatch", "dds/Heartbeat", _qos("RELIABLE"), 3),
    _mock_endpoint(
        5, 3, "writer", "/dds/topicforge/example", "topicforge/Example", _qos("RELIABLE"), 11
    ),
    _mock_endpoint(
        6, 2, "reader", "/dds/topicforge/example", "topicforge/Example", _qos("RELIABLE"), 8
    ),
    _mock_endpoint(
        7,
        4,
        "writer",
        "/dds/topicforge/opaque",
        "topicforge/Opaque",
        _qos(
            "RELIABLE",
            partitions=["left"],
            liveliness_kind="MANUAL_BY_TOPIC",
            liveliness_lease_ns=500_000_000,
            ownership_kind="EXCLUSIVE",
            ownership_strength=10,
        ),
        16,
    ),
)


def mock_mismatch_scan(topic: str | None) -> MismatchScan:
    """`MismatchScan` for the mock scenario.

    Runs the real scan over the endpoint fixtures, so mock and live share the
    rules: `/dds/qos_mismatch` gives one `Reliability` report, the opaque
    topic an orphan hint.
    """
    endpoints = [
        EndpointInfo(**rec)  # type: ignore[arg-type]
        for rec in _MOCK_ENDPOINT_RECORDS
    ]
    return scan_endpoints(endpoints, topic=topic, mode_effective="mock")


def mock_endpoint_listing(
    topic: str | None,
    participant_guid: str | None,
    include_observer: bool,
    include_internal: bool = False,
) -> EndpointListing:
    """Deterministic `EndpointListing` for the mock scenario, filtered like the live one."""
    return build_endpoint_listing(
        _MOCK_ENDPOINT_RECORDS,
        domain_id=0,
        mode_effective="mock",
        observer_guid=MOCK_OBSERVER_GUID,
        topic=topic,
        participant_guid=participant_guid,
        include_observer=include_observer,
        include_internal=include_internal,
        snapshot_ns=_LIFECYCLE_BASE_TS_NS + 60_000_000_000,
    )


# Topic metrics fixtures: a MetricsBuffer built at import with a 10 Hz stream
# on `/dds/heartbeat_10hz` (100 samples from `_METRICS_BASE_TS_NS`, each with
# a sequence number and a publish time 50 ms before receipt) plus a few topics
# with no data.
_METRICS_BASE_TS_NS = 1_704_067_200_000_000_000  # same anchor as lifecycle
_METRICS_NOW_NS = _METRICS_BASE_TS_NS + 10_000_000_000  # 10 s after first sample


def _build_mock_metrics_buffer() -> MetricsBuffer:
    buf = MetricsBuffer()
    for i in range(100):
        receive_ns = _METRICS_BASE_TS_NS + i * 100_000_000  # 100 ms spacing
        buf.record(
            topic="/dds/heartbeat_10hz",
            receive_ns=receive_ns,
            sequence_number=i,
            publish_ns=receive_ns - 50_000_000,  # 50 ms before receive
            domain_id=0,
        )
    # A second topic with a single sample: tests that
    # `observed_frequency_hz` returns None when fewer than 2 samples.
    buf.record(
        topic="/dds/singleton",
        receive_ns=_METRICS_BASE_TS_NS,
        sequence_number=0,
        publish_ns=None,
        domain_id=0,
    )
    # A topic on a different domain: domain filtering.
    buf.record(
        topic="/dds/cross_domain",
        receive_ns=_METRICS_BASE_TS_NS,
        sequence_number=0,
        publish_ns=None,
        domain_id=42,
    )
    return buf


_MOCK_METRICS_BUFFER: MetricsBuffer = _build_mock_metrics_buffer()

# Declared (Deadline-derived) rate of the one mock writer that announces one.
_MOCK_DECLARED_HZ: dict[str, float] = {"/dds/heartbeat_10hz": 10.0}


def mock_topic_metrics_for(topic: str, window_s: int, domain_id: int) -> TopicMetrics:
    """TopicMetrics from `_MOCK_METRICS_BUFFER`, with `now_ns` pinned to `_METRICS_NOW_NS`."""
    metrics = _MOCK_METRICS_BUFFER.compute_metrics(
        topic=topic,
        window_s=window_s,
        now_ns=_METRICS_NOW_NS,
        declared_hz=_MOCK_DECLARED_HZ.get(topic),
        mode_effective="mock",
        domain_id=domain_id,
    )
    status = "ok" if metrics.samples_observed > 0 else "no_samples_yet"
    return metrics.model_copy(update={"status": status})


MOCK_BAG_ANALYSIS = BagAnalysis(
    path="<mock>",
    storage_format="mcap",
    duration_s=42.5,
    message_count=1288,
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
        BagTopicStats(
            name="/events/write_split",
            message_type="rosbag2_interfaces/msg/WriteSplitEvent",
            message_count=1,
            kind="rosbag2_internal",
        ),
    ],
    # TODO(roadmap): bag anomaly detection: replace these canned strings with
    # output from a real anomaly detector (clock jumps, frame drops, TF gaps).
    anomalies=[
        "/scan: 3 frames dropped between t=10.1s and t=10.4s",
        "/tf: static transforms only; no dynamic updates during recording",
    ],
    mode_effective="mock",
    bag_format="mcap",
)


# Per-topic samples for peek_bag_samples, on the topics of MOCK_BAG_ANALYSIS,
# each annotated `_decode_status="full"`.
MOCK_BAG_SAMPLES: dict[str, list[MessageSample]] = {
    "/cmd_vel": [
        MessageSample(
            topic="/cmd_vel",
            message_type="geometry_msgs/msg/Twist",
            timestamp_ns=_BASE_TS_NS + i * 100_000_000,
            stamp_source="recorded",
            recorded_ns=_BASE_TS_NS + i * 100_000_000,
            payload={
                "_decode_status": "full",
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
            stamp_source="header",
            recorded_ns=_BASE_TS_NS + i * 100_000_000 + 2_000_000,
            payload={
                "_decode_status": "full",
                "header": {
                    "stamp": {
                        "sec": (_BASE_TS_NS + i * 100_000_000) // 1_000_000_000,
                        "nanosec": (_BASE_TS_NS + i * 100_000_000) % 1_000_000_000,
                    },
                    "frame_id": "odom",
                },
                "child_frame_id": "base_link",
                "pose": {
                    "pose": {
                        "position": {"x": 0.02 * i, "y": 0.0, "z": 0.0},
                        "orientation": _quaternion(0.0),
                    },
                },
                "twist": {
                    "twist": {
                        "linear": {"x": 0.2, "y": 0.0, "z": 0.0},
                        "angular": {"x": 0.0, "y": 0.0, "z": 0.0},
                    },
                },
            },
        )
        for i in range(5)
    ],
}


def mock_bag_samples_for(topic: str, count: int) -> list[MessageSample]:
    """Up to `count` mock samples for `topic`, with their summaries; empty for an unknown topic."""
    from topicforge.services.summaries import summarize_message

    return [
        s.model_copy(update={"summary": summarize_message(s.message_type, s.payload)})
        for s in MOCK_BAG_SAMPLES.get(topic, [])[:count]
    ]


def mock_bag_rate(samples: list[MessageSample]) -> TopicRate | None:
    """Rate block of mock bag samples on their record time; `None` without samples."""
    from topicforge.services.summaries import compute_rate

    recorded = [s.recorded_ns for s in samples if s.recorded_ns is not None]
    return compute_rate(recorded, basis="recorded_ns") if recorded else None
