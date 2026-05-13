"""Unit tests for the pure parsers in the live adapter.

These tests never touch a real ROS2 install — they hit the regex parsers
directly against representative CLI output. This is how we cover the live
adapter on machines (and CI) without ROS2.
"""

from __future__ import annotations

import pytest

from topicforge.adapters.ros2_live.adapter import (
    parse_bag_info,
    parse_echo_yaml,
    parse_pub_sub_counts,
    parse_topic_info,
    parse_topic_list,
)


def test_parse_topic_list_basic() -> None:
    sample = (
        "/cmd_vel [geometry_msgs/msg/Twist]\n"
        "/odom [nav_msgs/msg/Odometry]\n"
        "/scan [sensor_msgs/msg/LaserScan]\n"
    )
    assert parse_topic_list(sample) == [
        ("/cmd_vel", "geometry_msgs/msg/Twist"),
        ("/odom", "nav_msgs/msg/Odometry"),
        ("/scan", "sensor_msgs/msg/LaserScan"),
    ]


def test_parse_topic_list_ignores_garbage_lines() -> None:
    sample = "garbage line\n\n/foo [pkg/msg/Foo]\nmore garbage\n"
    assert parse_topic_list(sample) == [("/foo", "pkg/msg/Foo")]


def test_parse_topic_list_empty() -> None:
    assert parse_topic_list("") == []


def test_parse_pub_sub_counts() -> None:
    sample = "Type: geometry_msgs/msg/Twist\nPublisher count: 2\nSubscription count: 5\n"
    assert parse_pub_sub_counts(sample) == (2, 5)


def test_parse_pub_sub_counts_missing_lines_default_to_zero() -> None:
    assert parse_pub_sub_counts("") == (0, 0)


def test_parse_topic_info_full() -> None:
    sample = "Type: geometry_msgs/msg/Twist\nPublisher count: 1\nSubscription count: 1\n"
    info = parse_topic_info(sample, fallback_name="/cmd_vel")
    assert info is not None
    assert info.name == "/cmd_vel"
    assert info.message_type == "geometry_msgs/msg/Twist"
    assert info.publisher_count == 1
    assert info.subscriber_count == 1


def test_parse_topic_info_missing_type_returns_none() -> None:
    assert parse_topic_info("Publisher count: 0\n", fallback_name="/x") is None


def test_parse_echo_yaml_captures_top_level_fields_and_raw_text() -> None:
    # Inline top-level value is captured; nested keys are NOT extracted by
    # the MVP parser (they are only reachable via `_raw_text`).
    sample = "linear:\n  x: 0.2\n  y: 0.0\nangular:\n  z: 0.1\nstamp: 42\n"
    out = parse_echo_yaml(sample)

    assert out["_raw_text"] == sample
    # Top-level keys with no inline value end up as empty strings.
    assert out["linear"] == ""
    assert out["angular"] == ""
    # Top-level key with an inline value is captured verbatim.
    assert out["stamp"] == "42"
    # Nested keys must not leak into the flat view.
    assert "x" not in out
    assert "y" not in out
    assert "z" not in out


def test_parse_echo_yaml_skips_comments_and_blank_lines() -> None:
    sample = "# header\n\nfoo: bar\n"
    out = parse_echo_yaml(sample)
    assert out["foo"] == "bar"
    assert "# header" not in out
    # Reserved key is always emitted, even when only one real line is parsed.
    assert out["_raw_text"] == sample


def test_parse_echo_yaml_reserves_raw_text_key_on_empty_input() -> None:
    out = parse_echo_yaml("")
    # `_raw_text` is the single guaranteed key — clients can rely on its
    # presence even when the upstream CLI produced nothing parseable.
    assert out == {"_raw_text": ""}


def test_parse_bag_info_extracts_duration_and_topics() -> None:
    sample = (
        "Files: demo.mcap\n"
        "Bag size: 12.3 MiB\n"
        "Storage id: mcap\n"
        "Duration: 42.500s\n"
        "Start: ...\n"
        "End: ...\n"
        "Messages: 1287\n"
        "Topic information: \n"
        "  Topic: /cmd_vel | Type: geometry_msgs/msg/Twist | Count: 425 | Serialization Format: cdr\n"
        "  Topic: /scan | Type: sensor_msgs/msg/LaserScan | Count: 425 | Serialization Format: cdr\n"
    )
    result = parse_bag_info(sample, fallback_path="/x/demo.mcap")
    assert result.duration_seconds == 42.5
    assert result.message_count == 1287
    assert result.storage_format == "mcap"
    assert len(result.topics) == 2
    cmd_vel = next(t for t in result.topics if t.name == "/cmd_vel")
    assert cmd_vel.message_type == "geometry_msgs/msg/Twist"
    assert cmd_vel.message_count == 425
    assert cmd_vel.frequency_hz == pytest.approx(10.0)


def test_parse_bag_info_zero_duration_yields_no_frequency() -> None:
    sample = "Storage id: sqlite3\nDuration: 0.000s\nMessages: 0\n"
    result = parse_bag_info(sample, fallback_path="/x/empty.db3")
    assert result.duration_seconds == 0.0
    assert result.message_count == 0
    assert result.topics == []
