"""Unit tests for the pure parsers in the live adapter.

These tests never touch a real ROS2 install — they hit the regex parsers
directly against representative CLI output. This is how we cover the live
adapter on machines (and CI) without ROS2.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from topicforge.adapters.ros2_live.adapter import (
    parse_bag_info,
    parse_csv_echo,
    parse_echo_yaml,
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


# ---------------------------------------------------------------------------
# parse_csv_echo — covers the new `ros2 topic echo --csv --once` shape.
# ---------------------------------------------------------------------------


def test_parse_csv_echo_extracts_header_timestamp_from_fixture() -> None:
    sample = (_FIXTURES / "csv_echo_imu.txt").read_text()
    rows = parse_csv_echo(sample)
    assert len(rows) == 1
    ts_ns, payload = rows[0]
    # 1715600000 sec + 123456789 nsec -> 1715600000123456789 ns
    assert ts_ns == 1715600000 * 1_000_000_000 + 123456789
    # First payload column is the frame_id (timestamp columns are stripped).
    assert payload["col_0"] == "base_link"
    # Raw text is preserved (sans surrounding whitespace).
    assert "1715600000,123456789,base_link" in str(payload["_raw_text"])


def test_parse_csv_echo_multi_row_preserves_order_and_timestamps() -> None:
    sample = (_FIXTURES / "csv_echo_pose_multi.txt").read_text()
    rows = parse_csv_echo(sample)
    assert len(rows) == 2
    ts0, payload0 = rows[0]
    ts1, payload1 = rows[1]
    assert ts0 == 1715600100 * 1_000_000_000 + 500000000
    assert ts1 == 1715600101 * 1_000_000_000 + 750000000
    # Order matches the file order; payloads differ.
    assert payload0["col_0"] == "map"
    assert payload0["col_1"] == "1.0"
    assert payload1["col_1"] == "1.5"


def test_parse_csv_echo_empty_input_returns_empty_list() -> None:
    # No publisher / echo timed out before printing anything — adapter must
    # be allowed to return an empty sample list, not crash.
    assert parse_csv_echo("") == []


def test_parse_csv_echo_inline_single_row() -> None:
    # Compact inline case — sensor_msgs/Imu-shaped, three trailing columns
    # to confirm column indexing past the timestamp.
    sample = "1715600000,1,base_link,0.5,-0.5\n"
    rows = parse_csv_echo(sample)
    assert len(rows) == 1
    ts_ns, payload = rows[0]
    assert ts_ns == 1715600000 * 1_000_000_000 + 1
    assert payload["col_0"] == "base_link"
    assert payload["col_1"] == "0.5"
    assert payload["col_2"] == "-0.5"


def test_parse_csv_echo_headerless_row_has_zero_timestamp() -> None:
    # std_msgs/String -> a single column with the string data; no Header
    # means no timestamp columns. Leading column "hello" doesn't parse as
    # an int, so the parser keeps all columns in the payload and reports
    # timestamp_ns == 0 (documented schema behavior).
    sample = "hello,world\n"
    rows = parse_csv_echo(sample)
    assert len(rows) == 1
    ts_ns, payload = rows[0]
    assert ts_ns == 0
    assert payload["col_0"] == "hello"
    assert payload["col_1"] == "world"


def test_parse_csv_echo_skips_blank_and_comment_lines() -> None:
    sample = (
        "# comment header from a custom user wrapper\n"
        "\n"
        "1715600000,42,frame\n"
        "   \n"
        "# trailing comment\n"
    )
    rows = parse_csv_echo(sample)
    assert len(rows) == 1
    ts_ns, _payload = rows[0]
    assert ts_ns == 1715600000 * 1_000_000_000 + 42


def test_parse_csv_echo_skips_single_column_rows() -> None:
    # A degenerate one-column row can't be a Header-stamped sample and
    # isn't a useful payload; the parser drops it rather than emitting a
    # confusing `timestamp_ns=0, payload={"col_0": ...}` entry.
    sample = "just_one_field\n"
    assert parse_csv_echo(sample) == []


def test_parse_csv_echo_garbage_interleaved_does_not_crash() -> None:
    # Garbage rows still have >= 2 columns so they survive as
    # zero-timestamp payloads — the parser is intentionally tolerant. The
    # contract is "don't crash", not "filter every weird shape".
    sample = "totally,not,a,timestamp,row\n1715600000,7,frame_a\n,,empty,fields\n"
    rows = parse_csv_echo(sample)
    # Three rows survive; the middle one has the real timestamp.
    assert len(rows) == 3
    assert rows[1][0] == 1715600000 * 1_000_000_000 + 7


def test_parse_csv_echo_rejects_out_of_range_nanosec() -> None:
    # `nanosec` must be < 1e9. A row where the second column is >= 1e9
    # cannot be a real Header timestamp, so the parser treats both leading
    # columns as ordinary payload fields.
    sample = "1715600000,1500000000,frame\n"
    rows = parse_csv_echo(sample)
    assert len(rows) == 1
    ts_ns, payload = rows[0]
    assert ts_ns == 0
    # Both "timestamp-looking" columns survive in the payload.
    assert payload["col_0"] == "1715600000"
    assert payload["col_1"] == "1500000000"
    assert payload["col_2"] == "frame"


def test_parse_csv_echo_rejects_sec_outside_plausible_epoch_window() -> None:
    # A `sec` value of 1 (e.g. an `int32` field that happens to look like
    # a small integer) is not a plausible 2000-2100 epoch second and must
    # be left in the payload — otherwise headerless integer-leading
    # messages would get a fake timestamp of `~1 ns past the epoch`.
    sample = "1,2,3\n"
    rows = parse_csv_echo(sample)
    assert len(rows) == 1
    ts_ns, payload = rows[0]
    assert ts_ns == 0
    assert payload["col_0"] == "1"
    assert payload["col_1"] == "2"
    assert payload["col_2"] == "3"


def test_parse_csv_echo_handles_negative_sec_gracefully() -> None:
    # Negative leading column can't be a valid epoch second; payload-only.
    sample = "-5,100,frame\n"
    rows = parse_csv_echo(sample)
    assert len(rows) == 1
    ts_ns, _payload = rows[0]
    assert ts_ns == 0
