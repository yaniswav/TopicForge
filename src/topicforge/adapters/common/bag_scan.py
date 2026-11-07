"""One bounded pass over the messages of a bag: per-topic spans and anomalies.

The pass keeps the record time of every message of the topics worth checking, decodes
`/clock` and `/tf` in full, and decodes only a capped sample of messages per other topic for
their header stamp. Raw payloads are otherwise never decoded. The detectors are in
`bag_anomalies.py`.

The pass stops at a time budget (and at the active call budget). A stopped pass has seen only
the start of the bag: its spans are dropped (the caller keeps counts and whole-bag rates) and
its note says which part was covered, so an empty anomaly list never reads as "checked".
"""

from __future__ import annotations

import time
from array import array
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from topicforge import budget
from topicforge.adapters.common.bag_anomalies import find_anomalies
from topicforge.adapters.common.bag_kind import classify_bag_topic
from topicforge.adapters.common.bag_stats import TopicSpan

# Time the pass may run, whatever the size of the bag.
BAG_SCAN_MAX_SECONDS = 8.0
# Header stamps are decoded on at most this many messages per topic, evenly spread.
STAMP_SAMPLES_PER_TOPIC = 1000
_CLOCK_CHECK_EVERY = 16
_CALL_RESERVE_S = 2.0

_NOT_CHECKED = frozenset({"/clock", "/tf", "/tf_static", "/rosout", "/parameter_events"})
_NS = 1_000_000_000


@dataclass(frozen=True)
class BagScan:
    """What a pass found: complete `spans` (else `None`), `anomalies` and one `note` or `None`."""

    spans: dict[str, TopicSpan] | None
    anomalies: list[str] = field(default_factory=list)
    note: str | None = None


@dataclass
class _Topic:
    name: str
    message_type: str
    latched: bool | None
    expected: int
    count: int = 0
    first: int | None = None
    last: int | None = None
    times: array | None = None
    pairs: list[tuple[int, int]] = field(default_factory=list)
    stride: int = 1
    stampable: bool = False


def scan_reader(
    reader: Any,
    *,
    latched_of: Callable[[Any], bool | None],
    known_spans: dict[str, TopicSpan] | None = None,
    time_budget_s: float = BAG_SCAN_MAX_SECONDS,
) -> BagScan:
    """Scan every message of `reader` once, within `time_budget_s` and the call budget.

    `known_spans` are exact spans already read elsewhere (a `.db3` bag): they are kept when
    the pass is cut. `latched_of` tells the latching of a connection.
    """
    topics = _topics_of(reader, latched_of)
    clock: list[tuple[int, int]] = []
    edges: dict[tuple[str, str], array] = {}
    total = int(getattr(reader, "message_count", 0) or 0)
    deadline = time.monotonic() + _allowed_seconds(time_budget_s)
    seen = 0
    cut_at: int | None = None
    for seen, (connection, timestamp, raw) in enumerate(reader.messages(), start=1):
        if seen % _CLOCK_CHECK_EVERY == 0 and time.monotonic() > deadline:
            cut_at = int(timestamp)
            break
        ts = int(timestamp)
        topic = topics[connection.topic]
        _note_time(topic, ts)
        if topic.name == "/clock":
            _read_clock(reader, connection, raw, ts, clock)
        elif topic.name == "/tf":
            _read_tf(reader, connection, raw, ts, edges)
        elif topic.stampable and (topic.count - 1) % topic.stride == 0:
            _sample_stamp(reader, connection, raw, ts, topic)
    origin = _origin(reader, topics)
    anomalies = find_anomalies(
        origin_ns=origin,
        clock=clock,
        tf_edges=edges,
        topic_times={t.name: t.times for t in topics.values() if t.times is not None},
        stamps={t.name: t.pairs for t in topics.values() if t.pairs},
    )
    spans = known_spans
    if cut_at is None and spans is None:
        spans = {t.name: _span_of(t) for t in topics.values()}
    note = None
    if cut_at is not None:
        note = _cut_note(reader, time_budget_s, seen - 1, total, cut_at, origin, spans)
    return BagScan(spans=spans, anomalies=anomalies, note=note)


def _allowed_seconds(time_budget_s: float) -> float:
    left = budget.remaining()
    return time_budget_s if left is None else min(time_budget_s, left - _CALL_RESERVE_S)


