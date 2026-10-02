"""Unit tests for the pure parsers in the live adapter.

These tests never touch a real ROS2 install: they hit the regex parsers
directly against representative CLI output. This is how we cover the live
adapter on machines (and CI) without ROS2.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from topicforge.adapters.ros2_live.adapter import (
    parse_bag_info,
    parse_pub_sub_counts,
    parse_topic_info,
    parse_topic_list,
)

_FIXTURES = Path(__file__).parent / "fixtures"


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
    info = parse_topic_info(sample, fallback_name="/cmd_vel", mode_effective="live")
    assert info is not None
    assert info.name == "/cmd_vel"
    assert info.message_type == "geometry_msgs/msg/Twist"
    assert info.publisher_count == 1
    assert info.subscriber_count == 1
    assert info.mode_effective == "live"


def test_parse_topic_info_missing_type_returns_none() -> None:
    assert (
        parse_topic_info("Publisher count: 0\n", fallback_name="/x", mode_effective="live") is None
    )


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
    result = parse_bag_info(sample, fallback_path="/x/demo.mcap", mode_effective="live")
    assert result.duration_seconds == 42.5
    assert result.message_count == 1287
    assert result.storage_format == "mcap"
    assert result.mode_effective == "live"
    assert len(result.topics) == 2
    cmd_vel = next(t for t in result.topics if t.name == "/cmd_vel")
    assert cmd_vel.message_type == "geometry_msgs/msg/Twist"
    assert cmd_vel.message_count == 425
    assert cmd_vel.frequency_hz == pytest.approx(10.0)


def test_parse_bag_info_zero_duration_yields_no_frequency() -> None:
    sample = "Storage id: sqlite3\nDuration: 0.000s\nMessages: 0\n"
    result = parse_bag_info(sample, fallback_path="/x/empty.db3", mode_effective="live")
    assert result.duration_seconds == 0.0
    assert result.message_count == 0
    assert result.topics == []
    assert result.mode_effective == "live"
