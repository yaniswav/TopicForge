"""Bag analysis service over the `rosbags` library.

`rosbags` (pure Python) reads MCAP, ROS2 `.db3` and ROS1 `.bag` through one
`AnyReader` API. It is imported lazily so the core installs without it.
`peek_bag_samples` requires it; `analyze_bag` can fall back to the text
parsing of `ros2 bag info`.

Decoded samples share the payload shape of `peek_dds_samples`, through
`adapters/common/cdr_decoder.py`. Per-topic spans and latching are in
`bag_stats.py`.

rosbag2 bags recorded before Jazzy (`.db3` on Humble) embed no message
definitions, so the reader is given the typestore of the bag's distro, or
Humble when the bag does not record one.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

from topicforge import budget
from topicforge.adapters.base import AdapterError
from topicforge.adapters.common import annotate_raw
from topicforge.adapters.common.bag_range import range_note, validate_range, window_ns
from topicforge.adapters.common.cdr_decoder import stringify_non_finite
from topicforge.adapters.common.log_filter import LogFilter, first_matches_note, require_log_topic
from topicforge.adapters.common.stamps import payload_stamp
from topicforge.constants import MAX_SAMPLE_COUNT
from topicforge.models import (
    BagAnalysis,
    BagTopicStats,
    MessageSample,
    MessageSummary,
    SampleResult,
    TopicRate,
)
from topicforge.services.bag_stats import (
    TopicSpan,
    build_topic_stats,
    detect_ros_distro,
    parse_offered_qos_latched,
    read_db3_spans,
)
from topicforge.services.summaries import compute_rate, needs_whole_arrays, summarize_message

log = logging.getLogger(__name__)

_BAG_FORMAT_BY_EXTENSION: dict[str, str] = {
    ".mcap": "mcap",
    ".db3": "db3",
    ".bag": "bag",
}
_ROSBAGS_REQUIRED_MSG = (
    "Reading bag samples requires the `rosbags` library, which topicforge installs "
    "by default but could not import here: reinstall it with "
    "`pip install --force-reinstall rosbags`. `analyze_bag` can still summarize a bag "
    "through `ros2 bag info` when ROS 2 is installed; `peek_bag_samples` cannot."
)
_MAX_ARRAY_ELEMENTS = 4096
# Per-topic times of a non-db3 bag need a pass over every message; past either
# budget the scan is skipped and the caller keeps whole-bag rates.
SPAN_SCAN_MAX_BYTES = 200 * 1024 * 1024
SPAN_SCAN_MAX_SECONDS = 5.0
_SCAN_CLOCK_CHECK_EVERY = 1024
# A filtered or ranged bag peek may decode far more messages than it returns; it stops
# at this many seconds (or when the call budget runs out), checked every N messages.
PEEK_SCAN_MAX_SECONDS = 20.0
_PEEK_CLOCK_CHECK_EVERY = 64


class _ScanBudgetExceeded(Exception):
    """The message scan for per-topic spans ran past its time budget."""


def detect_bag_format(path: str) -> str:
    """Classify a bag by path extension: `"mcap"`, `"db3"`, `"bag"` or `"unknown"`.

    Does not touch the filesystem.
    """
    suffix = Path(path).suffix.lower()
    return _BAG_FORMAT_BY_EXTENSION.get(suffix, "unknown")


def is_rosbags_available() -> bool:
    """True when `rosbags` is importable."""
    import importlib.util

    try:
        return importlib.util.find_spec("rosbags") is not None
    except (ModuleNotFoundError, ValueError):
        return False


class BagService:
    """Facade over `rosbags`.

    * `analyze(path)` returns a `BagAnalysis` with per-topic stats, format
      and duration. It raises when `rosbags` is missing, so callers check
      `is_rosbags_available()` and fall back to the CLI parser.
    * `peek_samples(path, topic, count)` returns decoded samples as a
      `SampleResult`, with `_decode_status` annotations.
    """

    def __init__(self) -> None:
        if not is_rosbags_available():
            # Construction does not raise; the methods do.
            log.debug("BagService instantiated without `rosbags` ; methods will raise.")

    def analyze(self, path: str, *, mode_effective: str = "live") -> BagAnalysis:
        """Read `path` with `rosbags` into a `BagAnalysis`.

        Raises `AdapterError` when `rosbags` is missing, the path does not
        exist, or the bag cannot be opened.
        """
        if not is_rosbags_available():
            raise AdapterError(_ROSBAGS_REQUIRED_MSG)

        resolved = Path(path)
        if not resolved.exists():
            raise AdapterError(f"bag path does not exist: {path!r}")

        bag_format = detect_bag_format(path)
        try:
            reader_data = _read_with_rosbags(resolved)
        except AdapterError:
            raise
        except Exception as exc:  # pragma: no cover: defensive
            raise AdapterError(
                f"failed to open bag {path!r} ({type(exc).__name__}: {exc})"
            ) from exc

        return BagAnalysis(
            path=str(path),
            storage_format=bag_format if bag_format != "unknown" else None,
            duration_s=reader_data["duration_s"],
            message_count=reader_data["message_count"],
            topics=reader_data["topics"],
            anomalies=[],
            start_ns=reader_data["start_ns"],
            mode_effective=mode_effective,  # type: ignore[arg-type]
            bag_format=bag_format,  # type: ignore[arg-type]
        )

    def peek_samples(
        self,
        path: str,
        topic: str,
        count: int,
        *,
        mode_effective: str = "live",
        start_s: float | None = None,
        end_s: float | None = None,
        log_filter: LogFilter | None = None,
    ) -> SampleResult:
        """Return up to `count` decoded samples for `topic` from `path`.

        `start_s` / `end_s` select on the record time, bag-relative seconds `[start, end)`.
        `log_filter` keeps only the matching `rcl_interfaces/msg/Log` messages, the first
        `count` of them in bag order.
        """
        if not is_rosbags_available():
            raise AdapterError(_ROSBAGS_REQUIRED_MSG)
        if count < 0:
            raise AdapterError("count must be >= 0")
        validate_range(start_s, end_s)

        resolved = Path(path)
        if not resolved.exists():
            raise AdapterError(f"bag path does not exist: {path!r}")

        clamped = min(count, MAX_SAMPLE_COUNT)
        try:
            samples, note = _peek_with_rosbags(
                resolved, topic, clamped, start_s=start_s, end_s=end_s, log_filter=log_filter
            )
        except AdapterError:
            raise
        except Exception as exc:  # pragma: no cover: defensive
            raise AdapterError(
                f"failed to peek samples from {path!r} ({type(exc).__name__}: {exc})"
            ) from exc

        return SampleResult(
            topic=topic,
            count=len(samples),
            samples=samples,
            mode_effective=mode_effective,  # type: ignore[arg-type]
            note=note,
            rate=_bag_rate(samples),
        )


def _bag_rate(samples: list[MessageSample]) -> TopicRate:
    """Rate block of the peeked messages, on the bag record time.

    The messages are the first `count` of the topic, so the window has no end to measure
    a trailing gap against.
    """
    recorded = [s.recorded_ns for s in samples if s.recorded_ns is not None]
    stamped = all(s.stamp_source in ("header", "payload") for s in samples)
    return compute_rate(
        recorded,
        basis="recorded_ns",
        stamps_ns=[s.timestamp_ns for s in samples] if samples and stamped else None,
        stamped=[(s.recorded_ns, s.timestamp_ns, s.stamp_source) for s in samples if s.recorded_ns],
    )


_METADATA_START_NS = re.compile(r"starting_time:\s*nanoseconds_since_epoch:\s*(\d+)")


def _metadata_start_ns(path: Path) -> int | None:
    """`starting_time` of the rosbag2 `metadata.yaml` next to `path`, `None` when absent."""
    meta = (path if path.is_dir() else path.parent) / "metadata.yaml"
    if meta.is_file() and (m := _METADATA_START_NS.search(meta.read_text(errors="replace"))):
        return int(m.group(1))
    return None


def _reader_start_ns(start: Any) -> int | None:
    """A reader's `start_time` as ns; 0 is a valid start (sim time), a non-int is not."""
    if isinstance(start, bool) or not isinstance(start, int) or start < 0:
        return None
    return start


