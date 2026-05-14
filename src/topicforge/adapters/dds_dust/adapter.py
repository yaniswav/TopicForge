"""Dust DDS adapter — v0.4.0 Phase 1.5 stub implementation.

Dust DDS is the Rust-native conformant DDS-RTPS stack from the OMG
May 2025 interop matrix (`docs/dds-interop-matrix.md`). It does not
ship a maintained Python binding on PyPI as of 2026-05-14, so this
stub exists purely to give the auto-detect chain a fourth OSS slot
to land in. `is_available()` always returns False ; every protocol
method raises `AdapterError(_DUST_ROADMAP_MSG)`.

When a `dust-dds-python` (or equivalent) package ships, the stub is
replaced by a real adapter under the same module path. Shape mirrors
the historical v0.2.0 `CycloneDdsAdapter` stub and the v0.4.0 Phase
1.5 OpenDDS stub.
"""

from __future__ import annotations

import logging

from topicforge.adapters.base import AdapterError, AdapterName, EffectiveMode
from topicforge.models import (
    BagAnalysis,
    MessageSample,
    MismatchReport,
    ParticipantEvent,
    ParticipantInfo,
    SampleResult,
    TopicInfo,
)

log = logging.getLogger(__name__)

_DUST_ROADMAP_MSG = (
    "Dust DDS adapter is a stub at TopicForge v0.4.0 Phase 1.5 — Dust "
    "DDS is a Rust-native implementation with no maintained Python "
    "binding on PyPI. The auto-detect chain treats it as the lowest "
    "OSS priority ; the factory falls back to Fast / Cyclone / Mock "
    "before reaching this adapter in any realistic install. See "
    "`docs/dds-interop-matrix.md` for the OMG positioning."
)


class DustDdsAdapter:
    """Stub adapter — always unavailable in v0.4.0 Phase 1.5."""

    name: AdapterName = "dust"

    def __init__(self, domain_id: int = 0) -> None:
        if domain_id < 0 or domain_id > 232:
            raise AdapterError(f"domain_id must be in 0..232, got {domain_id}")
        self._domain_id = domain_id

    @property
    def effective_mode(self) -> EffectiveMode:
        return "live"

    def is_available(self) -> bool:
        return False  # Always — no Python binding maintained.

    # ----- ROS2 surface: not served by this adapter -----

    def list_topics(self) -> list[TopicInfo]:
        raise AdapterError(_DUST_ROADMAP_MSG)

    def get_topic_info(self, topic: str) -> TopicInfo:
        raise AdapterError(_DUST_ROADMAP_MSG)

    def sample_messages(self, topic: str, count: int) -> list[MessageSample]:
        raise AdapterError(_DUST_ROADMAP_MSG)

    def analyze_bag(self, path: str) -> BagAnalysis:
        raise AdapterError(_DUST_ROADMAP_MSG)

    # ----- DDS surface: stub raises with the roadmap message -----

    def list_participants(self, domain_id: int = 0) -> list[ParticipantInfo]:
        raise AdapterError(_DUST_ROADMAP_MSG)

    def detect_qos_mismatches(self, topic: str | None = None) -> list[MismatchReport]:
        raise AdapterError(_DUST_ROADMAP_MSG)

    def peek_dds_samples(self, topic: str, count: int) -> SampleResult:
        raise AdapterError(_DUST_ROADMAP_MSG)

    def participant_events(
        self, domain_id: int = 0, lookback_seconds: int = 300
    ) -> list[ParticipantEvent]:
        raise AdapterError(_DUST_ROADMAP_MSG)
