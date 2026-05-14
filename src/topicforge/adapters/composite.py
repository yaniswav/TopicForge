"""Composite adapter — routes ROS2 graph calls to one adapter, DDS calls to another.

v0.4.0 Phase 1 introduces a runtime where `TOPICFORGE_MODE=live` and
`TOPICFORGE_DDS_BACKEND=cyclone|fast` are **orthogonal** rather than
mutually exclusive. The factory composes a `Ros2CliAdapter` and a DDS
adapter behind this wrapper so a single process can serve all 8
`MiddlewareAdapter` tools simultaneously.

The wrapper itself implements `MiddlewareAdapter` and dispatches per
method category:

  * `list_topics`, `get_topic_info`, `sample_messages`, `analyze_bag`
    → `self._ros`
  * `list_participants`, `detect_qos_mismatches`, `peek_dds_samples`
    → `self._dds`

`AdapterError` from either side propagates unchanged. The composite
never swallows or remaps errors — that would defeat the underlying
adapter's diagnostic messages.
"""

from __future__ import annotations

from topicforge.adapters.base import AdapterName, EffectiveMode, MiddlewareAdapter
from topicforge.models import (
    BagAnalysis,
    MessageSample,
    MismatchReport,
    ParticipantInfo,
    SampleResult,
    TopicInfo,
)


class CompositeAdapter:
    """Routes ROS2 protocol methods to `ros`, DDS protocol methods to `dds`.

    Both underlying adapters must satisfy the `MiddlewareAdapter` protocol.
    The composite reports a hyphenated `name` (e.g. `"ros2_cli+cyclone"`)
    and reduces `effective_mode` to `"live"` when either side is live
    (the live half dominates the wire contract surfaced to MCP clients).
    """

    def __init__(self, ros: MiddlewareAdapter, dds: MiddlewareAdapter) -> None:
        self._ros = ros
        self._dds = dds

    @property
    def name(self) -> AdapterName:  # type: ignore[override]
        combined = f"{self._ros.name}+{self._dds.name}"
        return combined  # type: ignore[return-value]

    @property
    def effective_mode(self) -> EffectiveMode:
        if self._ros.effective_mode == "live" or self._dds.effective_mode == "live":
            return "live"
        return "mock"

    def is_available(self) -> bool:
        return self._ros.is_available() and self._dds.is_available()

    # ----- ROS2 graph surface → ROS adapter -----

    def list_topics(self) -> list[TopicInfo]:
        return self._ros.list_topics()

    def get_topic_info(self, topic: str) -> TopicInfo:
        return self._ros.get_topic_info(topic)

    def sample_messages(self, topic: str, count: int) -> list[MessageSample]:
        return self._ros.sample_messages(topic, count)

    def analyze_bag(self, path: str) -> BagAnalysis:
        return self._ros.analyze_bag(path)

    # ----- DDS surface → DDS adapter -----

    def list_participants(self, domain_id: int = 0) -> list[ParticipantInfo]:
        return self._dds.list_participants(domain_id)

    def detect_qos_mismatches(self, topic: str | None = None) -> list[MismatchReport]:
        return self._dds.detect_qos_mismatches(topic)

    def peek_dds_samples(self, topic: str, count: int) -> SampleResult:
        return self._dds.peek_dds_samples(topic, count)