def read_start_ns(path: Path) -> int | None:
    """Record time in ns of the first message of the bag at `path`, `None` when unreadable.

    Reads rosbag2 `metadata.yaml` (`starting_time`) when present, else the `rosbags`
    reader. A bag that starts at 0 (sim time) reports 0. Never raises.
    """
    try:
        if (found := _metadata_start_ns(path)) is not None:
            return found
        if not is_rosbags_available() or not path.exists():
            return None
        reader, _ = _open_reader(path)
        with reader:
            return _reader_start_ns(getattr(reader, "start_time", None))
    except Exception as exc:
        log.debug("could not read the start time of %s: %s", path, exc)
        return None


def read_topic_spans(path: Path) -> dict[str, TopicSpan] | None:
    """Per-topic spans of the bag at `path`, or `None` when it cannot be read here.

    See `scan_topic_spans` for the budget on non-db3 bags. Never raises.
    """
    return scan_topic_spans(path)[0]


def scan_topic_spans(path: Path) -> tuple[dict[str, TopicSpan] | None, str | None]:
    """`(spans, note)` for the bag at `path`; `spans` is `None` when not readable here.

    `.db3` bags need only the standard library. Other containers need
    `rosbags` and a scan of their message timestamps, which is skipped when
    the bag exceeds `SPAN_SCAN_MAX_BYTES` or the scan runs past
    `SPAN_SCAN_MAX_SECONDS`; `note` then says why. Never raises.
    """
    try:
        spans = read_db3_spans(path)
        if spans is not None:
            return spans, None
        if not is_rosbags_available() or not path.exists():
            return None, None
        size = _bag_size_bytes(path)
        if size > SPAN_SCAN_MAX_BYTES:
            return None, (
                f"Per-topic rates are count / bag duration: the bag is {size // (1024 * 1024)} MiB, "
                f"over the {SPAN_SCAN_MAX_BYTES // (1024 * 1024)} MiB limit for reading "
                "per-topic message times."
            )
        reader, _ = _open_reader(path)
        with reader:
            return _spans_from_messages(reader, time_budget_s=SPAN_SCAN_MAX_SECONDS), None
    except _ScanBudgetExceeded:
        return None, (
            "Per-topic rates are count / bag duration: reading per-topic message times "
            f"took longer than {SPAN_SCAN_MAX_SECONDS:g} s and was stopped."
        )
    except Exception as exc:
        log.debug("could not read per-topic spans from %s: %s", path, exc)
        return None, None


