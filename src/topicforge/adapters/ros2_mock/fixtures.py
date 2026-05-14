"""Deterministic fake-robot fixtures used by `MockAdapter`.

These model a small differential-drive mobile robot with a 2D LIDAR, an
RGB camera, and a TF tree. The data is rich enough to make demos and
screenshots believable, and stable enough for tests to assert on exact
values.

If you change a value here, expect to update tests under `tests/`.
"""

from __future__ import annotations

from topicforge.models import (
    BagAnalysis,
    BagTopicStats,
    MessageSample,
    MismatchReport,
    ParticipantEvent,
    ParticipantInfo,
    QosProfile,
    SampleResult,
    TopicInfo,
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


# ---------------------------------------------------------------------------
# DDS module fixtures — exercise list_participants, detect_qos_mismatches,
# peek_dds_samples. Two participants on a single domain ; one well-matched
# topic (reader & writer compatible) and one deliberately mismatched topic
# (Reliability incompatibility, since RELIABLE reader cannot match a
# BEST_EFFORT writer).
# ---------------------------------------------------------------------------

# v0.4.0 Phase 1 — deterministic lifecycle timeline anchored on this
# wall-clock value (2024-01-01T00:00:00Z) so tests can assert exact
# first_seen / last_seen / event timestamps without relying on the
# system clock. The chosen base sits comfortably inside any plausible
# `lookback_seconds` window from "today" used in real LLM sessions.
_LIFECYCLE_BASE_TS_NS = 1_704_067_200_000_000_000

MOCK_PARTICIPANTS: tuple[ParticipantInfo, ...] = (
    ParticipantInfo(
        guid="010f1c2a-3b4c-5d6e-7f80-000000000001",
        vendor="cyclone",
        hostname="mock-robot",
        domain_id=0,
        mode_effective="mock",
        first_seen_ns=_LIFECYCLE_BASE_TS_NS,
        last_seen_ns=_LIFECYCLE_BASE_TS_NS + 60_000_000_000,
        status="active",
        seen_count=3,
    ),
    ParticipantInfo(
        guid="010f1c2a-3b4c-5d6e-7f80-000000000002",
        vendor="cyclone",
        hostname="mock-laptop",
        domain_id=0,
        mode_effective="mock",
        first_seen_ns=_LIFECYCLE_BASE_TS_NS + 5_000_000_000,
        last_seen_ns=_LIFECYCLE_BASE_TS_NS + 55_000_000_000,
        status="active",
        seen_count=2,
    ),
    # v0.3.0: third participant exercises the multi-vendor positioning —
    # an eProsima Fast DDS participant alongside Cyclone, as the OMG-DDS
    # interop matrix promises (see docs/dds-interop-matrix.md).
    ParticipantInfo(
        guid="010f1c2a-3b4c-5d6e-7f80-000000000003",
        vendor="fast",
        hostname="mock-aerospace-node",
        domain_id=0,
        mode_effective="mock",
        first_seen_ns=_LIFECYCLE_BASE_TS_NS + 10_000_000_000,
        last_seen_ns=_LIFECYCLE_BASE_TS_NS + 50_000_000_000,
        status="active",
        seen_count=2,
    ),
)

# Deterministic lifecycle log for the same scenario. Three discovery
# events ordered by timestamp, no `lost` event (steady-state demo).
MOCK_PARTICIPANT_EVENTS: tuple[ParticipantEvent, ...] = (
    ParticipantEvent(
        guid="010f1c2a-3b4c-5d6e-7f80-000000000001",
        event_type="discovered",
        vendor="cyclone",
        timestamp_ns=_LIFECYCLE_BASE_TS_NS,
        hostname="mock-robot",
        domain_id=0,
        mode_effective="mock",
    ),
    ParticipantEvent(
        guid="010f1c2a-3b4c-5d6e-7f80-000000000002",
        event_type="discovered",
        vendor="cyclone",
        timestamp_ns=_LIFECYCLE_BASE_TS_NS + 5_000_000_000,
        hostname="mock-laptop",
        domain_id=0,
        mode_effective="mock",
    ),
    ParticipantEvent(
        guid="010f1c2a-3b4c-5d6e-7f80-000000000003",
        event_type="discovered",
        vendor="fast",
        timestamp_ns=_LIFECYCLE_BASE_TS_NS + 10_000_000_000,
        hostname="mock-aerospace-node",
        domain_id=0,
        mode_effective="mock",
    ),
)


def mock_participant_events_for(domain_id: int, lookback_seconds: int) -> list[ParticipantEvent]:
    """Return mock events filtered by `domain_id`, ordered newest-first.

    `lookback_seconds` is honored deterministically against the fixture
    timeline: events older than `MOCK_PARTICIPANT_EVENTS_NOW_NS -
    lookback_seconds * 1e9` are dropped. The fixture anchors `now` at
    `_LIFECYCLE_BASE_TS_NS + 120_000_000_000` (two minutes after the
    base) so a 60s lookback returns only events younger than that
    horizon ; a 300s lookback returns everything.
    """
    now_ns = _LIFECYCLE_BASE_TS_NS + 120_000_000_000
    cutoff = now_ns - lookback_seconds * 1_000_000_000
    filtered = [
        e for e in MOCK_PARTICIPANT_EVENTS if e.domain_id == domain_id and e.timestamp_ns >= cutoff
    ]
    filtered.sort(key=lambda e: e.timestamp_ns, reverse=True)
    return filtered


# MOCK_DDS_TOPICS — v0.4.0 Phase 1 adds two user-topic fixtures
# exercising the XTypes/IDL decode paths in `peek_dds_samples`:
#   * `/dds/ddsforge/example`     — `_decode_status="full"` path
#   * `/dds/ddsforge/opaque`      — `_decode_status="raw"` fallback path
# The two existing topics (well_matched / qos_mismatch) keep the v0.3.0
# builtin-style payload (no `_decode_status` key) so backward
# compatibility with the v0.3.0 wire contract is preserved.
MOCK_DDS_TOPICS: tuple[str, ...] = (
    "/dds/well_matched",
    "/dds/qos_mismatch",
    "/dds/ddsforge/example",
    "/dds/ddsforge/opaque",
)

_MOCK_MISMATCHES: tuple[MismatchReport, ...] = (
    MismatchReport(
        topic="/dds/qos_mismatch",
        reader_guid="010f1c2a-3b4c-5d6e-7f80-000000000001",
        writer_guid="010f1c2a-3b4c-5d6e-7f80-000000000002",
        incompatible_policies=["Reliability"],
        severity="incompatible",
        mode_effective="mock",
    ),
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
        # The mismatched topic still has a writer producing samples — the
        # mismatch only prevents one reader from matching, not the bus
        # from carrying traffic.
        samples = [
            MessageSample(
                topic=topic,
                message_type="dds/Heartbeat",
                timestamp_ns=_BASE_TS_NS,
                payload={"seq": 0, "vendor": "cyclone", "qos_note": "writer is BEST_EFFORT"},
            )
        ][:count]
    elif topic == "/dds/ddsforge/example":
        # v0.4.0 Phase 1 — user topic with `_decode_status="full"`. The
        # IDL is synthetic: a struct{ uint32 seq; string status; float32
        # battery_pct; } resolved cleanly by `cyclonedds.dynamic` /
        # `fastdds.DynamicData` (in the real adapters).
        from topicforge.adapters.common.xtypes import annotate_full

        samples = [
            MessageSample(
                topic=topic,
                message_type="ddsforge/Example",
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
    elif topic == "/dds/ddsforge/opaque":
        # v0.4.0 Phase 1 — user topic with `_decode_status="raw"`. Models
        # the binding-XTypes-unavailable fallback path: payload bytes
        # preserved as hex with a short diagnostic note.
        from topicforge.adapters.common.xtypes import annotate_raw

        synthetic_bytes = bytes.fromhex("deadbeefcafebabe")
        samples = [
            MessageSample(
                topic=topic,
                message_type="ddsforge/Opaque",
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


def mock_mismatches_for(topic: str | None) -> list[MismatchReport]:
    """Return the mock mismatches filtered by topic. None returns all."""
    if topic is None:
        return list(_MOCK_MISMATCHES)
    return [m for m in _MOCK_MISMATCHES if m.topic == topic]


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
