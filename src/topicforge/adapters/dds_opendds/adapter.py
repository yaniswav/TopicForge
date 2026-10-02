"""OpenDDS adapter stub.

`pyopendds` is not maintained on PyPI. The stub implements the full
`MiddlewareAdapter` protocol; every method raises
`AdapterError(_OPENDDS_ROADMAP_MSG)`.

`is_available()` is always False: the factory selects backends on it, so
reporting True (even with an importable `pyopendds`) would make the factory
pick OpenDDS and fail every tool call. A real adapter would replace this
stub under the same module path and probe the binding instead.
"""

from __future__ import annotations

import logging

from topicforge.adapters.base import AdapterError, AdapterName, EffectiveMode
from topicforge.adapters.common import validate_domain_id
from topicforge.models import (
    BagAnalysis,
    EndpointListing,
    MessageSample,
    MismatchScan,
    ParticipantEvent,
    ParticipantInfo,
    SampleResult,
    TopicInfo,
    TopicMetrics,
)

log = logging.getLogger(__name__)

_OPENDDS_ROADMAP_MSG = (
    "OpenDDS adapter is a stub at TopicForge v0.4.0 Phase 1.5: the "
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
        # Always False: even with `pyopendds` importable this stub serves nothing,
        # and the factory selects on is_available().
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

    def detect_qos_mismatches(self, topic: str | None = None) -> MismatchScan:
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

    def list_endpoints(
        self,
        topic: str | None = None,
        participant_guid: str | None = None,
        include_observer: bool = False,
        include_departed: bool = False,
    ) -> EndpointListing:
        raise AdapterError(_OPENDDS_ROADMAP_MSG)

    def peek_bag_samples(self, path: str, topic: str, count: int) -> SampleResult:
        raise AdapterError(_OPENDDS_ROADMAP_MSG)