def _topics_of(reader: Any, latched_of: Callable[[Any], bool | None]) -> dict[str, _Topic]:
    """One `_Topic` per topic name, with its expected message count and check role."""
    topics: dict[str, _Topic] = {}
    for connection in reader.connections:
        name = connection.topic
        expected = int(getattr(connection, "msgcount", 0) or 0)
        topic = topics.get(name)
        if topic is None:
            topic = topics[name] = _Topic(
                name, connection.msgtype, latched_of(connection), expected
            )
        else:
            topic.expected += expected
    for topic in topics.values():
        checked = topic.name not in _NOT_CHECKED and classify_bag_topic(topic.name) == "user"
        if checked and topic.latched is not True:
            topic.times = array("q")
            topic.stampable = True
            topic.stride = max(1, -(-topic.expected // STAMP_SAMPLES_PER_TOPIC))
    return topics


def _note_time(topic: _Topic, ts: int) -> None:
    topic.count += 1
    topic.first = ts if topic.first is None else min(topic.first, ts)
    topic.last = ts if topic.last is None else max(topic.last, ts)
    if topic.times is not None:
        topic.times.append(ts)


def _span_of(topic: _Topic) -> TopicSpan:
    return TopicSpan(
        name=topic.name,
        message_type=topic.message_type,
        count=topic.count,
        first_ns=topic.first,
        last_ns=topic.last,
        latched=topic.latched,
    )


def _origin(reader: Any, topics: dict[str, _Topic]) -> int:
    start = getattr(reader, "start_time", None)
    if isinstance(start, int) and not isinstance(start, bool) and start >= 0:
        return start
    firsts = [t.first for t in topics.values() if t.first is not None]
    return min(firsts) if firsts else 0


def _read_clock(
    reader: Any, connection: Any, raw: bytes, ts: int, out: list[tuple[int, int]]
) -> None:
    try:
        stamp = reader.deserialize(raw, connection.msgtype).clock
        out.append((ts, _time_ns(stamp)))
    except Exception:  # an undecodable /clock is simply not checked
        return


def _read_tf(
    reader: Any, connection: Any, raw: bytes, ts: int, edges: dict[tuple[str, str], array]
) -> None:
    try:
        for transform in reader.deserialize(raw, connection.msgtype).transforms:
            key = (
                str(transform.header.frame_id).lstrip("/"),
                str(transform.child_frame_id).lstrip("/"),
            )
            edges.setdefault(key, array("q")).append(ts)
    except Exception:  # an undecodable /tf is simply not checked
        return


def _sample_stamp(reader: Any, connection: Any, raw: bytes, ts: int, topic: _Topic) -> None:
    """Record the header stamp of one sampled message; stop sampling a topic with no header."""
    try:
        header = getattr(reader.deserialize(raw, connection.msgtype), "header", None)
        stamp = _time_ns(header.stamp) if header is not None else None
    except Exception:
        stamp = None
    if stamp is None:
        topic.stampable = False
        topic.pairs.clear()
        return
    topic.pairs.append((ts, stamp))


def _time_ns(stamp: Any) -> int:
    """Nanoseconds of a ROS 2 `Time` (`sec`, `nanosec`) or a ROS 1 `Time` (`secs`, `nsecs`)."""
    sec = getattr(stamp, "sec", None)
    if sec is None:
        return int(stamp.secs) * _NS + int(stamp.nsecs)
    return int(sec) * _NS + int(stamp.nanosec)


def _cut_note(
    reader: Any,
    budget_s: float,
    seen: int,
    total: int,
    cut_ns: int,
    origin: int,
    spans: dict[str, TopicSpan] | None,
) -> str:
    end = getattr(reader, "end_time", None)
    covered = ""
    if isinstance(end, int) and end > origin:
        covered = (
            f" (the first {max(0, min(100, (cut_ns - origin) * 100 // (end - origin)))}% by time)"
        )
    of_total = f" of {total}" if total else ""
    text = (
        f"The bag scan stopped at its {budget_s:g} s time budget after {seen}{of_total} "
        f"messages{covered}: anomalies were checked on that part only, so no anomaly in the "
        "rest is ruled out."
    )
    if spans is None:
        text += " Per-topic rates are count / bag duration."
    return text
