"""Inspector — the domain layer between MCP tool handlers and adapters.

Tools call the Inspector. The Inspector validates inputs, delegates to the
adapter, and ensures outputs are well-formed regardless of backend.
"""

from __future__ import annotations

import re
from pathlib import Path

from topicforge.adapters.base import AdapterError, AdapterName, MiddlewareAdapter
from topicforge.models import (
    BagAnalysis,
    MismatchReport,
    ParticipantEvent,
    ParticipantInfo,
    SampleResult,
    TopicInfo,
    TopicMetrics,
)
from topicforge.services.constants import MAX_SAMPLE_COUNT

DEFAULT_SAMPLE_COUNT = 5
DEFAULT_LOOKBACK_SECONDS = 300
DEFAULT_WINDOW_SECONDS = 60
_DDS_DOMAIN_MIN = 0
_DDS_DOMAIN_MAX = 232
_LOOKBACK_MIN = 1
_LOOKBACK_MAX = 86400
_WINDOW_MIN = 1
_WINDOW_MAX = 3600

# Re-export for backward-compatibility with v0.1.x code that imports
# `MAX_SAMPLE_COUNT` from `topicforge.services.inspector`. The canonical
# home is now `topicforge.services.constants` ; new code should import
# from there.
__all__ = ["DEFAULT_SAMPLE_COUNT", "MAX_SAMPLE_COUNT", "Inspector"]

# Strict allowlist mirroring ROS2 topic-name conventions:
#   * must start with `/`
#   * one or more segments separated by single `/`
#   * each segment starts with a letter or underscore, then [A-Za-z0-9_]*
# This rejects `//`, trailing `/`, digit-leading segments, dashes, dots, and
# every shell metacharacter before any value reaches the `ros2` CLI.
_TOPIC_NAME_RE = re.compile(r"^/[A-Za-z_][A-Za-z0-9_]*(?:/[A-Za-z_][A-Za-z0-9_]*)*$")

# Relaxed validator for DDS topic names. DDS-native conventions allow:
#   * topic names without a leading `/` (builtin DCPS topics: `DCPSParticipant`,
#     `DCPSSubscription`, `DCPSPublication`)
#   * `::` separators (C++-namespace style for typed user topics)
#   * leading letter, underscore, or `/`
# Still rejects whitespace, shell metacharacters, and other glaring oddities
# so the live adapter's downstream consumers can rely on a clean string.
# Resolves audit-2026-05-14 "Refactor opportunities" #5.
_DDS_TOPIC_NAME_RE = re.compile(r"^[A-Za-z_/][A-Za-z0-9_/:]*$")


