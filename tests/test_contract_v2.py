"""Behaviour pinned by the 0.7.0 output contract that no other test file covers."""

from __future__ import annotations

import pytest

from topicforge.adapters.common.bag_kind import classify_bag_topic
from topicforge.adapters.common.ros_names import is_internal_dds_topic, ros_topic_of
from topicforge.adapters.ros2_mock import MockAdapter
from topicforge.constants import MAX_PARTICIPANT_EVENTS
from topicforge.models import MessageSample, ParticipantEvent
from topicforge.services import HealthService, Inspector
from topicforge.services.health import resolve_rmw

# ---- health_check ----------------------------------------------------------


def test_health_check_reports_the_contract_version(health_service: HealthService) -> None:
    assert health_service.report().contract_version == 2


def test_rmw_from_the_environment() -> None:
    env = {"RMW_IMPLEMENTATION": "rmw_cyclonedds_cpp", "ROS_DISTRO": "humble"}
    assert resolve_rmw("ros2_cli", env) == ("rmw_cyclonedds_cpp", "env")


def test_rmw_distro_default_when_unset() -> None:
    assert resolve_rmw("ros2_cli", {"ROS_DISTRO": "humble"}) == (
        "rmw_fastrtps_cpp",
        "distro_default",
    )
    assert resolve_rmw("ros2_cli", {"ROS_DISTRO": "Galactic"}) == (
        "rmw_cyclonedds_cpp",
        "distro_default",
    )


def test_rmw_none_without_ros2_or_without_a_known_distro() -> None:
    assert resolve_rmw("none", {"RMW_IMPLEMENTATION": "rmw_x"}) == (None, "none")
    assert resolve_rmw("mock", {"RMW_IMPLEMENTATION": "rmw_x"}) == (None, "none")
    assert resolve_rmw("ros2_cli", {}) == (None, "none")
    assert resolve_rmw("ros2_cli", {"ROS_DISTRO": "noetic"}) == (None, "none")
    assert resolve_rmw("ros2_cli", {"RMW_IMPLEMENTATION": "  "}) == (None, "none")


def test_mock_health_has_no_rmw(health_service: HealthService) -> None:
    report = health_service.report()
    assert report.rmw_implementation is None and report.rmw_source == "none"


# ---- stamp_source ----------------------------------------------------------


def test_stamp_source_is_required_and_never_null() -> None:
    with pytest.raises(ValueError):
        MessageSample(topic="/t", message_type="p/msg/T", timestamp_ns=0)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        MessageSample(
            topic="/t",
            message_type="p/msg/T",
            timestamp_ns=0,
            stamp_source=None,  # type: ignore[arg-type]
        )


def test_mock_stamp_sources_follow_the_per_tool_table() -> None:
    adapter = MockAdapter()
    ros = {
        s.stamp_source
        for t in ("/cmd_vel", "/odom", "/tf")
        for s in adapter.sample_messages(t, 5).samples
    }
    assert ros <= {"header", "payload", "none"} and ros == {"header", "payload", "none"}
    bag = {
        s.stamp_source
        for t in ("/cmd_vel", "/odom")
        for s in adapter.peek_bag_samples("/tmp/demo.mcap", t, 5).samples
    }
    assert bag <= {"header", "payload", "recorded"} and bag == {"header", "recorded"}
    dds = {
        s.stamp_source
        for t in ("/dds/well_matched", "/dds/qos_mismatch", "/dds/topicforge/opaque")
        for s in adapter.peek_dds_samples(t, 5).samples
    }
    assert dds <= {"dds_source", "none"}


# ---- listings --------------------------------------------------------------


def test_participant_listing_carries_the_domain_once(inspector: Inspector) -> None:
    listing = inspector.list_participants(0)
    assert listing.domain_id == 0 and listing.mode_effective == "mock"
    assert listing.returned == listing.total == len(listing.participants) == 4
    assert all(p.node_names == [] and p.node_names_source == "none" for p in listing.participants)
    assert "mode_effective" not in listing.participants[0].model_dump()


def test_participant_listing_on_another_domain_is_empty_with_that_domain(
    inspector: Inspector,
) -> None:
    listing = inspector.list_participants(7)
    assert listing.participants == [] and listing.domain_id == 7 and listing.returned == 0


def test_a_full_event_log_is_reported_as_truncated() -> None:
    event = ParticipantEvent(
        guid="g", event_type="discovered", vendor="cyclone", timestamp_ns=1, domain_id=0
    )

    class _Full(MockAdapter):
        def participant_events(self, domain_id: int = 0, lookback_s: int = 300):  # type: ignore[no-untyped-def]
            return [event] * MAX_PARTICIPANT_EVENTS

    listing = Inspector(_Full()).participant_events(0, 300)
    assert listing.truncated is True and listing.returned == MAX_PARTICIPANT_EVENTS
    assert listing.note is not None and "lookback_s" in listing.note


# ---- topics ----------------------------------------------------------------


def test_topic_detail_has_qos_per_side_and_nodes(inspector: Inspector) -> None:
    info = inspector.get_topic_info("/scan")
    assert info.publisher_qos is not None and info.subscription_qos is not None
    assert info.publisher_qos.reliability == "best_effort"
    assert info.publisher_nodes == ["/lidar_driver"]
    assert info.subscriber_nodes == ["/nav_planner"]
    assert len(info.publisher_nodes) == info.publisher_count
    assert len(info.subscriber_nodes) == info.subscriber_count
    assert info.note is None


def test_every_mock_topic_lists_as_many_nodes_as_endpoints(inspector: Inspector) -> None:
    for item in inspector.list_topics().topics:
        info = inspector.get_topic_info(item.name)
        assert len(info.publisher_nodes) == item.publisher_count
        assert len(info.subscriber_nodes) == item.subscriber_count
        assert (
            info.publisher_qos is not None
            and info.publisher_qos.endpoint_count == item.publisher_count
        )
        assert info.subscription_qos is not None


# ---- names -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("/events/write_split", "rosbag2_internal"),
        ("/parameter_events", "ros_builtin"),
        ("/rosout", "ros_builtin"),
        ("/tf", "user"),
        ("/tf_static", "user"),
        ("/clock", "user"),
        ("/scan", "user"),
    ],
)
def test_bag_topic_kind(name: str, kind: str) -> None:
    assert classify_bag_topic(name) == kind


def test_ros_topic_unmangling() -> None:
    assert ros_topic_of("rt/camera/image_raw") == ("/camera/image_raw", None)
    assert ros_topic_of("rt/")[0] is None
    assert ros_topic_of("rq/x/get_parametersRequest")[0] is None
    assert ros_topic_of("DCPSParticipant")[0] is None


@pytest.mark.parametrize("name", ["rq/a", "rr/a", "rs/a", "rp/a", "ra/a", "ros_discovery_info"])
def test_internal_dds_topics(name: str) -> None:
    assert is_internal_dds_topic(name)


@pytest.mark.parametrize("name", ["rt/scan", "scan", "/dds/x", "DCPSParticipant"])
def test_user_dds_topics_are_not_internal(name: str) -> None:
    assert not is_internal_dds_topic(name)
