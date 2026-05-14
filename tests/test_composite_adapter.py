"""Tests for `topicforge.adapters.composite.CompositeAdapter`.

The composite is a pure routing wrapper — these tests assert that each
of the 7 `MiddlewareAdapter` methods reaches the correct half (ROS or
DDS) and that errors propagate unchanged.
"""

from __future__ import annotations

from typing import Any

import pytest

from topicforge.adapters.base import AdapterError, AdapterName, EffectiveMode
from topicforge.adapters.composite import CompositeAdapter
from topicforge.models import (
    BagAnalysis,
    MessageSample,
    MismatchReport,
    ParticipantEvent,
    ParticipantInfo,
    SampleResult,
    TopicInfo,
)


class _StubRosAdapter:
    """Tracks calls and returns canned values for the ROS half of the protocol."""

    def __init__(
        self,
        *,
        name: AdapterName = "ros2_cli",
        mode: EffectiveMode = "live",
        available: bool = True,
    ) -> None:
        self.name: AdapterName = name
        self._mode: EffectiveMode = mode
        self._available = available
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    @property
    def effective_mode(self) -> EffectiveMode:
        return self._mode

    def is_available(self) -> bool:
        return self._available

    def list_topics(self) -> list[TopicInfo]:
        self.calls.append(("list_topics", ()))
        return [
            TopicInfo(
                name="/cmd_vel",
                message_type="geometry_msgs/msg/Twist",
                publisher_count=1,
                subscriber_count=1,
                mode_effective=self._mode,
            )
        ]

    def get_topic_info(self, topic: str) -> TopicInfo:
        self.calls.append(("get_topic_info", (topic,)))
        return TopicInfo(
            name=topic,
            message_type="x/Y",
            publisher_count=0,
            subscriber_count=0,
            mode_effective=self._mode,
        )

    def sample_messages(self, topic: str, count: int) -> list[MessageSample]:
        self.calls.append(("sample_messages", (topic, count)))
        return []

    def analyze_bag(self, path: str) -> BagAnalysis:
        self.calls.append(("analyze_bag", (path,)))
        return BagAnalysis(
            path=path,
            storage_format="mcap",
            duration_seconds=0.0,
            message_count=0,
            topics=[],
            mode_effective=self._mode,
        )

    def list_participants(self, domain_id: int = 0) -> list[ParticipantInfo]:
        # The ROS half must NEVER be called for DDS methods. If it is,
        # tests will see this call in `self.calls` and assert failure.
        self.calls.append(("list_participants", (domain_id,)))
        raise AdapterError("ROS adapter should not receive DDS calls")

    def detect_qos_mismatches(self, topic: str | None = None) -> list[MismatchReport]:
        self.calls.append(("detect_qos_mismatches", (topic,)))
        raise AdapterError("ROS adapter should not receive DDS calls")

    def peek_dds_samples(self, topic: str, count: int) -> SampleResult:
        self.calls.append(("peek_dds_samples", (topic, count)))
        raise AdapterError("ROS adapter should not receive DDS calls")

    def participant_events(
        self, domain_id: int = 0, lookback_seconds: int = 300
    ) -> list[ParticipantEvent]:
        self.calls.append(("participant_events", (domain_id, lookback_seconds)))
        raise AdapterError("ROS adapter should not receive DDS calls")


class _StubDdsAdapter:
    """Tracks calls and returns canned values for the DDS half of the protocol."""

    def __init__(
        self,
        *,
        name: AdapterName = "cyclone",
        mode: EffectiveMode = "live",
        available: bool = True,
    ) -> None:
        self.name: AdapterName = name
        self._mode: EffectiveMode = mode
        self._available = available
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    @property
    def effective_mode(self) -> EffectiveMode:
        return self._mode

    def is_available(self) -> bool:
        return self._available

    def list_topics(self) -> list[TopicInfo]:
        self.calls.append(("list_topics", ()))
        raise AdapterError("DDS adapter should not receive ROS calls")

    def get_topic_info(self, topic: str) -> TopicInfo:
        self.calls.append(("get_topic_info", (topic,)))
        raise AdapterError("DDS adapter should not receive ROS calls")

    def sample_messages(self, topic: str, count: int) -> list[MessageSample]:
        self.calls.append(("sample_messages", (topic, count)))
        raise AdapterError("DDS adapter should not receive ROS calls")

    def analyze_bag(self, path: str) -> BagAnalysis:
        self.calls.append(("analyze_bag", (path,)))
        raise AdapterError("DDS adapter should not receive ROS calls")

    def list_participants(self, domain_id: int = 0) -> list[ParticipantInfo]:
        self.calls.append(("list_participants", (domain_id,)))
        return [
            ParticipantInfo(
                guid="0" * 16,
                vendor="cyclone",
                hostname="host",
                domain_id=domain_id,
                mode_effective=self._mode,
            )
        ]

    def detect_qos_mismatches(self, topic: str | None = None) -> list[MismatchReport]:
        self.calls.append(("detect_qos_mismatches", (topic,)))
        return []

    def peek_dds_samples(self, topic: str, count: int) -> SampleResult:
        self.calls.append(("peek_dds_samples", (topic, count)))
        return SampleResult(topic=topic, count=0, samples=[], mode_effective=self._mode)

    def participant_events(
        self, domain_id: int = 0, lookback_seconds: int = 300
    ) -> list[ParticipantEvent]:
        self.calls.append(("participant_events", (domain_id, lookback_seconds)))
        return []


