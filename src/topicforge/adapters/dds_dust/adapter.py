"""Dust DDS adapter stub.

Dust DDS is a Rust-native, RTPS-conformant stack (`docs/dds-interop-matrix.md`)
with no maintained Python binding on PyPI. `is_available()` is always False
and every protocol method raises `AdapterError(_DUST_ROADMAP_MSG)`. A real
adapter would replace this stub under the same module path.
"""

from __future__ import annotations

import logging

from topicforge.adapters.base import AdapterError, AdapterName, EffectiveMode
from topicforge.adapters.common import validate_domain_id
from topicforge.constants import DEFAULT_MAX_ARRAY_LENGTH, DEFAULT_SAMPLE_TIMEOUT_S
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

log = logging.getLogger(__name__)

_DUST_ROADMAP_MSG = (
    "The Dust DDS backend is not implemented: Dust has no Python binding "
    "TopicForge can use. Dust participants are still visible: run TopicForge "
    "with TOPICFORGE_DDS_BACKEND=cyclone and it observes them through standard "
    "DDS discovery. See `docs/dds-interop-matrix.md`."
)


class DustDdsAdapter:
    """Stub adapter: always unavailable."""

    name: AdapterName = "dust"

    def __init__(self, domain_id: int = 0) -> None:
        validate_domain_id(domain_id)
        self._domain_id = domain_id

    @property
    def effective_mode(self) -> EffectiveMode:
        return "live"

    def is_available(self) -> bool:
        return False

    # ----- ROS2 surface: not served by this adapter -----

    def list_topics(self) -> list[TopicInfo]:
        raise AdapterError(_DUST_ROADMAP_MSG)

    def get_topic_info(self, topic: str) -> TopicInfo:
        raise AdapterError(_DUST_ROADMAP_MSG)

    def sample_messages(
        self,
        topic: str,
        count: int,
        *,
        max_array_length: int | None = DEFAULT_MAX_ARRAY_LENGTH,
        arrays_summary_only: bool = False,
        timeout_s: float = DEFAULT_SAMPLE_TIMEOUT_S,
    ) -> SampleResult:
        raise AdapterError(_DUST_ROADMAP_MSG)

    def analyze_bag(self, path: str) -> BagAnalysis:
        raise AdapterError(_DUST_ROADMAP_MSG)

    # ----- DDS surface: stub raises with the roadmap message -----

    def list_participants(self, domain_id: int = 0) -> list[ParticipantInfo]:
        raise AdapterError(_DUST_ROADMAP_MSG)

    def detect_qos_mismatches(self, topic: str | None = None) -> MismatchScan:
        raise AdapterError(_DUST_ROADMAP_MSG)

    def peek_dds_samples(self, topic: str, count: int) -> SampleResult:
        raise AdapterError(_DUST_ROADMAP_MSG)

    def participant_events(
        self, domain_id: int = 0, lookback_seconds: int = 300
    ) -> list[ParticipantEvent]:
        raise AdapterError(_DUST_ROADMAP_MSG)

    def topic_metrics(
        self, topic: str, window_seconds: int = 60, domain_id: int = 0
    ) -> TopicMetrics:
        raise AdapterError(_DUST_ROADMAP_MSG)

    def list_endpoints(
        self,
        topic: str | None = None,
        participant_guid: str | None = None,
        include_observer: bool = False,
        include_departed: bool = False,
    ) -> EndpointListing:
        raise AdapterError(_DUST_ROADMAP_MSG)

    def peek_bag_samples(self, path: str, topic: str, count: int) -> SampleResult:
        raise AdapterError(_DUST_ROADMAP_MSG)
