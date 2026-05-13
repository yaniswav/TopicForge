"""Tests for `topicforge.adapters.ros2_mock.MockAdapter`."""

from __future__ import annotations

import pytest

from topicforge.adapters.base import AdapterError
from topicforge.adapters.ros2_mock import MockAdapter


def test_is_available_always_true() -> None:
    assert MockAdapter().is_available() is True


def test_effective_mode_is_mock() -> None:
    assert MockAdapter().effective_mode == "mock"


def test_list_topics_includes_known_fixtures(mock_adapter: MockAdapter) -> None:
    topics = mock_adapter.list_topics()
    names = {t.name for t in topics}
    assert {"/cmd_vel", "/odom", "/scan", "/tf", "/camera/image_raw"} <= names
    # Every listed TopicInfo carries the mock-mode marker.
    assert all(t.mode_effective == "mock" for t in topics)


def test_get_topic_info_returns_known_type(mock_adapter: MockAdapter) -> None:
    info = mock_adapter.get_topic_info("/cmd_vel")
    assert info.message_type == "geometry_msgs/msg/Twist"
    assert info.publisher_count == 1
    assert info.mode_effective == "mock"


def test_get_topic_info_unknown_raises(mock_adapter: MockAdapter) -> None:
    with pytest.raises(AdapterError, match="Unknown topic"):
        mock_adapter.get_topic_info("/nope")


def test_sample_messages_returns_up_to_count(mock_adapter: MockAdapter) -> None:
    samples = mock_adapter.sample_messages("/cmd_vel", 3)
    assert len(samples) == 3
    assert all(s.topic == "/cmd_vel" for s in samples)
    assert all(s.message_type == "geometry_msgs/msg/Twist" for s in samples)


def test_sample_messages_count_zero(mock_adapter: MockAdapter) -> None:
    assert mock_adapter.sample_messages("/cmd_vel", 0) == []


def test_sample_messages_negative_count_raises(mock_adapter: MockAdapter) -> None:
    with pytest.raises(AdapterError):
        mock_adapter.sample_messages("/cmd_vel", -1)


def test_sample_messages_unknown_topic_raises(mock_adapter: MockAdapter) -> None:
    with pytest.raises(AdapterError):
        mock_adapter.sample_messages("/nope", 3)


def test_analyze_bag_returns_fixture_with_path(mock_adapter: MockAdapter) -> None:
    result = mock_adapter.analyze_bag("/tmp/demo.mcap")
    assert result.path == "/tmp/demo.mcap"
    assert result.duration_seconds > 0
    assert result.message_count > 0
    assert any(t.name == "/cmd_vel" for t in result.topics)
    assert result.anomalies  # mock fixture intentionally includes some
    assert result.mode_effective == "mock"


@pytest.mark.parametrize(
    "good_path",
    [
        "/tmp/demo.mcap",
        "/tmp/recording.db3",
        "/tmp/legacy.bag",
        "C:\\demos\\run.MCAP",  # extension match is case-insensitive
        "/tmp/rosbag2_2026_05_12",  # extensionless: assumed to be a directory
    ],
)
def test_analyze_bag_accepts_known_bag_paths(mock_adapter: MockAdapter, good_path: str) -> None:
    result = mock_adapter.analyze_bag(good_path)
    assert result.path == good_path


@pytest.mark.parametrize(
    "bad_path",
    [
        "/tmp/notes.txt",
        "/tmp/recording.json",
        "/tmp/random.unknown",
        "/tmp/demo.py",
    ],
)
def test_analyze_bag_rejects_non_bag_extension(mock_adapter: MockAdapter, bad_path: str) -> None:
    with pytest.raises(AdapterError, match="does not look like a ROS2 bag"):
        mock_adapter.analyze_bag(bad_path)


# ---------------------- fixture-coherence regression -----------------------
# These tests catch the "added a topic to MOCK_TOPICS but forgot to add
# samples" (or vice-versa) drift documented in `.claude/skills/topicforge/
# update-mock-fixtures/SKILL.md`.


def test_every_mock_topic_has_at_least_one_sample() -> None:
    from topicforge.adapters.ros2_mock import fixtures

    for topic in fixtures.MOCK_TOPICS:
        samples = fixtures.mock_samples_for(topic.name, count=100)
        assert samples, (
            f"Mock topic {topic.name!r} has no entry in `_MOCK_SAMPLES`. "
            "Either add samples or drop the topic from MOCK_TOPICS."
        )
        assert all(s.topic == topic.name for s in samples), (
            f"Sample.topic must match MOCK_TOPICS entry for {topic.name!r}"
        )
        assert all(s.message_type == topic.message_type for s in samples), (
            f"Sample.message_type must match MOCK_TOPICS for {topic.name!r}"
        )


def test_no_orphan_mock_samples() -> None:
    from topicforge.adapters.ros2_mock import fixtures

    known_names = {t.name for t in fixtures.MOCK_TOPICS}
    for sample_topic in fixtures._MOCK_SAMPLES:
        assert sample_topic in known_names, (
            f"`_MOCK_SAMPLES[{sample_topic!r}]` has no matching MOCK_TOPICS "
            "entry. Either add the topic to MOCK_TOPICS or drop the samples."
        )
