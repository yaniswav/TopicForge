"""Bag analysis service over the `rosbags` library.

`rosbags` (pure Python) reads MCAP, ROS2 `.db3` and ROS1 `.bag` through one
`AnyReader` API. It is imported lazily so the core installs without it.
`peek_bag_samples` requires it; `analyze_bag` can fall back to the text
parsing of `ros2 bag info`.

Decoded samples share the payload shape of `peek_dds_samples`, through
`adapters/common/cdr_decoder.py`.
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

log = logging.getLogger(__name__)

_BAG_FORMAT_BY_EXTENSION: dict[str, str] = {
    ".mcap": "mcap",
    ".db3": "db3",
    ".bag": "bag",
}
_ROSBAGS_REQUIRED_MSG = (
    "Bag analysis with full sample decode requires the `rosbags` library. "
    "Install via `pip install topicforge[bags]` and retry. "
    "`analyze_bag` may still fall back to the v0.3.0 `ros2 bag info` text-parse "
    "path via the live ROS2 CLI adapter ; `peek_bag_samples` has no fallback."
)


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
            samples = _peek_with_rosbags(resolved, topic, clamped)
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
        )


def _read_with_rosbags(resolved: Path) -> dict[str, Any]:
    """Open `resolved` with `rosbags` and compute per-topic stats.

    Returns a dict with `duration_seconds`, `message_count`, `topics`,
    `samples_decoded_count` and `recording_duration_ns`.
    """
    from rosbags.highlevel import AnyReader  # type: ignore[import-not-found]

    with AnyReader([resolved]) as reader:
        start = getattr(reader, "start_time", None)
        end = getattr(reader, "end_time", None)
        if isinstance(start, int) and isinstance(end, int) and end >= start:
            duration_ns = end - start
        else:
            duration_ns = 0

        topics: list[BagTopicStats] = []
        total_messages = 0
        for connection in reader.connections:
            count = getattr(connection, "msgcount", 0) or 0
            total_messages += count
            duration_s = duration_ns / 1_000_000_000 if duration_ns > 0 else 0.0
            frequency = (count / duration_s) if duration_s > 0 and count > 0 else None
            topics.append(
                BagTopicStats(
                    name=getattr(connection, "topic", "<unknown>"),
                    message_type=getattr(connection, "msgtype", "<unknown>"),
                    message_count=count,
                    frequency_hz=frequency,
                )
            )

    return {
        "duration_seconds": duration_ns / 1_000_000_000 if duration_ns > 0 else 0.0,
        "message_count": total_messages,
        "topics": topics,
        "samples_decoded_count": 0,  # analysis reads stats only; peek_samples decodes
        "recording_duration_ns": duration_ns if duration_ns > 0 else None,
    }


def _peek_with_rosbags(resolved: Path, topic: str, count: int) -> list[MessageSample]:
    """Up to `count` decoded samples on `topic`; `AdapterError` if the bag has no such topic."""
    from rosbags.highlevel import AnyReader  # type: ignore[import-not-found]

    samples: list[MessageSample] = []
    with AnyReader([resolved]) as reader:
        connections = [c for c in reader.connections if getattr(c, "topic", None) == topic]
        if not connections:
            raise AdapterError(
                f"topic {topic!r} not present in bag (known topics: "
                f"{[getattr(c, 'topic', '<?>') for c in reader.connections]!r})"
            )

        message_type = getattr(connections[0], "msgtype", "<unknown>")
        for connection, timestamp, raw in reader.messages(connections=connections):
            if len(samples) >= count:
                break
            payload = _decode_bag_message(reader, connection, raw)
            samples.append(
                MessageSample(
                    topic=topic,
                    message_type=message_type,
                    timestamp_ns=int(timestamp),
                    payload=payload,
                )
            )
    return samples


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
