"""Inspector: validates tool inputs and delegates to the adapter."""

from __future__ import annotations

import re
from pathlib import Path

from topicforge.adapters.base import AdapterError, AdapterName, MiddlewareAdapter
from topicforge.constants import (
    DEFAULT_MAX_ARRAY_LENGTH,
    DEFAULT_MAX_SAMPLE_BYTES,
    DEFAULT_SAMPLE_TIMEOUT_S,
    MAX_ARRAY_LENGTH,
    MAX_SAMPLE_COUNT,
    MAX_SAMPLE_TIMEOUT_S,
    MIN_SAMPLE_TIMEOUT_S,
    TRUNCATED_FIELDS_KEY,
)
from topicforge.models import (
    BagAnalysis,
    EndpointListing,
    MismatchScan,
    ParticipantEvent,
    ParticipantInfo,
    SampleResult,
    TopicInfo,
    TopicMetrics,
)
from topicforge.services.sample_budget import apply_sample_budget

DEFAULT_SAMPLE_COUNT = 5
DEFAULT_LOOKBACK_SECONDS = 300
DEFAULT_WINDOW_SECONDS = 60
_DDS_DOMAIN_MIN = 0
_DDS_DOMAIN_MAX = 232
_LOOKBACK_MIN = 1
_LOOKBACK_MAX = 86400
_WINDOW_MIN = 1
_WINDOW_MAX = 3600

__all__ = ["DEFAULT_SAMPLE_COUNT", "Inspector"]

# ROS2 topic names: a leading `/`, then segments separated by single `/`, each
# starting with a letter or underscore. Rejects `//`, a trailing `/`, dashes,
# dots and shell metacharacters before a value reaches the `ros2` CLI.
_TOPIC_NAME_RE = re.compile(r"^/[A-Za-z_][A-Za-z0-9_]*(?:/[A-Za-z_][A-Za-z0-9_]*)*$")

# DDS topic names may omit the leading `/` (the builtin `DCPS*` topics) and may
# use `::` separators. Whitespace and shell metacharacters are still rejected.
_DDS_TOPIC_NAME_RE = re.compile(r"^[A-Za-z_/][A-Za-z0-9_/:]*$")


