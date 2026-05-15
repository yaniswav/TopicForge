"""Mock adapter — deterministic fixtures for development, tests, and demos.

Always available. Outputs are stable across runs so tests can assert on
exact values.
"""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath

from topicforge.adapters.base import AdapterError, AdapterName, EffectiveMode
from topicforge.adapters.ros2_mock import fixtures
from topicforge.models import (
    BagAnalysis,
    MessageSample,
    MismatchReport,
    ParticipantEvent,
    ParticipantInfo,
    SampleResult,
    TopicInfo,
    TopicMetrics,
)

# Extensions the live `ros2 bag info` accepts. The mock mirrors this list so
# a test that passes `/tmp/demo.txt` fails in mock the same way it would in
# live — otherwise mock mode would hide a real-world UX problem until the
# first ROS2 install.
_BAG_EXTENSIONS: frozenset[str] = frozenset({".mcap", ".db3", ".bag"})

_MAX_SAMPLE_COUNT = 50


class MockAdapter:
    name: AdapterName = "mock"

    @property
    def effective_mode(self) -> EffectiveMode:
        return "mock"

    def is_available(self) -> bool:
        return True

    def list_topics(self) -> list[TopicInfo]:
        return list(fixtures.MOCK_TOPICS)

    def get_topic_info(self, topic: str) -> TopicInfo:
        for t in fixtures.MOCK_TOPICS:
            if t.name == topic:
                return t
        raise AdapterError(f"Unknown topic: {topic!r}")

    def sample_messages(self, topic: str, count: int) -> list[MessageSample]:
        if count < 0:
            raise AdapterError("count must be >= 0")
        # Validate the topic exists first so the error is the same as `get_topic_info`.
        self.get_topic_info(topic)
        return fixtures.mock_samples_for(topic, count)

    def analyze_bag(self, path: str) -> BagAnalysis:
        _reject_non_bag_path(path)
        # The fixture is frozen; produce a copy with the caller's path.
        return fixtures.MOCK_BAG_ANALYSIS.model_copy(update={"path": path})

    def peek_bag_samples(self, path: str, topic: str, count: int) -> SampleResult:
        """Deterministic mock sample peek for a recorded bag."""
        if count < 0:
            raise AdapterError("count must be >= 0")
        _reject_non_bag_path(path)
        clamped = min(count, _MAX_SAMPLE_COUNT)
        samples = fixtures.mock_bag_samples_for(topic, clamped)
        return SampleResult(
            topic=topic,
            count=len(samples),
            samples=samples,
            mode_effective="mock",
        )

    # ---------------------------- DDS module ------------------------------

    def list_participants(self, domain_id: int = 0) -> list[ParticipantInfo]:
        if domain_id < 0 or domain_id > 232:
            raise AdapterError(f"domain_id must be in 0..232, got {domain_id}")
        return [p for p in fixtures.MOCK_PARTICIPANTS if p.domain_id == domain_id]

    def detect_qos_mismatches(self, topic: str | None = None) -> list[MismatchReport]:
        if topic is not None and topic not in fixtures.MOCK_DDS_TOPICS:
            raise AdapterError(
                f"Unknown DDS topic: {topic!r}. Known mock DDS topics: "
                f"{list(fixtures.MOCK_DDS_TOPICS)}"
            )
        return fixtures.mock_mismatches_for(topic)

    def peek_dds_samples(self, topic: str, count: int) -> SampleResult:
        if count < 0:
            raise AdapterError("count must be >= 0")
        if topic not in fixtures.MOCK_DDS_TOPICS:
            raise AdapterError(
                f"Unknown DDS topic: {topic!r}. Known mock DDS topics: "
                f"{list(fixtures.MOCK_DDS_TOPICS)}"
            )
        clamped = min(count, _MAX_SAMPLE_COUNT)
        return fixtures.mock_dds_samples_for(topic, clamped)

    def participant_events(
        self, domain_id: int = 0, lookback_seconds: int = 300
    ) -> list[ParticipantEvent]:
        if domain_id < 0 or domain_id > 232:
            raise AdapterError(f"domain_id must be in 0..232, got {domain_id}")
        if lookback_seconds < 1 or lookback_seconds > 86400:
            raise AdapterError(f"lookback_seconds must be in 1..86400, got {lookback_seconds}")
        return fixtures.mock_participant_events_for(domain_id, lookback_seconds)

    def topic_metrics(
        self, topic: str, window_seconds: int = 60, domain_id: int = 0
    ) -> TopicMetrics:
        if domain_id < 0 or domain_id > 232:
            raise AdapterError(f"domain_id must be in 0..232, got {domain_id}")
        if window_seconds < 1 or window_seconds > 3600:
            raise AdapterError(f"window_seconds must be in 1..3600, got {window_seconds}")
        return fixtures.mock_topic_metrics_for(topic, window_seconds, domain_id)


def _reject_non_bag_path(path: str) -> None:
    """Reject paths the live `ros2 bag info` would obviously refuse.

    Accepts: `.mcap` / `.db3` / `.bag` files, and any extensionless path
    (which could legitimately be a `rosbag2_*` directory). Rejects every
    other extension so mock demos surface the same shape of error a
    real ROS2 install would produce on, e.g., `/tmp/note.txt`.
    """
    # `PurePosixPath` handles `/tmp/foo.mcap`; `PureWindowsPath` handles
    # `C:\demos\foo.mcap`. The longest suffix wins.
    posix_suffix = PurePosixPath(path).suffix.lower()
    win_suffix = PureWindowsPath(path).suffix.lower()
    suffix = posix_suffix or win_suffix
    if suffix and suffix not in _BAG_EXTENSIONS:
        raise AdapterError(
            f"path does not look like a ROS2 bag (got suffix {suffix!r}); "
            f"expected one of {sorted(_BAG_EXTENSIONS)} or a "
            "`rosbag2_*` directory"
        )
