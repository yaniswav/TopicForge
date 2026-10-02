"""Deterministic fake-robot fixtures used by `MockAdapter`.

A small differential-drive robot with a 2D LIDAR, an RGB camera and a TF
tree. Tests assert on exact values, so changing one here means updating
`tests/`.
"""

from __future__ import annotations

from topicforge.adapters.common.endpoints import build_endpoint_listing
from topicforge.adapters.common.metrics_buffer import MetricsBuffer
from topicforge.adapters.common.qos_scan import scan_endpoints
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
    TopicInfo,
    TopicMetrics,
)

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
        mode_effective="mock",
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
        mode_effective="mock",
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
        mode_effective="mock",
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
        mode_effective="mock",
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
        mode_effective="mock",
    ),
    ParticipantEvent(
        guid="010f1c2a-3b4c-5d6e-7f80-000000000002",
        event_type="discovered",
        vendor="cyclone",
        timestamp_ns=_LIFECYCLE_BASE_TS_NS + 5_000_000_000,
        hostname="mock-laptop",
        name="nav_planner",
        domain_id=0,
        mode_effective="mock",
    ),
    ParticipantEvent(
        guid="010f1c2a-3b4c-5d6e-7f80-000000000003",
        event_type="discovered",
        vendor="fast",
        timestamp_ns=_LIFECYCLE_BASE_TS_NS + 10_000_000_000,
        hostname="mock-aerospace-node",
        name="camera_driver",
        domain_id=0,
        mode_effective="mock",
    ),
    ParticipantEvent(
        guid="010f1c2a-3b4c-5d6e-7f80-000000000004",
        event_type="discovered",
        vendor="dust",
        timestamp_ns=_LIFECYCLE_BASE_TS_NS + 15_000_000_000,
        hostname="mock-rust-node",
        domain_id=0,
        mode_effective="mock",
    ),
)


def mock_participant_events_for(domain_id: int, lookback_seconds: int) -> list[ParticipantEvent]:
    """Mock events for `domain_id`, newest first.

    `now` is pinned two minutes after `_LIFECYCLE_BASE_TS_NS`, so a 60 s
    lookback drops older events and a 300 s lookback returns all of them.
    """
    now_ns = _LIFECYCLE_BASE_TS_NS + 120_000_000_000
    cutoff = now_ns - lookback_seconds * 1_000_000_000
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
    return {
        "guid": f"010f1c2a-3b4c-5d6e-7f80-{participant:04d}{index:08d}",
        "role": role,
        "participant_guid": participant_guid,
        "participant_name": _PARTICIPANT_NAMES.get(participant_guid),
        "participant_vendor": _PARTICIPANT_VENDORS.get(participant_guid, "unknown"),
        "topic": topic,
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
        EndpointInfo(**rec, domain_id=0, mode_effective="mock")  # type: ignore[arg-type]
        for rec in _MOCK_ENDPOINT_RECORDS
    ]
    return scan_endpoints(endpoints, topic=topic, mode_effective="mock")


def mock_endpoint_listing(
    topic: str | None, participant_guid: str | None, include_observer: bool
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
    # `frequency_hz_observed` returns None when fewer than 2 samples.
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


def mock_topic_metrics_for(topic: str, window_seconds: int, domain_id: int) -> TopicMetrics:
    """TopicMetrics from `_MOCK_METRICS_BUFFER`, with `now_ns` pinned to `_METRICS_NOW_NS`."""
    metrics = _MOCK_METRICS_BUFFER.compute_metrics(
        topic=topic,
        window_seconds=window_seconds,
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
    # TODO(roadmap): bag anomaly detection: replace these canned strings with
    # output from a real anomaly detector (clock jumps, frame drops, TF gaps).
    anomalies=[
        "/scan: 3 frames dropped between t=10.1s and t=10.4s",
        "/tf: static transforms only; no dynamic updates during recording",
    ],
    mode_effective="mock",
    bag_format="mcap",
    samples_decoded_count=0,  # analysis does not decode; peek_bag_samples does
    recording_duration_ns=42_500_000_000,  # 42.5s
    participants_recorded=[],
)


# Per-topic samples for peek_bag_samples, on the topics of MOCK_BAG_ANALYSIS,
# each annotated `_decode_status="full"`.
MOCK_BAG_SAMPLES: dict[str, list[MessageSample]] = {
    "/cmd_vel": [
        MessageSample(
            topic="/cmd_vel",
            message_type="geometry_msgs/msg/Twist",
            timestamp_ns=_BASE_TS_NS + i * 100_000_000,
            payload={
                "_decode_status": "full",
                "linear": {"x": 0.20 + i * 0.01, "y": 0.0, "z": 0.0},
                "angular": {"x": 0.0, "y": 0.0, "z": 0.05 * i},
                "_msgtype": "geometry_msgs/msg/Twist",
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
                "_decode_status": "full",
                "header": {"frame_id": "odom"},
                "pose": {"position": {"x": 0.1 * i, "y": 0.0, "z": 0.0}},
                "_msgtype": "nav_msgs/msg/Odometry",
            },
        )
        for i in range(3)
    ],
}


def mock_bag_samples_for(topic: str, count: int) -> list[MessageSample]:
    """Up to `count` mock samples for `topic`; empty for an unknown topic."""
    return list(MOCK_BAG_SAMPLES.get(topic, [])[:count])