# ---------------------------------------------------------------------------
# Routing matrix
# ---------------------------------------------------------------------------


def test_ros_methods_route_to_ros_half() -> None:
    ros = _StubRosAdapter()
    dds = _StubDdsAdapter()
    composite = CompositeAdapter(ros, dds)

    composite.list_topics()
    composite.get_topic_info("/cmd_vel")
    composite.sample_messages("/cmd_vel", 3)
    composite.analyze_bag("/tmp/x.mcap")

    assert [c[0] for c in ros.calls] == [
        "list_topics",
        "get_topic_info",
        "sample_messages",
        "analyze_bag",
    ]
    assert dds.calls == []  # DDS half must never be touched.


def test_dds_methods_route_to_dds_half() -> None:
    ros = _StubRosAdapter()
    dds = _StubDdsAdapter()
    composite = CompositeAdapter(ros, dds)

    composite.list_participants(domain_id=42)
    composite.detect_qos_mismatches(topic="/x")
    composite.peek_dds_samples("/x", 5)
    composite.participant_events(domain_id=42, lookback_seconds=60)

    assert [c[0] for c in dds.calls] == [
        "list_participants",
        "detect_qos_mismatches",
        "peek_dds_samples",
        "participant_events",
    ]
    assert ros.calls == []


def test_method_arguments_propagate_unchanged() -> None:
    ros = _StubRosAdapter()
    dds = _StubDdsAdapter()
    composite = CompositeAdapter(ros, dds)

    composite.sample_messages("/cmd_vel", 7)
    composite.peek_dds_samples("/dds/topic", 12)
    composite.list_participants(domain_id=99)

    assert ros.calls[-1] == ("sample_messages", ("/cmd_vel", 7))
    assert dds.calls[0] == ("peek_dds_samples", ("/dds/topic", 12))
    assert dds.calls[1] == ("list_participants", (99,))


# ---------------------------------------------------------------------------
# Identity & state
# ---------------------------------------------------------------------------


def test_name_combines_halves() -> None:
    composite = CompositeAdapter(_StubRosAdapter(), _StubDdsAdapter())
    assert composite.name == "ros2_cli+cyclone"


def test_name_combines_halves_for_fast() -> None:
    composite = CompositeAdapter(_StubRosAdapter(), _StubDdsAdapter(name="fast"))
    assert composite.name == "ros2_cli+fast"


def test_effective_mode_live_when_either_side_live() -> None:
    composite = CompositeAdapter(
        _StubRosAdapter(mode="live"),
        _StubDdsAdapter(mode="mock"),
    )
    assert composite.effective_mode == "live"


def test_effective_mode_mock_only_when_both_mock() -> None:
    composite = CompositeAdapter(
        _StubRosAdapter(mode="mock"),
        _StubDdsAdapter(mode="mock"),
    )
    assert composite.effective_mode == "mock"


def test_is_available_requires_both_halves() -> None:
    both_up = CompositeAdapter(_StubRosAdapter(available=True), _StubDdsAdapter(available=True))
    ros_down = CompositeAdapter(_StubRosAdapter(available=False), _StubDdsAdapter(available=True))
    dds_down = CompositeAdapter(_StubRosAdapter(available=True), _StubDdsAdapter(available=False))

    assert both_up.is_available() is True
    assert ros_down.is_available() is False
    assert dds_down.is_available() is False


# ---------------------------------------------------------------------------
# Error propagation
# ---------------------------------------------------------------------------


class _RaisingRos(_StubRosAdapter):
    def list_topics(self) -> list[TopicInfo]:
        raise AdapterError("boom-ros")


class _RaisingDds(_StubDdsAdapter):
    def list_participants(self, domain_id: int = 0) -> list[ParticipantInfo]:
        raise AdapterError("boom-dds")


def test_ros_error_propagates_unchanged() -> None:
    composite = CompositeAdapter(_RaisingRos(), _StubDdsAdapter())
    with pytest.raises(AdapterError, match="boom-ros"):
        composite.list_topics()


def test_dds_error_propagates_unchanged() -> None:
    composite = CompositeAdapter(_StubRosAdapter(), _RaisingDds())
    with pytest.raises(AdapterError, match="boom-dds"):
        composite.list_participants()
