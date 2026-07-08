"""OpenDDS adapter — v0.4.0 Phase 1.5 stub implementation.

`pyopendds` is not currently maintained on PyPI. This stub implements
the full `MiddlewareAdapter` protocol so the auto-detect framework
treats OpenDDS uniformly with Cyclone / Fast / RTI ; the 8 protocol
methods raise `AdapterError(_OPENDDS_ROADMAP_MSG)` with a clear pointer
to the v0.5+ roadmap if a user reaches them.

`is_available()` always returns False while this is a stub (Audit S1):
the factory selects backends on `is_available()`, so reporting True —
even when a `pyopendds` module happens to be importable — would make the
factory pick OpenDDS and then every tool call would raise. The Dust stub
follows the same rule. When a real binding ships, the replacement adapter
sets this from an actual capability probe.

When `pyopendds` ships, the stub is replaced by a real adapter under
the same module path. No other code changes : the factory, the
auto-detect chain, the health report, and the tool surface all keep
working as-is.

Shape mirrors the historical v0.2.0 `CycloneDdsAdapter` stub.
"""

from __future__ import annotations

import logging

from topicforge.adapters.base import AdapterError, AdapterName, EffectiveMode
from topicforge.adapters.common import validate_domain_id
from topicforge.models import (
    BagAnalysis,
    MessageSample,
    MismatchReport,
    ParticipantEvent,
    ParticipantInfo,
    SampleResult,
    TopicInfo,
    TopicMetrics,
)

log = logging.getLogger(__name__)

_OPENDDS_ROADMAP_MSG = (
    "OpenDDS adapter is a stub at TopicForge v0.4.0 Phase 1.5 — the "
    "`pyopendds` Python binding is not yet maintained on PyPI. Track "
    "OpenDDS Python binding progress upstream or contribute to the "
    "TopicForge OpenDDS adapter under `src/topicforge/adapters/dds_opendds/`. "
    "Until then, the auto-detect chain falls through to Fast / Cyclone "
    "/ Mock per the priority order in `config/settings.py`. See "
    "`docs/projet-file/mcp-02-spec.md` for the multi-vendor roadmap."
)


class OpenDdsAdapter:
    """Stub adapter that surfaces a clean error path for OpenDDS users."""

    name: AdapterName = "opendds"

    def __init__(self, domain_id: int = 0) -> None:
        validate_domain_id(domain_id)
        self._domain_id = domain_id

    @property
    def effective_mode(self) -> EffectiveMode:
        return "live"

    def is_available(self) -> bool:
        # Always False while this is a stub — even if a `pyopendds` module is
        # importable, this adapter cannot serve any request, and the factory
        # selects on is_available(). (Audit S1.)
        return False

    # ----- ROS2 surface: not served by this adapter -----

    def list_topics(self) -> list[TopicInfo]:
        raise AdapterError(_OPENDDS_ROADMAP_MSG)

    def get_topic_info(self, topic: str) -> TopicInfo:
        raise AdapterError(_OPENDDS_ROADMAP_MSG)

    def sample_messages(self, topic: str, count: int) -> list[MessageSample]:
        raise AdapterError(_OPENDDS_ROADMAP_MSG)

    def analyze_bag(self, path: str) -> BagAnalysis:
        raise AdapterError(_OPENDDS_ROADMAP_MSG)

    # ----- DDS surface: stub raises with the roadmap message -----

    def list_participants(self, domain_id: int = 0) -> list[ParticipantInfo]:
        raise AdapterError(_OPENDDS_ROADMAP_MSG)

    def detect_qos_mismatches(self, topic: str | None = None) -> list[MismatchReport]:
        raise AdapterError(_OPENDDS_ROADMAP_MSG)

    def peek_dds_samples(self, topic: str, count: int) -> SampleResult:
        raise AdapterError(_OPENDDS_ROADMAP_MSG)

    def participant_events(
        self, domain_id: int = 0, lookback_seconds: int = 300
    ) -> list[ParticipantEvent]:
        raise AdapterError(_OPENDDS_ROADMAP_MSG)

    def topic_metrics(
        self, topic: str, window_seconds: int = 60, domain_id: int = 0
    ) -> TopicMetrics:
        raise AdapterError(_OPENDDS_ROADMAP_MSG)

    def peek_bag_samples(self, path: str, topic: str, count: int) -> SampleResult:
        raise AdapterError(_OPENDDS_ROADMAP_MSG)
