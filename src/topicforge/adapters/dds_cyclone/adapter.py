"""Cyclone DDS adapter.

Top-level `import cyclonedds` — this module is loaded conditionally by
`services.factory` only when `TOPICFORGE_DDS_BACKEND` resolves to
`cyclone` (or `auto` with cyclonedds installable). Mock-only and
ROS2-only installs never pay the import cost.

**v0.2.0 stub.** Ships the protocol-compliant adapter, lazy import
wiring, and clear `is_available()` semantics. The three DDS methods
(`list_participants`, `detect_qos_mismatches`, `peek_dds_samples`)
raise `AdapterError` with a v0.2.x roadmap pointer — the real
CycloneDDS integration (builtin-topic discovery, QoS pair extraction,
typed reader for samples) is scheduled for a v0.2.x patch. The mock
backend (`TOPICFORGE_DDS_BACKEND=mock`) exposes a working surface for
testing tool integration in the meantime.

This stub approach is deliberate: shipping untested DDS code against a
real broker on first release would be more dangerous than shipping a
clearly-flagged limitation. Once the v0.2.x patch lands with full
discovery + QoS analysis, the same protocol surface is preserved.
"""

from __future__ import annotations

import logging

# Top-level import — the factory only loads this module when cyclonedds
# is importable. An ImportError here propagates to the factory, which
# falls back to mock with a logged warning.
import cyclonedds  # noqa: F401 — kept for is_available() proof and future use

from topicforge.adapters.base import AdapterError, AdapterName, EffectiveMode
from topicforge.models import (
    BagAnalysis,
    MessageSample,
    MismatchReport,
    ParticipantInfo,
    SampleResult,
    TopicInfo,
)

log = logging.getLogger(__name__)

_NOT_IMPLEMENTED_MSG = (
    "CycloneDdsAdapter is a v0.2.0 stub: lazy import and protocol wiring "
    "are in place, but the real CycloneDDS discovery (builtin topics) and "
    "QoS pair extraction land in a v0.2.x patch. Use "
    "`TOPICFORGE_DDS_BACKEND=mock` to exercise the DDS tool surface "
    "against deterministic fixtures in the meantime."
)

_DDS_ONLY_ERROR_MSG = (
    "CycloneDdsAdapter does not serve ROS2 graph or bag introspection. "
    "Use the ROS2 CLI adapter (TOPICFORGE_MODE=live) for ROS2 tools, "
    "or the mock adapter (TOPICFORGE_MODE=mock) for demos."
)


class CycloneDdsAdapter:
    """Read-only adapter backed by CycloneDDS Python bindings."""

    name: AdapterName = "cyclone"

    def __init__(self, domain_id: int = 0) -> None:
        if domain_id < 0 or domain_id > 232:
            raise AdapterError(f"domain_id must be in 0..232, got {domain_id}")
        self._domain_id = domain_id

    @property
    def effective_mode(self) -> EffectiveMode:
        return "live"

    def is_available(self) -> bool:
        # Reaching __init__ means the top-level cyclonedds import succeeded.
        return True

    # ----- ROS2 surface: not served by this adapter -----

    def list_topics(self) -> list[TopicInfo]:
        raise AdapterError(_DDS_ONLY_ERROR_MSG)

    def get_topic_info(self, topic: str) -> TopicInfo:
        raise AdapterError(_DDS_ONLY_ERROR_MSG)

    def sample_messages(self, topic: str, count: int) -> list[MessageSample]:
        raise AdapterError(_DDS_ONLY_ERROR_MSG)

    def analyze_bag(self, path: str) -> BagAnalysis:
        raise AdapterError(_DDS_ONLY_ERROR_MSG)

    # ----- DDS surface: v0.2.0 stub -----

    def list_participants(self, domain_id: int = 0) -> list[ParticipantInfo]:
        # TODO(roadmap): v0.2.x — wire BuiltinTopicDcpsParticipant reader
        # on a DomainParticipant bound to `domain_id`, take samples,
        # return ParticipantInfo list. See docs/projet-file/mcp-02-spec.md.
        raise AdapterError(_NOT_IMPLEMENTED_MSG)

    def detect_qos_mismatches(self, topic: str | None = None) -> list[MismatchReport]:
        # TODO(roadmap): v0.2.x — read BuiltinTopicDcpsSubscription and
        # BuiltinTopicDcpsPublication, match (reader, writer) pairs on
        # `topic_name`, call `adapters.common.detect_mismatches` on each
        # pair, build MismatchReport list.
        raise AdapterError(_NOT_IMPLEMENTED_MSG)

    def peek_dds_samples(self, topic: str, count: int) -> SampleResult:
        # TODO(roadmap): v0.2.x — typed sample reading requires IDL
        # discovery for arbitrary user topics. Builtin-topic peek is
        # feasible sooner ; arbitrary user-topic peek follows.
        raise AdapterError(_NOT_IMPLEMENTED_MSG)