def _bag_size_bytes(path: Path) -> int:
    """Total size of the bag file, or of the files under a bag directory."""
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _typestore_for(resolved: Path) -> tuple[Any, str]:
    """The typestore for bags without embedded definitions, and a description of it.

    The distro the bag records when `rosbags` has a store for it, else ROS 2 Humble.
    """
    from rosbags.typesys import Stores, get_typestore  # type: ignore[import-not-found]

    distro = detect_ros_distro(resolved)
    store = Stores.__members__.get(f"ROS2_{distro.upper()}") if distro else None
    if store is not None and distro:
        return get_typestore(store), f"ROS 2 {distro.capitalize()} (the distro the bag records)"
    reason = (
        f"the bag records an unknown distro {distro!r}" if distro else "the bag records no distro"
    )
    return get_typestore(Stores.ROS2_HUMBLE), f"ROS 2 Humble (assumed: {reason})"


def _open_reader(resolved: Path) -> tuple[Any, str]:
    """An `AnyReader` over `resolved` with a default typestore, and that typestore's description."""
    from rosbags.highlevel import AnyReader  # type: ignore[import-not-found]

    typestore, description = _typestore_for(resolved)
    return AnyReader([resolved], default_typestore=typestore), description


def _lacks_embedded_definitions(reader: Any) -> bool:
    """True when some connection carries no message definition (`.db3` recorded before Jazzy)."""
    for connection in reader.connections:
        fmt = getattr(getattr(connection, "msgdef", None), "format", None)
        if getattr(fmt, "name", "") == "NONE":
            return True
    return False


