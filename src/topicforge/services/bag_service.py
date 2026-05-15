"""Bag analysis service — wraps the `rosbags` library.

v0.4.0 Phase 3 surface for analyzing recorded bags across the three
formats the OMG / ROS ecosystem actually ships :

* MCAP (Foxglove container, CDR-encoded payloads)
* ROS2 `.db3` (SQLite-backed rosbag2)
* ROS1 `.bag` (legacy binary chunked, non-CDR serialization)

`rosbags` (Apache 2.0, pure-Python) handles all three via a single
`AnyReader` API. We lazy-import it so the OSS core stays installable
without `rosbags` ; the factory and Ros2CliAdapter fall back to the
v0.3.0 text-parsed `ros2 bag info` behavior when the library is
absent. `peek_bag_samples` requires `rosbags` (no graceful fallback —
the tool description tells the LLM exactly what to ask the user).

The decoded sample shape mirrors `peek_dds_samples` — the CDR
decoder in `adapters/common/cdr_decoder.py` is the shared decode
core (Phase 3 sub-milestone 3.1).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from topicforge.adapters.base import AdapterError
from topicforge.adapters.common import (
    annotate_full,
    annotate_partial,
    annotate_raw,
)
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

_MAX_SAMPLE_COUNT = 50
_ROSBAGS_REQUIRED_MSG = (
    "Bag sample peek requires the `rosbags` library. Install via `pip install topicforge[bags]`."
)


def detect_bag_format(path: str) -> str:
    """Best-effort format classification from the path extension.

    Returns one of `"mcap"`, `"db3"`, `"bag"`, `"unknown"`. Pure
    string operation — does not touch the filesystem.
    """
    suffix = Path(path).suffix.lower()
    return _BAG_FORMAT_BY_EXTENSION.get(suffix, "unknown")


def is_rosbags_available() -> bool:
    """True iff the `rosbags` Python library is importable on this host."""
    import importlib.util

    try:
        return importlib.util.find_spec("rosbags") is not None
    except (ModuleNotFoundError, ValueError):
        return False


class BagService:
    """High-level facade over `rosbags` for the Inspector layer.

    Two public methods:

    * `analyze(path)` returns an enriched `BagAnalysis` with per-topic
      stats, format detection, decoded sample counts, recording
      duration. Falls through to a "rosbags-not-installed" error when
      the library is absent — callers (Ros2CliAdapter) should check
      `is_rosbags_available()` first and fall back to their v0.3.0
      text-parse path when False.
    * `peek_samples(path, topic, count)` returns up to `count` decoded
      samples for `topic` as a `SampleResult`. Same payload shape as
      `peek_dds_samples` (with `_decode_status` annotations).
    """

    def __init__(self) -> None:
        if not is_rosbags_available():
            # Constructor does NOT raise — we want callers to be able to
            # introspect the service without crashing. Methods raise
            # AdapterError when actually called.
            log.debug("BagService instantiated without `rosbags` ; methods will raise.")

    def analyze(self, path: str, *, mode_effective: str = "live") -> BagAnalysis:
        """Read `path` with rosbags and return an enriched BagAnalysis.

        Raises `AdapterError` when `rosbags` is not installed (caller
        should fall back). Raises `AdapterError` when the path does
        not exist or rosbags cannot open it.
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
        except Exception as exc:  # pragma: no cover — defensive
            raise AdapterError(f"failed to open bag {path!r}: {exc}") from exc

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

        clamped = min(count, _MAX_SAMPLE_COUNT)
        try:
            samples = _peek_with_rosbags(resolved, topic, clamped)
        except AdapterError:
            raise
        except Exception as exc:  # pragma: no cover — defensive
            raise AdapterError(f"failed to peek samples from {path!r}: {exc}") from exc

        return SampleResult(
            topic=topic,
            count=len(samples),
            samples=samples,
            mode_effective=mode_effective,  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# rosbags I/O — lazy-imported helpers
# ---------------------------------------------------------------------------


def _read_with_rosbags(resolved: Path) -> dict[str, Any]:
    """Open `resolved` with rosbags and compute per-topic stats.

    Returns a dict with keys `duration_seconds`, `message_count`,
    `topics` (list[BagTopicStats]), `samples_decoded_count`,
    `recording_duration_ns`.
    """
    # Lazy import — this function is only reached after
    # is_rosbags_available() returned True.
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
        "samples_decoded_count": 0,  # analyze() reads stats only ; peek_samples() decodes
        "recording_duration_ns": duration_ns if duration_ns > 0 else None,
    }


def _peek_with_rosbags(resolved: Path, topic: str, count: int) -> list[MessageSample]:
    """Iterate messages on `topic` via rosbags ; return up to `count` decoded samples."""
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
    """Best-effort decode of a single bag message via rosbags + cdr_decoder.

    rosbags returns the raw payload bytes ; we attempt to deserialize
    through the reader's typestore and then run the result through the
    shared `cdr_decoder.decode_dynamic_sample` for the same payload
    shape as live DDS samples.
    """
    try:
        deserialized = reader.deserialize(raw, connection.msgtype)
    except Exception as exc:  # pragma: no cover — binding-side error
        return annotate_raw(
            raw if isinstance(raw, (bytes, bytearray)) else b"",
            note=f"rosbags deserialize failed: {exc}",
        )

    # Run through the shared decoder for the same _decode_status shape
    # the live DDS path produces.
    from topicforge.adapters.common.cdr_decoder import decode_dynamic_sample

    decoded = decode_dynamic_sample(deserialized)
    # decode_dynamic_sample stamps _decode_status (full / partial / raw)
    # ; we additionally surface the rosbags-side message type for the LLM.
    if isinstance(decoded, dict):
        decoded.setdefault("_msgtype", getattr(connection, "msgtype", "<unknown>"))
    return decoded


# Unused imports kept for forward-compat (planned use in v0.4.0 Phase 3
# patches that surface participant metadata from MCAP channel records).
_ = (annotate_full, annotate_partial)
