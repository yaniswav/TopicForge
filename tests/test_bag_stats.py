"""Per-topic spans, rates and latching of bags, read with the standard library only."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from topicforge.adapters.ros2_live.adapter import Ros2CliAdapter, parse_bag_info
from topicforge.services import bag_service
from topicforge.services.bag_service import read_topic_spans, scan_topic_spans
from topicforge.services.bag_stats import (
    TopicSpan,
    build_topic_stats,
    db3_files,
    detect_ros_distro,
    overlay_spans,
    parse_offered_qos_latched,
    read_db3_spans,
    span_frequency,
)

BAG_DIR = Path(__file__).parent / "fixtures" / "bags" / "omnisim_humble"
BAG_DB3 = BAG_DIR / "omnisim_husky_topicforge_20261002_190546_0.db3"

# (n - 1) / (last - first) per topic, from the OmniSim team's independent rosbags read.
EXPECTED_RATES_HZ = {
    "/scan": 4.9974,
    "/clock": 9.9965,
    "/odom": 5.0002,
    "/imu/data": 5.0001,
    "/gps/local": 4.9971,
    "/husky/joint_states": 2.4989,
    "/tf": 7.5151,
    "/parameter_events": 0.0706,
}


def test_span_frequency_is_n_minus_one_over_span() -> None:
    assert span_frequency(11, 0, 10_000_000_000) == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("count", "first", "last"),
    [(0, None, None), (1, 5, 5), (3, 7, 7), (2, None, 10), (2, 10, 5)],
)
def test_span_frequency_none_without_a_usable_span(
    count: int, first: int | None, last: int | None
) -> None:
    assert span_frequency(count, first, last) is None


def test_build_topic_stats_marks_basis_only_when_there_is_a_rate() -> None:
    rated = build_topic_stats(TopicSpan("/a", "t/msg/A", 3, 0, 2_000_000_000, False))
    assert rated.frequency_hz == pytest.approx(1.0)
    assert rated.frequency_basis == "topic_span"
    assert rated.first_timestamp_ns == 0 and rated.last_timestamp_ns == 2_000_000_000
    assert rated.latched is False

    single = build_topic_stats(TopicSpan("/b", "t/msg/B", 1, 5, 5, None))
    assert single.frequency_hz is None and single.frequency_basis is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("- history: 3\n  depth: 0\n  reliability: 1\n  durability: 1\n  deadline:\n", True),
        ("- history: 3\n  reliability: 1\n  durability: 2\n", False),
        ("- durability: 2\n- durability: 1\n", True),
        ("- durability: transient_local\n", True),
        ("- durability: volatile\n", False),
        ("- liveliness_lease_duration:\n    sec: 1\n", None),
        ("", None),
    ],
)
def test_parse_offered_qos_latched(text: str, expected: bool | None) -> None:
    assert parse_offered_qos_latched(text) is expected


def test_db3_files_for_file_directory_and_other(tmp_path: Path) -> None:
    assert db3_files(BAG_DB3) == [BAG_DB3]
    assert db3_files(BAG_DIR) == [BAG_DB3]
    other = tmp_path / "x.mcap"
    other.write_bytes(b"")
    assert db3_files(other) == []
    assert db3_files(tmp_path / "missing") == []


def test_read_db3_spans_matches_the_omnisim_ground_truth() -> None:
    spans = read_db3_spans(BAG_DIR)
    assert spans is not None
    assert sum(s.count for s in spans.values()) == 1434
    for name, expected in EXPECTED_RATES_HZ.items():
        rate = span_frequency(spans[name].count, spans[name].first_ns, spans[name].last_ns)
        assert rate == pytest.approx(expected, abs=1e-4), name


def test_read_db3_spans_flags_latched_topics() -> None:
    spans = read_db3_spans(BAG_DB3)
    assert spans is not None
    assert spans["/tf_static"].latched is True
    assert spans["/rosout"].latched is True
    assert spans["/scan"].latched is False
    assert spans["/tf"].latched is False


def test_read_db3_spans_none_for_a_file_that_is_not_sqlite(tmp_path: Path) -> None:
    bad = tmp_path / "bad.db3"
    bad.write_bytes(b"not a database")
    assert read_db3_spans(bad) is None


def test_read_db3_spans_merges_split_files(tmp_path: Path) -> None:
    for i, (first, last) in enumerate([(0, 1_000_000_000), (2_000_000_000, 3_000_000_000)]):
        db = sqlite3.connect(tmp_path / f"bag_{i}.db3")
        db.executescript(
            "CREATE TABLE topics(id INTEGER PRIMARY KEY, name TEXT, type TEXT,"
            " serialization_format TEXT, offered_qos_profiles TEXT);"
            "CREATE TABLE messages(id INTEGER PRIMARY KEY, topic_id INTEGER,"
            " timestamp INTEGER, data BLOB);"
            "INSERT INTO topics VALUES (1, '/a', 'p/msg/A', 'cdr', '');"
        )
        db.executemany(
            "INSERT INTO messages(topic_id, timestamp, data) VALUES (1, ?, x'00')",
            [(first,), (last,)],
        )
        db.commit()
        db.close()
    spans = read_db3_spans(tmp_path)
    assert spans is not None
    assert spans["/a"].count == 4
    assert (spans["/a"].first_ns, spans["/a"].last_ns) == (0, 3_000_000_000)
    assert spans["/a"].latched is None


def test_detect_ros_distro_from_the_db3_schema_table() -> None:
    assert detect_ros_distro(BAG_DB3) == "humble"
    assert detect_ros_distro(BAG_DIR) == "humble"


def test_detect_ros_distro_from_metadata_yaml(tmp_path: Path) -> None:
    (tmp_path / "metadata.yaml").write_text("rosbag2_bagfile_information:\n  ros_distro: jazzy\n")
    assert detect_ros_distro(tmp_path) == "jazzy"
    assert detect_ros_distro(tmp_path / "missing.mcap") == "jazzy"


def test_detect_ros_distro_none_without_information(tmp_path: Path) -> None:
    assert detect_ros_distro(tmp_path) is None


def test_overlay_replaces_the_whole_bag_rate_with_the_span_rate() -> None:
    cli = parse_bag_info(
        (BAG_DIR / "ros2_bag_info.txt").read_text(), fallback_path="x", mode_effective="live"
    )
    scan_before = next(t for t in cli.topics if t.name == "/scan")
    assert scan_before.frequency_basis == "bag_duration"
    assert scan_before.frequency_hz == pytest.approx(5.0058, abs=1e-4)

    spans = read_topic_spans(BAG_DIR)
    assert spans is not None
    by_name = {t.name: t for t in overlay_spans(cli.topics, spans)}
    assert by_name["/scan"].frequency_hz == pytest.approx(4.9974, abs=1e-4)
    assert by_name["/scan"].frequency_basis == "topic_span"
    assert by_name["/scan"].message_count == 177
    assert by_name["/tf_static"].latched is True
    assert by_name["/events/write_split"].frequency_hz is None


def test_overlay_keeps_the_duration_rate_for_a_topic_the_spans_lack() -> None:
    cli = parse_bag_info(
        (BAG_DIR / "ros2_bag_info.txt").read_text(), fallback_path="x", mode_effective="live"
    )
    out = overlay_spans(cli.topics, {})
    assert all(t.frequency_basis == "bag_duration" for t in out if t.frequency_hz is not None)
    assert all(t.latched is None for t in out)


def test_read_topic_spans_none_for_a_missing_path(tmp_path: Path) -> None:
    assert read_topic_spans(tmp_path / "nope.mcap") is None


def _latched(count: int, span_s: float) -> TopicSpan:
    return TopicSpan("/l", "t/msg/L", count, 0, int(span_s * 1_000_000_000), True)


def test_latched_burst_under_a_second_has_no_rate() -> None:
    stats = build_topic_stats(_latched(13, 0.008))
    assert stats.frequency_hz is None and stats.frequency_basis is None
    assert stats.latched is True


def test_latched_topic_published_over_a_longer_span_keeps_its_rate() -> None:
    stats = build_topic_stats(_latched(11, 10.0))
    assert stats.frequency_hz == pytest.approx(1.0)
    assert stats.frequency_basis == "topic_span"
    assert stats.latched is True


def test_latched_span_of_exactly_one_second_keeps_its_rate() -> None:
    assert build_topic_stats(_latched(3, 1.0)).frequency_hz == pytest.approx(2.0)


def test_scan_topic_spans_for_a_missing_path_has_no_note(tmp_path: Path) -> None:
    assert scan_topic_spans(tmp_path / "nope.mcap") == (None, None)


# ---- non-db3 scan budget and rosbags 0.11 connection shapes ---------------------


class _FakeReader:
    def __init__(self, messages: int) -> None:
        self.connections = [SimpleNamespace(topic="/a", msgtype="t/msg/A", ext=None)]
        self._n = messages

    def __enter__(self) -> _FakeReader:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def messages(self) -> Iterator[tuple[object, int, bytes]]:
        for i in range(self._n):
            yield self.connections[0], i, b""


def _fake_mcap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, messages: int) -> Path:
    bag = tmp_path / "x.mcap"
    bag.write_bytes(b"0" * 64)
    monkeypatch.setattr(bag_service, "is_rosbags_available", lambda: True)
    monkeypatch.setattr(bag_service, "_open_reader", lambda p: (_FakeReader(messages), ""))
    return bag


def test_a_large_non_db3_bag_is_not_scanned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bag = _fake_mcap(tmp_path, monkeypatch, 10)
    monkeypatch.setattr(bag_service, "SPAN_SCAN_MAX_BYTES", 8)
    spans, note = scan_topic_spans(bag)
    assert spans is None
    assert note is not None and "bag duration" in note


def test_a_slow_scan_stops_at_the_time_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bag = _fake_mcap(tmp_path, monkeypatch, 5000)
    monkeypatch.setattr(bag_service, "SPAN_SCAN_MAX_SECONDS", -1.0)
    spans, note = scan_topic_spans(bag)
    assert spans is None
    assert note is not None and "stopped" in note


def test_a_small_fast_scan_returns_spans(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bag = _fake_mcap(tmp_path, monkeypatch, 5)
    spans, note = scan_topic_spans(bag)
    assert note is None and spans is not None
    assert (spans["/a"].count, spans["/a"].first_ns, spans["/a"].last_ns) == (5, 0, 4)


def test_analyze_bag_carries_the_scan_note(monkeypatch: pytest.MonkeyPatch) -> None:
    info = (BAG_DIR / "ros2_bag_info.txt").read_text()
    adapter = Ros2CliAdapter()
    monkeypatch.setattr(adapter, "_run", lambda *a, **k: info)
    monkeypatch.setattr(
        bag_service,
        "scan_topic_spans",
        lambda p: (None, "Per-topic rates are count / bag duration"),
    )
    analysis = adapter.analyze_bag(str(BAG_DIR))
    assert analysis.note == "Per-topic rates are count / bag duration"
    assert all(t.frequency_basis == "bag_duration" for t in analysis.topics if t.frequency_hz)


@pytest.mark.parametrize(
    ("profiles", "expected"),
    [
        ("- durability: 1\n  history: 3\n", True),
        ("- durability: 2\n  history: 3\n", False),
        ("", None),
        ([SimpleNamespace(durability=SimpleNamespace(name="TRANSIENT_LOCAL"))], True),
    ],
)
def test_connection_latched_reads_a_str_or_object_profile(
    profiles: object, expected: bool | None
) -> None:
    connection = SimpleNamespace(ext=SimpleNamespace(offered_qos_profiles=profiles))
    assert bag_service._connection_latched(connection) is expected