class Inspector:
    """Validation layer between tool handlers and adapters.

    Topic name format, count clamping and path checks happen here, so
    adapters can assume well-formed input. Every tool goes through it, even
    when it only passes the call on.
    """

    def __init__(
        self,
        adapter: MiddlewareAdapter,
        *,
        max_sample_bytes: int = DEFAULT_MAX_SAMPLE_BYTES,
    ) -> None:
        self._adapter = adapter
        self._max_sample_bytes = max_sample_bytes

    @property
    def backend_name(self) -> AdapterName:
        return self._adapter.name

    def list_topics(self) -> list[TopicInfo]:
        # No arguments, so nothing to validate here.
        return self._adapter.list_topics()

    def get_topic_info(self, topic: str) -> TopicInfo:
        _validate_topic_name(topic)
        return self._adapter.get_topic_info(topic)

    def sample_messages(
        self,
        topic: str,
        count: int | None = None,
        *,
        max_array_length: int | None = DEFAULT_MAX_ARRAY_LENGTH,
        arrays_summary_only: bool = False,
        timeout_s: float = DEFAULT_SAMPLE_TIMEOUT_S,
    ) -> SampleResult:
        _validate_topic_name(topic)
        n = DEFAULT_SAMPLE_COUNT if count is None else count
        if n < 0:
            raise AdapterError("count must be >= 0")
        _validate_max_array_length(max_array_length)
        _validate_timeout_s(timeout_s)
        capped = min(n, MAX_SAMPLE_COUNT)
        result = self._adapter.sample_messages(
            topic,
            capped,
            max_array_length=max_array_length,
            arrays_summary_only=arrays_summary_only,
            timeout_s=timeout_s,
        )
        samples, notes = apply_sample_budget(result.samples, self._max_sample_bytes)
        if n > capped:
            notes.insert(0, f"count capped to {capped} (requested {n}).")
        if result.note:
            notes.insert(0, result.note)
        if any(TRUNCATED_FIELDS_KEY in s.payload for s in samples):
            notes.append(
                f"Arrays, strings or bytes longer than {max_array_length} elements were cut; "
                f"the cut fields are listed in `{TRUNCATED_FIELDS_KEY}`. Pass "
                "`max_array_length` null for full values, or `arrays_summary_only` true to "
                "drop array contents."
            )
        return result.model_copy(
            update={"samples": samples, "count": len(samples), "note": " ".join(notes) or None}
        )

    def analyze_bag(self, path: str) -> BagAnalysis:
        return self._adapter.analyze_bag(_validate_bag_path(path))

    def _await_dds(self) -> None:
        """Wait, bounded, for a newly started discovery tracker to hear the bus; usually a no-op."""
        wait = getattr(self._adapter, "await_discovery_ready", None)
        if callable(wait):
            wait()

    def list_participants(self, domain_id: int = 0) -> list[ParticipantInfo]:
        _validate_dds_domain(domain_id)
        self._await_dds()
        return self._adapter.list_participants(domain_id)

    def detect_qos_mismatches(self, topic: str | None = None) -> MismatchScan:
        if topic is not None:
            _validate_topic_name_dds(topic)
        self._await_dds()
        return self._adapter.detect_qos_mismatches(topic)

    def peek_dds_samples(self, topic: str, count: int | None = None) -> SampleResult:
        _validate_topic_name_dds(topic)
        n = DEFAULT_SAMPLE_COUNT if count is None else count
        if n < 0:
            raise AdapterError("count must be >= 0")
        self._await_dds()
        return self._adapter.peek_dds_samples(topic, min(n, MAX_SAMPLE_COUNT))

    def participant_events(
        self, domain_id: int = 0, lookback_s: int | None = None
    ) -> list[ParticipantEvent]:
        _validate_dds_domain(domain_id)
        seconds = DEFAULT_LOOKBACK_SECONDS if lookback_s is None else lookback_s
        _validate_lookback_s(seconds)
        self._await_dds()
        return self._adapter.participant_events(domain_id, seconds)

    def topic_metrics(
        self,
        topic: str,
        window_s: int | None = None,
        domain_id: int = 0,
    ) -> TopicMetrics:
        _validate_topic_name_dds(topic)
        _validate_dds_domain(domain_id)
        seconds = DEFAULT_WINDOW_SECONDS if window_s is None else window_s
        _validate_window_s(seconds)
        self._await_dds()
        return self._adapter.topic_metrics(topic, seconds, domain_id)

    def list_endpoints(
        self,
        topic: str | None = None,
        participant_guid: str | None = None,
        include_observer: bool = False,
        domain_id: int = 0,
        include_departed: bool = False,
    ) -> EndpointListing:
        _validate_dds_domain(domain_id)
        if topic is not None:
            _validate_topic_name_dds(topic)
        guid = participant_guid.strip() if participant_guid else None
        if participant_guid is not None and not guid:
            raise AdapterError("participant_guid must be a non-empty string when given")
        self._await_dds()
        return self._adapter.list_endpoints(topic, guid, include_observer, include_departed)

    def peek_bag_samples(self, path: str, topic: str, count: int | None = None) -> SampleResult:
        clean_path = _validate_bag_path(path)
        _validate_topic_name_dds(topic)
        n = DEFAULT_SAMPLE_COUNT if count is None else count
        if n < 0:
            raise AdapterError("count must be >= 0")
        result = self._adapter.peek_bag_samples(clean_path, topic, min(n, MAX_SAMPLE_COUNT))
        samples, notes = apply_sample_budget(result.samples, self._max_sample_bytes)
        if not notes:
            return result
        note = " ".join([n for n in [result.note, *notes] if n])
        return result.model_copy(update={"samples": samples, "count": len(samples), "note": note})