def _connection_latched(connection: Any) -> bool | None:
    """Latching of a bag connection: the ROS 1 flag, or a transient_local ROS 2 profile."""
    ext = getattr(connection, "ext", None)
    if ext is None:
        return None
    if hasattr(ext, "latching"):
        return None if ext.latching is None else bool(ext.latching)
    profiles = getattr(ext, "offered_qos_profiles", None)
    if not profiles:
        return None
    if isinstance(profiles, str):
        return parse_offered_qos_latched(profiles)
    return any(
        getattr(getattr(p, "durability", None), "name", "") == "TRANSIENT_LOCAL" for p in profiles
    )


def _spans_from_messages(
    reader: Any, *, time_budget_s: float | None = None
) -> dict[str, TopicSpan]:
    """Per-topic spans from message timestamps, without deserializing (non-db3 bags).

    Raises `_ScanBudgetExceeded` when `time_budget_s` runs out.
    """
    first: dict[str, int] = {}
    last: dict[str, int] = {}
    count: dict[str, int] = {}
    deadline = None if time_budget_s is None else time.monotonic() + time_budget_s
    for seen, (connection, timestamp, _raw) in enumerate(reader.messages(), start=1):
        if (
            deadline is not None
            and seen % _SCAN_CLOCK_CHECK_EVERY == 0
            and time.monotonic() > deadline
        ):
            raise _ScanBudgetExceeded
        topic = connection.topic
        ts = int(timestamp)
        count[topic] = count.get(topic, 0) + 1
        first[topic] = min(first.get(topic, ts), ts)
        last[topic] = max(last.get(topic, ts), ts)
    spans: dict[str, TopicSpan] = {}
    for connection in reader.connections:
        topic = connection.topic
        if topic not in spans:
            spans[topic] = TopicSpan(
                name=topic,
                message_type=connection.msgtype,
                count=count.get(topic, 0),
                first_ns=first.get(topic),
                last_ns=last.get(topic),
                latched=_connection_latched(connection),
            )
    return spans


def _read_with_rosbags(resolved: Path) -> dict[str, Any]:
    """Open `resolved` with `rosbags` and compute per-topic stats.

    Returns a dict with `duration_s`, `message_count` and `topics`. `.db3` spans come
    from `sqlite3`; other containers are scanned through `rosbags`.
    """
    meta_start = _metadata_start_ns(resolved)
    reader, _ = _open_reader(resolved)
    with reader:
        start = getattr(reader, "start_time", None)
        end = getattr(reader, "end_time", None)
        duration_ns = 0
        if isinstance(start, int) and isinstance(end, int) and end >= start:
            duration_ns = end - start

        spans = read_db3_spans(resolved) or _spans_from_messages(reader)
        topics: list[BagTopicStats] = [build_topic_stats(span) for span in spans.values()]

    return {
        "start_ns": meta_start if meta_start is not None else _reader_start_ns(start),
        "duration_s": duration_ns / 1_000_000_000 if duration_ns > 0 else 0.0,
        "message_count": sum(t.message_count for t in topics),
        "topics": topics,
    }


def _peek_out_of_time(hard_stop: float) -> bool:
    """True when the hard cap or the active call budget of a bag scan has run out."""
    left = budget.remaining()
    return time.monotonic() > hard_stop or (left is not None and left <= 0)


