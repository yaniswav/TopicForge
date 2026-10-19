"""Mock adapter: deterministic fixtures for development, tests and demos."""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath

from topicforge.adapters.base import AdapterError, AdapterName, EffectiveMode
from topicforge.adapters.common.bag_range import range_note, validate_range, window_ns
from topicforge.adapters.common.log_filter import (
    LogFilter,
    first_matches,
    newest_matches,
    require_log_topic,
)
from topicforge.adapters.ros2_mock import fixtures, node_fixtures, tf_fixtures
from topicforge.constants import (
    DEFAULT_MAX_ARRAY_LENGTH,
    DEFAULT_SAMPLE_TIMEOUT_S,
    MAX_SAMPLE_COUNT,
)
from topicforge.models import (
    BagAnalysis,
    EndpointListing,
    MismatchScan,
    NodeInfo,
    NodeListing,
    ParticipantEvent,
    ParticipantInfo,
    SampleResult,
    TfCapture,
    TopicInfo,
    TopicListItem,
    TopicMetrics,
)

# Extensions the live `ros2 bag info` accepts; the mock rejects the same
# paths so mock mode does not hide the error.
_BAG_EXTENSIONS: frozenset[str] = frozenset({".mcap", ".db3", ".bag"})


class MockAdapter:
    name: AdapterName = "mock"
    # Set by the factory when a DDS backend was requested but the server fell back to
    # these fixtures, so `health_check` can say why. `None` for a deliberate mock.
    dds_inactive_reason: str | None = None

    @property
    def effective_mode(self) -> EffectiveMode:
        return "mock"

    def is_available(self) -> bool:
        return True

    def list_topics(self) -> list[TopicListItem]:
        return list(fixtures.MOCK_TOPIC_ITEMS)

    def get_topic_info(self, topic: str) -> TopicInfo:
        for t in fixtures.MOCK_TOPICS:
            if t.name == topic:
                return t
        raise AdapterError(f"Unknown topic: {topic!r}")

    def list_nodes(self) -> NodeListing:
        return node_fixtures.mock_node_listing()

    def get_node_info(self, node: str, timeout_s: float = 8.0) -> NodeInfo:
        # Mock parameters are read instantly, so `timeout_s` does not apply.
        return node_fixtures.mock_node_info(node)

    def capture_tf(self, window_s: float) -> TfCapture:
        # The mock capture is fixed: `window_s` does not apply.
        return tf_fixtures.mock_tf_capture()

    def sample_messages(
        self,
        topic: str,
        count: int,
        *,
        max_array_length: int | None = DEFAULT_MAX_ARRAY_LENGTH,
        arrays_summary_only: bool = False,
        timeout_s: float = DEFAULT_SAMPLE_TIMEOUT_S,
        min_log_level: str | None = None,
        node: str | None = None,
    ) -> SampleResult:
        # Mock samples never wait, so `timeout_s` does not apply.
        if count < 0:
            raise AdapterError("count must be >= 0")
        # Validate the topic exists first so the error is the same as `get_topic_info`.
        info = self.get_topic_info(topic)
        log_filter = LogFilter.of(min_log_level, node)
        if log_filter is not None:
            require_log_topic(topic, info.message_type)
            everything = fixtures.mock_samples_for(topic, 10_000, max_array_length=max_array_length)
            kept, note = newest_matches(everything, count, log_filter)
            return SampleResult(
                topic=topic, count=len(kept), samples=kept, mode_effective="mock", note=note
            )
        samples = fixtures.mock_samples_for(
            topic,
            count,
            max_array_length=max_array_length,
            arrays_summary_only=arrays_summary_only,
        )
        return SampleResult(
            topic=topic,
            count=len(samples),
            samples=samples,
            mode_effective="mock",
            rate=fixtures.mock_sample_rate(samples),
        )

    def analyze_bag(self, path: str) -> BagAnalysis:
        _reject_non_bag_path(path)
        # The fixture is frozen; produce a copy with the caller's path.
        return fixtures.MOCK_BAG_ANALYSIS.model_copy(update={"path": path})

    def peek_bag_samples(
        self,
        path: str,
        topic: str,
        count: int,
        *,
        start_s: float | None = None,
        end_s: float | None = None,
        min_log_level: str | None = None,
        node: str | None = None,
    ) -> SampleResult:
        """Deterministic mock sample peek for a recorded bag."""
        if count < 0:
            raise AdapterError("count must be >= 0")
        _reject_non_bag_path(path)
        validate_range(start_s, end_s)
        log_filter = LogFilter.of(min_log_level, node)
        clamped = min(count, MAX_SAMPLE_COUNT)
        everything = fixtures.mock_bag_samples_for(topic)
        if log_filter is not None and everything:
            require_log_topic(topic, everything[0].message_type)
        base = fixtures.MOCK_BAG_ANALYSIS.start_ns or 0
        start, stop = window_ns(base, start_s, end_s)
        in_range = [
            s
            for s in everything
            if (start is None or (s.recorded_ns or 0) >= start)
            and (stop is None or (s.recorded_ns or 0) < stop)
        ]
        notes: list[str] = []
        if log_filter is not None:
            samples, filter_note = first_matches(in_range, clamped, log_filter)
            if filter_note:
                notes.append(filter_note)
        else:
            samples = in_range[:clamped]
        range_text = range_note(
            start_s, end_s, duration_s=fixtures.MOCK_BAG_ANALYSIS.duration_s, got=len(samples)
        )
        if range_text:
            notes.append(range_text)
        return SampleResult(
            topic=topic,
            count=len(samples),
            samples=samples,
            mode_effective="mock",
            note=" ".join(notes) or None,
            rate=fixtures.mock_bag_rate(samples),
        )

    def list_participants(self, domain_id: int = 0) -> list[ParticipantInfo]:
        if domain_id < 0 or domain_id > 232:
            raise AdapterError(f"domain_id must be in 0..232, got {domain_id}")
        return [_with_mock_nodes(p) for p in fixtures.MOCK_PARTICIPANTS if p.domain_id == domain_id]

    def detect_qos_mismatches(self, topic: str | None = None) -> MismatchScan:
        if topic is not None and topic not in fixtures.MOCK_DDS_TOPICS:
            raise AdapterError(
                f"Unknown DDS topic: {topic!r}. Known mock DDS topics: "
                f"{list(fixtures.MOCK_DDS_TOPICS)}"
            )
        return fixtures.mock_mismatch_scan(topic)

    def peek_dds_samples(self, topic: str, count: int) -> SampleResult:
        if count < 0:
            raise AdapterError("count must be >= 0")
        if topic not in fixtures.MOCK_DDS_TOPICS:
            raise AdapterError(
                f"Unknown DDS topic: {topic!r}. Known mock DDS topics: "
                f"{list(fixtures.MOCK_DDS_TOPICS)}"
            )
        clamped = min(count, MAX_SAMPLE_COUNT)
        return fixtures.mock_dds_samples_for(topic, clamped)

    def list_endpoints(
        self,
        topic: str | None = None,
        participant_guid: str | None = None,
        include_observer: bool = False,
        include_departed: bool = False,
        include_internal: bool = False,
    ) -> EndpointListing:
        return fixtures.mock_endpoint_listing(
            topic, participant_guid, include_observer, include_internal
        )

    def participant_events(
        self, domain_id: int = 0, lookback_s: int = 300
    ) -> list[ParticipantEvent]:
        if domain_id < 0 or domain_id > 232:
            raise AdapterError(f"domain_id must be in 0..232, got {domain_id}")
        if lookback_s < 1 or lookback_s > 86400:
            raise AdapterError(f"lookback_s must be in 1..86400, got {lookback_s}")
        return fixtures.mock_participant_events_for(domain_id, lookback_s)

    def topic_metrics(self, topic: str, window_s: int = 60, domain_id: int = 0) -> TopicMetrics:
        if domain_id < 0 or domain_id > 232:
            raise AdapterError(f"domain_id must be in 0..232, got {domain_id}")
        if window_s < 1 or window_s > 3600:
            raise AdapterError(f"window_s must be in 1..3600, got {window_s}")
        return fixtures.mock_topic_metrics_for(topic, window_s, domain_id)


def _reject_non_bag_path(path: str) -> None:
    """Reject paths `ros2 bag info` would refuse.

    `.mcap`, `.db3`, `.bag` and extensionless paths (possibly a `rosbag2_*`
    directory) pass; any other extension raises.
    """
    # POSIX parsing for `/tmp/foo.mcap`, Windows parsing for `C:\demos\foo.mcap`.
    posix_suffix = PurePosixPath(path).suffix.lower()
    win_suffix = PureWindowsPath(path).suffix.lower()
    suffix = posix_suffix or win_suffix
    if suffix and suffix not in _BAG_EXTENSIONS:
        raise AdapterError(
            f"path does not look like a ROS2 bag (got suffix {suffix!r}); "
            f"expected one of {sorted(_BAG_EXTENSIONS)} or a "
            "`rosbag2_*` directory"
        )


def _with_mock_nodes(participant: ParticipantInfo) -> ParticipantInfo:
    """Attach the node names the mock robot's participants announce on `ros_discovery_info`."""
    names = node_fixtures.mock_participant_node_names(participant.name)
    if not names:
        return participant
    return participant.model_copy(
        update={"node_names": names, "node_names_source": "ros_discovery_info"}
    )
