"""Mock adapter — deterministic fixtures for development, tests, and demos.

Always available. Outputs are stable across runs so tests can assert on
exact values.
"""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath

from topicforge.adapters.base import AdapterError, AdapterName
from topicforge.adapters.ros2_mock import fixtures
from topicforge.models import BagAnalysis, MessageSample, TopicInfo

# Extensions the live `ros2 bag info` accepts. The mock mirrors this list so
# a test that passes `/tmp/demo.txt` fails in mock the same way it would in
# live — otherwise mock mode would hide a real-world UX problem until the
# first ROS2 install.
_BAG_EXTENSIONS: frozenset[str] = frozenset({".mcap", ".db3", ".bag"})


class MockAdapter:
    name: AdapterName = "mock"

    @property
    def effective_mode(self) -> AdapterName:
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
