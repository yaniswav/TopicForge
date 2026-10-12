"""Per-topic message spans and latching for bags.

A topic's rate is `(n - 1) / (last - first)` over its own messages, not
`n / bag duration`. For rosbag2 `.db3` bags the spans come from one stdlib
`sqlite3` query, so no ROS 2 install and no `rosbags` are needed. Other
containers go through `rosbags` (see `bag_service.py`), which feeds the same
`TopicSpan` and `build_topic_stats`.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from topicforge.adapters.common.bag_kind import classify_bag_topic
from topicforge.models import BagTopicStats

log = logging.getLogger(__name__)

_NS_PER_S = 1_000_000_000
# A latched topic whose messages all fall within this span is a start-up burst.
_LATCHED_BURST_SPAN_NS = _NS_PER_S

_DURABILITY_LINE = re.compile(r"^\s*-?\s*durability:\s*(\S+)\s*$", re.MULTILINE)
_DISTRO_LINE = re.compile(r"^\s*ros_distro:\s*[\"']?([A-Za-z]+)[\"']?\s*$", re.MULTILINE)
# rmw durability enum value of TRANSIENT_LOCAL, as written by rosbag2.
_TRANSIENT_LOCAL_VALUE = "1"


@dataclass(frozen=True)
class TopicSpan:
    """What a bag holds for one topic: type, count, first and last message time, latching."""

    name: str
    message_type: str
    count: int
    first_ns: int | None
    last_ns: int | None
    latched: bool | None


def span_frequency(count: int, first_ns: int | None, last_ns: int | None) -> float | None:
    """`(count - 1) / span` in Hz, `None` for fewer than 2 messages or a zero span."""
    if count < 2 or first_ns is None or last_ns is None or last_ns <= first_ns:
        return None
    return (count - 1) / ((last_ns - first_ns) / _NS_PER_S)


def build_topic_stats(span: TopicSpan) -> BagTopicStats:
    """`BagTopicStats` from a span, with the per-topic-span rate.

    A latched topic whose messages span under 1 second gets no rate: that is a
    start-up burst. One published over a longer span keeps its rate.
    """
    freq = (
        None if _is_latched_burst(span) else span_frequency(span.count, span.first_ns, span.last_ns)
    )
    return BagTopicStats(
        name=span.name,
        message_type=span.message_type,
        message_count=span.count,
        kind=classify_bag_topic(span.name),
        frequency_hz=freq,
        first_timestamp_ns=span.first_ns,
        last_timestamp_ns=span.last_ns,
        frequency_basis="topic_span" if freq is not None else None,
        latched=span.latched,
    )


def _is_latched_burst(span: TopicSpan) -> bool:
    if not span.latched or span.first_ns is None or span.last_ns is None:
        return False
    return span.last_ns - span.first_ns < _LATCHED_BURST_SPAN_NS


def parse_offered_qos_latched(text: str) -> bool | None:
    """Whether a rosbag2 `offered_qos_profiles` YAML text includes a transient_local profile.

    `None` when the text lists no durability at all (no QoS recorded).
    """
    values = [m.group(1).strip("\"'").lower() for m in _DURABILITY_LINE.finditer(text or "")]
    if not values:
        return None
    return any(v in (_TRANSIENT_LOCAL_VALUE, "transient_local") for v in values)


def db3_files(path: Path) -> list[Path]:
    """The `.db3` files of a bag: the file itself, or those inside a rosbag2 directory."""
    if path.is_file():
        return [path] if path.suffix.lower() == ".db3" else []
    if path.is_dir():
        return sorted(path.glob("*.db3"))
    return []


def read_db3_spans(path: Path) -> dict[str, TopicSpan] | None:
    """Per-topic spans of a `.db3` bag, or `None` when the path is not a readable db3 bag.

    Opens the files read-only. A split bag is merged by topic name.
    """
    files = db3_files(path)
    if not files:
        return None
    merged: dict[str, TopicSpan] = {}
    try:
        for file in files:
            for span in _read_one_db3(file):
                merged[span.name] = _merge(merged.get(span.name), span)
    except sqlite3.Error as exc:
        log.debug("could not read %s as a sqlite3 bag: %s", path, exc)
        return None
    return merged


def _read_one_db3(file: Path) -> list[TopicSpan]:
    conn = sqlite3.connect(f"{file.resolve().as_uri()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT t.name, t.type, t.offered_qos_profiles, COUNT(m.id),"
            " MIN(m.timestamp), MAX(m.timestamp)"
            " FROM topics t LEFT JOIN messages m ON m.topic_id = t.id GROUP BY t.id"
        ).fetchall()
    finally:
        conn.close()
    return [
        TopicSpan(
            name=name,
            message_type=msg_type,
            count=int(count),
            first_ns=first,
            last_ns=last,
            latched=parse_offered_qos_latched(qos),
        )
        for name, msg_type, qos, count, first, last in rows
    ]


def _merge(a: TopicSpan | None, b: TopicSpan) -> TopicSpan:
    if a is None:
        return b
    firsts = [t for t in (a.first_ns, b.first_ns) if t is not None]
    lasts = [t for t in (a.last_ns, b.last_ns) if t is not None]
    if a.latched is None or b.latched is None:
        latched = a.latched if b.latched is None else b.latched
    else:
        latched = a.latched or b.latched
    return TopicSpan(
        name=a.name,
        message_type=a.message_type,
        count=a.count + b.count,
        first_ns=min(firsts) if firsts else None,
        last_ns=max(lasts) if lasts else None,
        latched=latched,
    )


def detect_ros_distro(path: Path) -> str | None:
    """The ROS distro a rosbag2 bag was recorded on (`humble`, `jazzy`, ...), if it says.

    Reads `ros_distro` from `metadata.yaml` (rosbag2 metadata v8+), else from
    the `schema` table of a `.db3` file. Bags written by `rosbags` record
    `rosbags` there, which is not a distro and is ignored by the caller.
    """
    folder = path if path.is_dir() else path.parent
    metadata = folder / "metadata.yaml"
    try:
        if metadata.is_file():
            m = _DISTRO_LINE.search(metadata.read_text(encoding="utf-8", errors="replace"))
            if m:
                return m.group(1).lower()
    except OSError:
        pass
    for file in db3_files(path)[:1]:
        try:
            conn = sqlite3.connect(f"{file.resolve().as_uri()}?mode=ro", uri=True)
            try:
                row = conn.execute("SELECT ros_distro FROM schema LIMIT 1").fetchone()
            finally:
                conn.close()
        except sqlite3.Error:
            continue
        if row and isinstance(row[0], str) and row[0].strip():
            return row[0].strip().lower()
    return None


def overlay_spans(topics: list[BagTopicStats], spans: dict[str, TopicSpan]) -> list[BagTopicStats]:
    """Replace each topic's whole-bag rate with its own span rate where `spans` knows the topic.

    Counts from `ros2 bag info` are kept; spans only add times, rate and
    latching. A topic missing from `spans` keeps its `bag_duration` rate.
    """
    out: list[BagTopicStats] = []
    for stats in topics:
        span = spans.get(stats.name)
        if span is None:
            out.append(_with_duration_basis(stats))
            continue
        span_stats = build_topic_stats(
            TopicSpan(
                name=stats.name,
                message_type=stats.message_type,
                count=stats.message_count,
                first_ns=span.first_ns,
                last_ns=span.last_ns,
                latched=span.latched,
            )
        )
        out.append(span_stats)
    return out


def _with_duration_basis(stats: BagTopicStats) -> BagTopicStats:
    if stats.frequency_hz is None:
        return stats
    return stats.model_copy(update={"frequency_basis": "bag_duration"})
