"""Composite adapter: routes ROS2 graph calls to one adapter, DDS calls to another.

`TOPICFORGE_MODE=live` and `TOPICFORGE_DDS_BACKEND=cyclone|fast` are
independent settings. When both are active the factory puts a
`Ros2CliAdapter` and a DDS adapter behind this wrapper, so one process
serves all tools.

The ROS2 graph and bag methods go to the ROS adapter; the DDS and
observability methods go to the DDS adapter. `AdapterError` from either
side propagates unchanged.
"""

from __future__ import annotations

from typing import Any

from topicforge.adapters.base import AdapterName, EffectiveMode, MiddlewareAdapter
from topicforge.constants import DEFAULT_MAX_ARRAY_LENGTH, DEFAULT_SAMPLE_TIMEOUT_S
from topicforge.models import (
    BagAnalysis,
    EndpointListing,
    MismatchScan,
    ParticipantEvent,
    ParticipantInfo,
    SampleResult,
    TopicInfo,
    TopicMetrics,
)


class CompositeAdapter:
    """Routes ROS2 protocol methods to `ros`, DDS protocol methods to `dds`.

    Reports a hyphenated `name` (e.g. `"ros2_cli+cyclone"`) and an
    `effective_mode` of `"live"` when either side is live.
    """

    def __init__(self, ros: MiddlewareAdapter, dds: MiddlewareAdapter) -> None:
        self._ros = ros
        self._dds = dds

    @property
    def name(self) -> AdapterName:  # type: ignore[override]
        combined = f"{self._ros.name}+{self._dds.name}"
        return combined  # type: ignore[return-value]

    @property
    def effective_mode(self) -> EffectiveMode:
        if self._ros.effective_mode == "live" or self._dds.effective_mode == "live":
            return "live"
        return "mock"

    def is_available(self) -> bool:
        return self._ros.is_available() and self._dds.is_available()

    # ----- ROS2 graph surface -> ROS adapter -----

    def list_topics(self) -> list[TopicInfo]:
        return self._ros.list_topics()

    def get_topic_info(self, topic: str) -> TopicInfo:
        return self._ros.get_topic_info(topic)

    def sample_messages(
        self,
        topic: str,
        count: int,
        *,
        max_array_length: int | None = DEFAULT_MAX_ARRAY_LENGTH,
        arrays_summary_only: bool = False,
        timeout_s: float = DEFAULT_SAMPLE_TIMEOUT_S,
    ) -> SampleResult:
        return self._ros.sample_messages(
            topic,
            count,
            max_array_length=max_array_length,
            arrays_summary_only=arrays_summary_only,
            timeout_s=timeout_s,
        )

    def analyze_bag(self, path: str) -> BagAnalysis:
        return self._ros.analyze_bag(path)

    def sim_clock_published(self) -> bool | None:
        """The ROS 2 half's `/clock` probe, `None` when it has none."""
        probe = getattr(self._ros, "sim_clock_published", None)
        return probe() if callable(probe) else None

    # ----- DDS surface -> DDS adapter -----

    def observer_status(self) -> dict[str, Any] | None:
        """The DDS half's observer/tracker status, `None` when it has none."""
        status = getattr(self._dds, "observer_status", None)
        return status() if callable(status) else None

    def await_discovery_ready(self) -> bool:
        """Delegate to the DDS half's warm-up wait; `True` when it has none."""
        wait = getattr(self._dds, "await_discovery_ready", None)
        return bool(wait()) if callable(wait) else True

    def list_participants(self, domain_id: int = 0) -> list[ParticipantInfo]:
        return self._dds.list_participants(domain_id)

    def detect_qos_mismatches(self, topic: str | None = None) -> MismatchScan:
        return self._dds.detect_qos_mismatches(topic)

    def peek_dds_samples(self, topic: str, count: int) -> SampleResult:
        return self._dds.peek_dds_samples(topic, count)

    def participant_events(
        self, domain_id: int = 0, lookback_s: int = 300
    ) -> list[ParticipantEvent]:
        return self._dds.participant_events(domain_id, lookback_s)

    def topic_metrics(
        self, topic: str, window_s: int = 60, domain_id: int = 0
    ) -> TopicMetrics:
        return self._dds.topic_metrics(topic, window_s, domain_id)

    def list_endpoints(
        self,
        topic: str | None = None,
        participant_guid: str | None = None,
        include_observer: bool = False,
        include_departed: bool = False,
    ) -> EndpointListing:
        return self._dds.list_endpoints(topic, participant_guid, include_observer, include_departed)

    def peek_bag_samples(self, path: str, topic: str, count: int) -> SampleResult:
        # Bag decoding is on the ROS half (MCAP and rosbags are ROS tooling).
        return self._ros.peek_bag_samples(path, topic, count)