def _peek_with_rosbags(
    resolved: Path,
    topic: str,
    count: int,
    *,
    start_s: float | None = None,
    end_s: float | None = None,
    log_filter: LogFilter | None = None,
) -> tuple[list[MessageSample], str | None]:
    """Up to `count` decoded samples on `topic` and a note; `AdapterError` if the topic is absent.

    The range `[start_s, end_s)` is bag-relative seconds on the record time. With a log
    filter, the first `count` matching messages in range, in bag order.
    """
    samples: list[MessageSample] = []
    capped: set[str] = set()
    scanned = 0
    stopped: str | None = None
    reader, typestore_desc = _open_reader(resolved)
    with reader:
        connections = [c for c in reader.connections if getattr(c, "topic", None) == topic]
        if not connections:
            raise AdapterError(
                f"topic {topic!r} not present in bag (known topics: "
                f"{[getattr(c, 'topic', '<?>') for c in reader.connections]!r})"
            )

        message_type = getattr(connections[0], "msgtype", "<unknown>")
        if log_filter is not None:
            require_log_topic(topic, message_type)
        whole = needs_whole_arrays(message_type)
        assumed = _lacks_embedded_definitions(reader)
        bag_start = int(reader.start_time)
        duration_s = max(int(reader.end_time) - bag_start, 0) / 1_000_000_000
        start, stop = window_ns(bag_start, start_s, end_s)
        started = time.monotonic()
        hard_stop = started + PEEK_SCAN_MAX_SECONDS
        for connection, timestamp, raw in reader.messages(
            connections=connections, start=start, stop=stop
        ):
            if len(samples) >= count:
                break
            if scanned and scanned % _PEEK_CLOCK_CHECK_EVERY == 0 and _peek_out_of_time(hard_stop):
                stopped = (
                    f"scan stopped after {time.monotonic() - started:.1f} s / {scanned} "
                    f"messages, {len(samples)} matches so far: narrow start_s/end_s or the filter."
                )
                break
            scanned += 1
            decoded = _decode_bag_message(reader, connection, raw, whole_arrays=whole)
            if log_filter is not None and not log_filter.matches(decoded):
                continue
            found = payload_stamp(decoded)
            cut_here: set[str] = set()
            payload = _cap_arrays(decoded, cut_here)
            capped.update(cut_here)
            samples.append(
                MessageSample(
                    topic=topic,
                    message_type=message_type,
                    timestamp_ns=int(timestamp) if found is None else found[0],
                    stamp_source="recorded" if found is None else found[1],
                    recorded_ns=int(timestamp),
                    payload=stringify_non_finite(payload),
                    summary=_bag_summary(message_type, decoded, set() if whole else cut_here),
                )
            )

    notes: list[str] = []
    if log_filter is not None and (text := first_matches_note(len(samples), scanned, log_filter)):
        notes.append(text)
    if stopped:
        notes.append(stopped)
    if text := range_note(start_s, end_s, duration_s=duration_s, got=len(samples)):
        notes.append(text)
    if assumed:
        notes.append(
            "The bag stores no message definitions; samples were decoded with the "
            f"{typestore_desc} type definitions."
        )
    if capped:
        notes.append(
            f"Arrays longer than {_MAX_ARRAY_ELEMENTS} elements were cut to their first "
            f"{_MAX_ARRAY_ELEMENTS} (fields: {', '.join(sorted(capped))})."
        )
    return samples, " ".join(notes) or None


def _cap_arrays(payload: dict[str, Any], capped: set[str], prefix: str = "") -> dict[str, Any]:
    """Cut lists over `_MAX_ARRAY_ELEMENTS`, recording the field paths in `capped`."""
    out: dict[str, Any] = {}
    for key, value in payload.items():
        path = f"{prefix}{key}"
        if isinstance(value, list) and len(value) > _MAX_ARRAY_ELEMENTS:
            capped.add(path)
            out[key] = value[:_MAX_ARRAY_ELEMENTS]
        elif isinstance(value, dict):
            out[key] = _cap_arrays(value, capped, f"{path}.")
        else:
            out[key] = value
    return out


def _bag_summary(
    message_type: str, decoded: dict[str, Any], cut_fields: set[str]
) -> MessageSummary | None:
    """Summary of a fully decoded message; `None` when the decode was partial or failed."""
    if decoded.get("_decode_status") != "full":
        return None
    return summarize_message(message_type, decoded, cut_fields)


def _decode_bag_message(
    reader: Any, connection: Any, raw: bytes, *, whole_arrays: bool = False
) -> dict[str, Any]:
    """Deserialize one bag message and run it through `decode_dynamic_sample`.

    `whole_arrays` keeps every array element (for a summary that reads an array);
    otherwise an array is read up to `_MAX_ARRAY_ELEMENTS` and one more.
    """
    try:
        deserialized = reader.deserialize(raw, connection.msgtype)
    except Exception as exc:  # pragma: no cover: binding-side error
        return annotate_raw(
            raw if isinstance(raw, (bytes, bytearray)) else b"",
            note=f"rosbags deserialize failed: {exc}",
        )

    from topicforge.adapters.common.cdr_decoder import decode_dynamic_sample

    return decode_dynamic_sample(
        deserialized, max_array_elements=None if whole_arrays else _MAX_ARRAY_ELEMENTS
    )