class Inspector:
    """Validation and orchestration layer between MCP tool handlers and ROS adapters.

    All MCP-level input normalization happens here (topic name format, count clamping,
    path validation) so adapters can assume well-formed inputs. Today some methods are
    thin pass-throughs to the adapter; they remain in this layer to keep the contract
    surface symmetric — every tool goes through the same gate.
    """

    def __init__(self, adapter: MiddlewareAdapter) -> None:
        self._adapter = adapter

    @property
    def backend_name(self) -> AdapterName:
        return self._adapter.name

    def list_topics(self) -> list[TopicInfo]:
        # TODO(roadmap, audit-2026-05-14): validation symmetry — list_topics
        # is a pass-through with no input to validate, while peer methods
        # like get_topic_info validate. The "symmetric gate" docstring
        # justifies it today, but revisit if new tools land that take args
        # this method does not. See architecture audit "Refactor" #7.
        return self._adapter.list_topics()

    def get_topic_info(self, topic: str) -> TopicInfo:
        _validate_topic_name(topic)
        return self._adapter.get_topic_info(topic)

    def sample_messages(self, topic: str, count: int | None = None) -> SampleResult:
        _validate_topic_name(topic)
        n = DEFAULT_SAMPLE_COUNT if count is None else count
        if n < 0:
            raise AdapterError("count must be >= 0")
        samples = self._adapter.sample_messages(topic, min(n, MAX_SAMPLE_COUNT))
        return SampleResult(
            topic=topic,
            count=len(samples),
            samples=samples,
            mode_effective=self._adapter.effective_mode,
        )

    def analyze_bag(self, path: str) -> BagAnalysis:
        return self._adapter.analyze_bag(_validate_bag_path(path))

    # ---------------------------- DDS module ------------------------------

    def list_participants(self, domain_id: int = 0) -> list[ParticipantInfo]:
        _validate_dds_domain(domain_id)
        return self._adapter.list_participants(domain_id)

    def detect_qos_mismatches(self, topic: str | None = None) -> list[MismatchReport]:
        if topic is not None:
            _validate_topic_name_dds(topic)
        return self._adapter.detect_qos_mismatches(topic)

    def peek_dds_samples(self, topic: str, count: int | None = None) -> SampleResult:
        _validate_topic_name_dds(topic)
        n = DEFAULT_SAMPLE_COUNT if count is None else count
        if n < 0:
            raise AdapterError("count must be >= 0")
        return self._adapter.peek_dds_samples(topic, min(n, MAX_SAMPLE_COUNT))

    def participant_events(
        self, domain_id: int = 0, lookback_seconds: int | None = None
    ) -> list[ParticipantEvent]:
        _validate_dds_domain(domain_id)
        seconds = DEFAULT_LOOKBACK_SECONDS if lookback_seconds is None else lookback_seconds
        _validate_lookback_seconds(seconds)
        return self._adapter.participant_events(domain_id, seconds)

    def topic_metrics(
        self,
        topic: str,
        window_seconds: int | None = None,
        domain_id: int = 0,
    ) -> TopicMetrics:
        _validate_topic_name_dds(topic)
        _validate_dds_domain(domain_id)
        seconds = DEFAULT_WINDOW_SECONDS if window_seconds is None else window_seconds
        _validate_window_seconds(seconds)
        return self._adapter.topic_metrics(topic, seconds, domain_id)

    def peek_bag_samples(self, path: str, topic: str, count: int | None = None) -> SampleResult:
        clean_path = _validate_bag_path(path)
        _validate_topic_name_dds(topic)
        n = DEFAULT_SAMPLE_COUNT if count is None else count
        if n < 0:
            raise AdapterError("count must be >= 0")
        return self._adapter.peek_bag_samples(clean_path, topic, min(n, MAX_SAMPLE_COUNT))


def _validate_dds_domain(domain_id: int) -> None:
    if not isinstance(domain_id, int) or isinstance(domain_id, bool):
        raise AdapterError(f"domain_id must be an int, got {type(domain_id).__name__}")
    if domain_id < _DDS_DOMAIN_MIN or domain_id > _DDS_DOMAIN_MAX:
        raise AdapterError(
            f"domain_id must be in {_DDS_DOMAIN_MIN}..{_DDS_DOMAIN_MAX}, got {domain_id}"
        )


def _validate_lookback_seconds(seconds: int) -> None:
    if not isinstance(seconds, int) or isinstance(seconds, bool):
        raise AdapterError(f"lookback_seconds must be an int, got {type(seconds).__name__}")
    if seconds < _LOOKBACK_MIN or seconds > _LOOKBACK_MAX:
        raise AdapterError(
            f"lookback_seconds must be in {_LOOKBACK_MIN}..{_LOOKBACK_MAX}, got {seconds}"
        )


def _validate_window_seconds(seconds: int) -> None:
    if not isinstance(seconds, int) or isinstance(seconds, bool):
        raise AdapterError(f"window_seconds must be an int, got {type(seconds).__name__}")
    if seconds < _WINDOW_MIN or seconds > _WINDOW_MAX:
        raise AdapterError(f"window_seconds must be in {_WINDOW_MIN}..{_WINDOW_MAX}, got {seconds}")


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
    """Relaxed validator for DDS-native topic names.

    DDS topic names follow OMG conventions: they may or may not start with
    `/`, may contain `::` separators (C++-namespace style), and the builtin
    DCPS topics (`DCPSParticipant`, `DCPSSubscription`, `DCPSPublication`)
    have no leading `/` at all. Still rejects whitespace, shell
    metacharacters, and other oddities so the live adapter's downstream
    consumers can rely on a clean string.
    """
    if not topic or not topic.strip():
        raise AdapterError("topic must be a non-empty string")
    if not _DDS_TOPIC_NAME_RE.match(topic):
        raise AdapterError(
            f"DDS topic name is malformed (got {topic!r}); allowed "
            "characters are letters, digits, `_`, `:`, and `/`. "
            "Whitespace, shell metacharacters, and dashes are rejected."
        )


def _validate_bag_path(path: str) -> str:
    """Validate and normalize a bag path before it reaches an adapter.

    Returns the stripped path. Raises `AdapterError` for empty, blank,
    null-byte-containing, or otherwise malformed paths. Does NOT check
    existence or extension — that is the live adapter's responsibility.
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
