"""Tests for `topicforge.services.Inspector`."""

from __future__ import annotations

import pytest

from topicforge.adapters.base import AdapterError
from topicforge.services import Inspector


def test_list_topics_passes_through(inspector: Inspector) -> None:
    topics = inspector.list_topics()
    assert len(topics) >= 5


def test_get_topic_info_requires_leading_slash(inspector: Inspector) -> None:
    with pytest.raises(AdapterError, match="must start with"):
        inspector.get_topic_info("cmd_vel")


def test_get_topic_info_rejects_blank(inspector: Inspector) -> None:
    with pytest.raises(AdapterError, match="non-empty"):
        inspector.get_topic_info("   ")


@pytest.mark.parametrize(
    "bad_topic",
    [
        # shell metacharacters / whitespace / quotes / flags
        "/foo bar",
        "/foo;rm -rf /",
        "/foo --help",
        "/foo$(id)",
        "/foo`whoami`",
        "/foo|cat",
        "/foo&background",
        "/foo'quote",
        '/foo"quote',
        "/foo\nbar",
        "/foo-with-dash",
        # ROS2 topic-name structural rules
        "/",
        "/foo/",
        "/foo//bar",
        "//foo",
        "/1foo",
        "/foo/1bar",
        "/foo.bar",
    ],
)
def test_get_topic_info_rejects_malformed_names(inspector: Inspector, bad_topic: str) -> None:
    with pytest.raises(AdapterError, match="malformed"):
        inspector.get_topic_info(bad_topic)


def test_get_topic_info_accepts_nested_namespace(inspector: Inspector) -> None:
    # Validator must pass for namespaced topics; the mock then raises "Unknown topic".
    with pytest.raises(AdapterError, match="Unknown topic"):
        inspector.get_topic_info("/robot1/camera/image_raw")


def test_get_topic_info_accepts_existing_namespaced_fixture(inspector: Inspector) -> None:
    info = inspector.get_topic_info("/camera/image_raw")
    assert info.name == "/camera/image_raw"


def test_get_topic_info_accepts_underscore_prefixed_segment(inspector: Inspector) -> None:
    # `/_internal/...` is valid per ROS2 conventions; validator must pass and
    # delegate to the adapter, which then raises a domain error for the
    # unknown topic.
    with pytest.raises(AdapterError, match="Unknown topic"):
        inspector.get_topic_info("/_internal/state")


def test_sample_messages_default_count(inspector: Inspector) -> None:
    samples = inspector.sample_messages("/cmd_vel")
    assert 0 < len(samples) <= 5


def test_sample_messages_clamps_to_max(inspector: Inspector) -> None:
    # MAX_SAMPLE_COUNT is 50; the mock fixture has fewer than that, so we
    # confirm the clamp does not raise and returns at most MAX.
    samples = inspector.sample_messages("/cmd_vel", 9999)
    assert len(samples) <= 50


def test_sample_messages_negative_raises(inspector: Inspector) -> None:
    with pytest.raises(AdapterError):
        inspector.sample_messages("/cmd_vel", -1)


def test_analyze_bag_requires_path(inspector: Inspector) -> None:
    with pytest.raises(AdapterError):
        inspector.analyze_bag("")


def test_analyze_bag_rejects_blank_path(inspector: Inspector) -> None:
    with pytest.raises(AdapterError, match="non-empty"):
        inspector.analyze_bag("   ")


def test_analyze_bag_rejects_null_byte(inspector: Inspector) -> None:
    with pytest.raises(AdapterError, match="null bytes"):
        inspector.analyze_bag("/tmp/foo\x00.mcap")


def test_analyze_bag_strips_surrounding_whitespace(inspector: Inspector) -> None:
    # The mock adapter echoes the path back through `BagAnalysis.path`, so we
    # can observe normalization without a live ROS2 environment.
    result = inspector.analyze_bag("  /tmp/demo.mcap  ")
    assert result.path == "/tmp/demo.mcap"


def test_analyze_bag_returns_structured(inspector: Inspector) -> None:
    result = inspector.analyze_bag("/tmp/demo.mcap")
    assert result.path == "/tmp/demo.mcap"
    assert result.topics


def test_backend_name(inspector: Inspector) -> None:
    assert inspector.backend_name == "mock"
