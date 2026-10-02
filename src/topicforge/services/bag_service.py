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
from pathlib import Path
from typing import Any

from topicforge.adapters.base import AdapterError
from topicforge.adapters.common import annotate_raw
from topicforge.constants import MAX_SAMPLE_COUNT
from topicforge.models import (
    BagAnalysis,
    BagTopicStats,
    MessageSample,
    SampleResult,
)
from topicforge.services.bag_stats import (
    TopicSpan,
    build_topic_stats,
    detect_ros_distro,
    read_db3_spans,
)

log = logging.getLogger(__name__)

_BAG_FORMAT_BY_EXTENSION: dict[str, str] = {
    ".mcap": "mcap",
    ".db3": "db3",
    ".bag": "bag",
}
_ROSBAGS_REQUIRED_MSG = (
    "Reading bag samples requires the `rosbags` library: "
    "pip install topicforge[bags]. `analyze_bag` can still summarize a bag "
    "through `ros2 bag info` when ROS 2 is installed; `peek_bag_samples` cannot."
)
_MAX_ARRAY_ELEMENTS = 4096


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
            duration_seconds=reader_data["duration_seconds"],
            message_count=reader_data["message_count"],
            topics=reader_data["topics"],
            anomalies=[],
            mode_effective=mode_effective,  # type: ignore[arg-type]
            bag_format=bag_format,  # type: ignore[arg-type]
            samples_decoded_count=reader_data["samples_decoded_count"],
            recording_duration_ns=reader_data["recording_duration_ns"],
            participants_recorded=[],
        )

    def peek_samples(
        self,
        path: str,
        topic: str,
        count: int,
        *,
        mode_effective: str = "live",
    ) -> SampleResult:
        """Return up to `count` decoded samples for `topic` from `path`."""
        if not is_rosbags_available():
            raise AdapterError(_ROSBAGS_REQUIRED_MSG)
        if count < 0:
            raise AdapterError("count must be >= 0")

        resolved = Path(path)
        if not resolved.exists():
            raise AdapterError(f"bag path does not exist: {path!r}")

        clamped = min(count, MAX_SAMPLE_COUNT)
        try:
            samples, note = _peek_with_rosbags(resolved, topic, clamped)
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
        )


def read_topic_spans(path: Path) -> dict[str, TopicSpan] | None:
    """Per-topic spans of the bag at `path`, or `None` when it cannot be read here.

    `.db3` bags need only the standard library. Other containers need
    `rosbags` and a scan of their message timestamps. Never raises.
    """
    try:
        spans = read_db3_spans(path)
        if spans is not None:
            return spans
        if not is_rosbags_available() or not path.exists():
            return None
        reader, _ = _open_reader(path)
        with reader:
            return _spans_from_messages(reader)
    except Exception as exc:
        log.debug("could not read per-topic spans from %s: %s", path, exc)
        return None


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
    return any(
        getattr(getattr(p, "durability", None), "name", "") == "TRANSIENT_LOCAL" for p in profiles
    )


def _spans_from_messages(reader: Any) -> dict[str, TopicSpan]:
    """Per-topic spans from message timestamps, without deserializing (non-db3 bags)."""
    first: dict[str, int] = {}
    last: dict[str, int] = {}
    count: dict[str, int] = {}
    for connection, timestamp, _raw in reader.messages():
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

    Returns a dict with `duration_seconds`, `message_count`, `topics`,
    `samples_decoded_count` and `recording_duration_ns`. `.db3` spans come
    from `sqlite3`; other containers are scanned through `rosbags`.
    """
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
        "duration_seconds": duration_ns / 1_000_000_000 if duration_ns > 0 else 0.0,
        "message_count": sum(t.message_count for t in topics),
        "topics": topics,
        "samples_decoded_count": 0,  # analysis reads stats only; peek_samples decodes
        "recording_duration_ns": duration_ns if duration_ns > 0 else None,
    }


def _peek_with_rosbags(
    resolved: Path, topic: str, count: int
) -> tuple[list[MessageSample], str | None]:
    """Up to `count` decoded samples on `topic` and a note; `AdapterError` if the topic is absent."""
    samples: list[MessageSample] = []
    capped: set[str] = set()
    reader, typestore_desc = _open_reader(resolved)
    with reader:
        connections = [c for c in reader.connections if getattr(c, "topic", None) == topic]
        if not connections:
            raise AdapterError(
                f"topic {topic!r} not present in bag (known topics: "
                f"{[getattr(c, 'topic', '<?>') for c in reader.connections]!r})"
            )

        message_type = getattr(connections[0], "msgtype", "<unknown>")
        assumed = _lacks_embedded_definitions(reader)
        for connection, timestamp, raw in reader.messages(connections=connections):
            if len(samples) >= count:
                break
            payload = _decode_bag_message(reader, connection, raw)
            samples.append(
                MessageSample(
                    topic=topic,
                    message_type=message_type,
                    timestamp_ns=int(timestamp),
                    payload=_cap_arrays(payload, capped),
                )
            )

    notes: list[str] = []
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


def _decode_bag_message(reader: Any, connection: Any, raw: bytes) -> dict[str, Any]:
    """Deserialize one bag message and run it through `decode_dynamic_sample`."""
    try:
        deserialized = reader.deserialize(raw, connection.msgtype)
    except Exception as exc:  # pragma: no cover: binding-side error
        return annotate_raw(
            raw if isinstance(raw, (bytes, bytearray)) else b"",
            note=f"rosbags deserialize failed: {exc}",
        )

    from topicforge.adapters.common.cdr_decoder import decode_dynamic_sample

    decoded = decode_dynamic_sample(deserialized)
    if isinstance(decoded, dict):
        decoded.setdefault("_msgtype", getattr(connection, "msgtype", "<unknown>"))
    return decoded
