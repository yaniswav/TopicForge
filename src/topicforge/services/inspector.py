"""Inspector — the domain layer between MCP tool handlers and adapters.

Tools call the Inspector. The Inspector validates inputs, delegates to the
adapter, and ensures outputs are well-formed regardless of backend.
"""

from __future__ import annotations

import re
from pathlib import Path

from topicforge.adapters.base import AdapterError, AdapterName, RosAdapter
from topicforge.models import BagAnalysis, SampleResult, TopicInfo

DEFAULT_SAMPLE_COUNT = 5
MAX_SAMPLE_COUNT = 50

# Strict allowlist mirroring ROS2 topic-name conventions:
#   * must start with `/`
#   * one or more segments separated by single `/`
#   * each segment starts with a letter or underscore, then [A-Za-z0-9_]*
# This rejects `//`, trailing `/`, digit-leading segments, dashes, dots, and
# every shell metacharacter before any value reaches the `ros2` CLI.
_TOPIC_NAME_RE = re.compile(r"^/[A-Za-z_][A-Za-z0-9_]*(?:/[A-Za-z_][A-Za-z0-9_]*)*$")


class Inspector:
    """Validation and orchestration layer between MCP tool handlers and ROS adapters.

    All MCP-level input normalization happens here (topic name format, count clamping,
    path validation) so adapters can assume well-formed inputs. Today some methods are
    thin pass-throughs to the adapter; they remain in this layer to keep the contract
    surface symmetric — every tool goes through the same gate.
    """

    def __init__(self, adapter: RosAdapter) -> None:
        self._adapter = adapter

    @property
    def backend_name(self) -> AdapterName:
        return self._adapter.name

    def list_topics(self) -> list[TopicInfo]:
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