def _validate_timeout_s(value: float) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise AdapterError(f"timeout_s must be a number, got {type(value).__name__}")
    if not MIN_SAMPLE_TIMEOUT_S <= value <= MAX_SAMPLE_TIMEOUT_S:
        raise AdapterError(
            f"timeout_s must be in {MIN_SAMPLE_TIMEOUT_S:g}..{MAX_SAMPLE_TIMEOUT_S:g}, got {value}"
        )


def _validate_max_array_length(value: int | None) -> None:
    if value is None:
        return
    if not isinstance(value, int) or isinstance(value, bool):
        raise AdapterError(f"max_array_length must be an int or null, got {type(value).__name__}")
    if value < 1 or value > MAX_ARRAY_LENGTH:
        raise AdapterError(f"max_array_length must be in 1..{MAX_ARRAY_LENGTH}, got {value}")


def _validate_dds_domain(domain_id: int) -> None:
    if not isinstance(domain_id, int) or isinstance(domain_id, bool):
        raise AdapterError(f"domain_id must be an int, got {type(domain_id).__name__}")
    if domain_id < _DDS_DOMAIN_MIN or domain_id > _DDS_DOMAIN_MAX:
        raise AdapterError(
            f"domain_id must be in {_DDS_DOMAIN_MIN}..{_DDS_DOMAIN_MAX}, got {domain_id}"
        )


def _validate_lookback_s(seconds: int) -> None:
    if not isinstance(seconds, int) or isinstance(seconds, bool):
        raise AdapterError(f"lookback_s must be an int, got {type(seconds).__name__}")
    if seconds < _LOOKBACK_MIN or seconds > _LOOKBACK_MAX:
        raise AdapterError(
            f"lookback_s must be in {_LOOKBACK_MIN}..{_LOOKBACK_MAX}, got {seconds}"
        )


def _validate_window_s(seconds: int) -> None:
    if not isinstance(seconds, int) or isinstance(seconds, bool):
        raise AdapterError(f"window_s must be an int, got {type(seconds).__name__}")
    if seconds < _WINDOW_MIN or seconds > _WINDOW_MAX:
        raise AdapterError(f"window_s must be in {_WINDOW_MIN}..{_WINDOW_MAX}, got {seconds}")


def _validate_topic_name(topic: str) -> None:
    if not topic or not topic.strip():
        raise AdapterError("topic must be a non-empty string")
    if not topic.startswith("/"):
        raise AdapterError(f"topic must start with '/' (got {topic!r})")
    if not _TOPIC_NAME_RE.match(topic):
        raise AdapterError(
            f"topic name is malformed (got {topic!r}); each `/`-separated "
            "segment must start with a letter or underscore and contain only "
            "letters, digits, and underscores (no `//`, no trailing `/`)"
        )


def _validate_topic_name_dds(topic: str) -> None:
    """Validate a DDS topic name (see `_DDS_TOPIC_NAME_RE`)."""
    if not topic or not topic.strip():
        raise AdapterError("topic must be a non-empty string")
    if not _DDS_TOPIC_NAME_RE.match(topic):
        raise AdapterError(
            f"DDS topic name is malformed (got {topic!r}); allowed "
            "characters are letters, digits, `_`, `:`, and `/`. "
            "Whitespace, shell metacharacters, and dashes are rejected."
        )


def _validate_bag_path(path: str) -> str:
    """Return the stripped bag path, or raise `AdapterError` if it is empty, blank or malformed.

    Existence and extension are checked by the adapter.
    """
    if not isinstance(path, str):
        raise AdapterError("path must be a string")
    if not path or not path.strip():
        raise AdapterError("path must be a non-empty string")
    clean = path.strip()
    if "\x00" in clean:
        raise AdapterError("path must not contain null bytes")
    try:
        Path(clean)
    except (ValueError, OSError) as exc:
        raise AdapterError(f"path is not a valid filesystem path: {exc}") from exc
    return clean
