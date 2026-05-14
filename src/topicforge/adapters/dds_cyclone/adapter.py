"""Cyclone DDS adapter — real implementation (v0.3.0).

Replaces the v0.2.0 stub with actual CycloneDDS discovery via the
`cyclonedds.builtin` builtin data readers. Joins the bus as a read-only
DDS-RTPS participant on the configured domain and observes every
conformant vendor on the wire — see `docs/dds-interop-matrix.md` for
the canonical multi-vendor positioning.

The 3 DDS methods (`list_participants`, `detect_qos_mismatches`,
`peek_dds_samples`) call into the CycloneDDS Python bindings ; the 4
ROS2 methods raise `AdapterError(DDS_ONLY_ERROR_MSG)` (this adapter is
DDS-only). The factory only loads this module when
`TOPICFORGE_DDS_BACKEND=cyclone` (or `auto` resolving to cyclone) — see
`services/factory.py`.

v0.3.0 scope:
  * `list_participants` — full DCPSParticipant discovery via builtin reader
  * `detect_qos_mismatches` — DCPSSubscription + DCPSPublication paired
    by topic, run through the vendor-neutral pure analyzer in
    `adapters/common/qos_analyzer.py`
  * `peek_dds_samples` — works on the 4 builtin DCPS topics. Arbitrary
    user topics require IDL/XTypes discovery and raise an `AdapterError`
    pointing at the v0.3.x roadmap.

Sample-introspection helpers below are defensive against binding-version
shape variations — they read attributes via `getattr` with fallbacks and
collapse missing data to `None` / `"unknown"` rather than raising. A
single odd discovery sample must not break the whole tool call.
"""

from __future__ import annotations

import logging
from typing import Any

# Top-level imports — the factory only loads this module when the
# cyclonedds bindings are importable. ImportError here propagates to
# the factory which falls back to mock with a logged warning.
from cyclonedds.builtin import (
    BuiltinDataReader,
    BuiltinTopicDcpsParticipant,
    BuiltinTopicDcpsPublication,
    BuiltinTopicDcpsSubscription,
)
from cyclonedds.domain import DomainParticipant
from cyclonedds.util import duration

from topicforge.adapters.base import AdapterError, AdapterName, EffectiveMode
from topicforge.adapters.common import (
    DDS_ONLY_ERROR_MSG,
    canonicalize_vendor_id,
    detect_mismatches,
    format_guid,
)
from topicforge.models import (
    BagAnalysis,
    MessageSample,
    MismatchReport,
    ParticipantInfo,
    QosProfile,
    SampleResult,
    TopicInfo,
)

log = logging.getLogger(__name__)

# Tunables — kept module-level so a future env-var hook is a one-line
# change. Discovery is a bounded operation: `take_iter` returns whatever
# samples accumulated during the timeout window.
_DISCOVERY_TIMEOUT_SEC = 2.0
_SAMPLE_TIMEOUT_SEC = 1.0
_MAX_PARTICIPANTS = 256
_MAX_ENDPOINTS = 1024

# Builtin DCPS topics that `peek_dds_samples` can serve in v0.3.0.
# Arbitrary user topics require IDL/XTypes discovery (v0.3.x roadmap).
_BUILTIN_DCPS_TOPICS: dict[str, Any] = {
    "DCPSParticipant": BuiltinTopicDcpsParticipant,
    "DCPSSubscription": BuiltinTopicDcpsSubscription,
    "DCPSPublication": BuiltinTopicDcpsPublication,
}

_USER_TOPIC_ROADMAP_MSG = (
    "peek_dds_samples on arbitrary user topics requires IDL/XTypes "
    "discovery, which is a TopicForge v0.3.x roadmap item. The builtin "
    "DCPS topics (DCPSParticipant, DCPSSubscription, DCPSPublication) "
    "work today. See docs/projet-file/mcp-02-spec.md §7."
)


class CycloneDdsAdapter:
    """Read-only adapter backed by Eclipse CycloneDDS Python bindings."""

    name: AdapterName = "cyclone"

    def __init__(self, domain_id: int = 0) -> None:
        if domain_id < 0 or domain_id > 232:
            raise AdapterError(f"domain_id must be in 0..232, got {domain_id}")
        self._domain_id = domain_id
        try:
            self._dp = DomainParticipant(domain_id)
        except Exception as exc:  # binding-side errors vary by version
            raise AdapterError(
                f"Failed to create CycloneDDS DomainParticipant on domain {domain_id}: {exc}"
            ) from exc

    @property
    def effective_mode(self) -> EffectiveMode:
        return "live"

    def is_available(self) -> bool:
        # __init__ only completes when DomainParticipant() succeeds.
        return True

    # ----- ROS2 surface: not served by this adapter -----

    def list_topics(self) -> list[TopicInfo]:
        raise AdapterError(DDS_ONLY_ERROR_MSG)

    def get_topic_info(self, topic: str) -> TopicInfo:
        raise AdapterError(DDS_ONLY_ERROR_MSG)

    def sample_messages(self, topic: str, count: int) -> list[MessageSample]:
        raise AdapterError(DDS_ONLY_ERROR_MSG)

    def analyze_bag(self, path: str) -> BagAnalysis:
        raise AdapterError(DDS_ONLY_ERROR_MSG)

    # ----- DDS surface (v0.3.0 real implementation) -----

    def list_participants(self, domain_id: int = 0) -> list[ParticipantInfo]:
        """Discover DDS participants via the builtin DCPSParticipant reader.

        The `domain_id` argument is accepted for protocol uniformity but
        the adapter only observes the domain it joined at construction
        time. Callers asking for a different domain receive what *this*
        participant sees — spinning up a second participant on the fly
        would violate the "one bus join per adapter instance" rule.
        """
        try:
            reader = BuiltinDataReader(self._dp, BuiltinTopicDcpsParticipant)
            samples = list(reader.take_iter(timeout=duration(seconds=_DISCOVERY_TIMEOUT_SEC)))
        except Exception as exc:
            raise AdapterError(f"cyclone participant discovery failed: {exc}") from exc

        participants: list[ParticipantInfo] = []
        for sample in samples[:_MAX_PARTICIPANTS]:
            participants.append(
                ParticipantInfo(
                    guid=format_guid(_extract_guid(sample)),
                    vendor=canonicalize_vendor_id(_extract_vendor_id(sample)),
                    hostname=_extract_hostname(sample),
                    domain_id=self._domain_id,
                    mode_effective="live",
                )
            )
        return participants

    def detect_qos_mismatches(self, topic: str | None = None) -> list[MismatchReport]:
        """Pair reader/writer endpoints by topic, run the pure analyzer on each."""
        try:
            sub_reader = BuiltinDataReader(self._dp, BuiltinTopicDcpsSubscription)
            pub_reader = BuiltinDataReader(self._dp, BuiltinTopicDcpsPublication)
            subs = list(sub_reader.take_iter(timeout=duration(seconds=_DISCOVERY_TIMEOUT_SEC)))[
                :_MAX_ENDPOINTS
            ]
            pubs = list(pub_reader.take_iter(timeout=duration(seconds=_DISCOVERY_TIMEOUT_SEC)))[
                :_MAX_ENDPOINTS
            ]
        except Exception as exc:
            raise AdapterError(f"cyclone endpoint discovery failed: {exc}") from exc

        by_topic: dict[str, tuple[list[Any], list[Any]]] = {}
        for sample in subs:
            tname = _extract_topic_name(sample)
            if tname is None:
                continue
            if topic is not None and tname != topic:
                continue
            by_topic.setdefault(tname, ([], []))[0].append(sample)
        for sample in pubs:
            tname = _extract_topic_name(sample)
            if tname is None:
                continue
            if topic is not None and tname != topic:
                continue
            by_topic.setdefault(tname, ([], []))[1].append(sample)

        reports: list[MismatchReport] = []
        for tname, (readers, writers) in by_topic.items():
            for reader_sample in readers:
                reader_profile = _cyclone_qos_to_profile(reader_sample)
                if reader_profile is None:
                    continue
                for writer_sample in writers:
                    writer_profile = _cyclone_qos_to_profile(writer_sample)
                    if writer_profile is None:
                        continue
                    result = detect_mismatches(reader_profile, writer_profile)
                    if result is None:
                        continue
                    policies, severity = result
                    reports.append(
                        MismatchReport(
                            topic=tname,
                            reader_guid=format_guid(_extract_guid(reader_sample)),
                            writer_guid=format_guid(_extract_guid(writer_sample)),
                            incompatible_policies=policies,
                            severity=severity,
                            mode_effective="live",
                        )
                    )
        return reports

    def peek_dds_samples(self, topic: str, count: int) -> SampleResult:
        """Peek recent samples on a DDS topic.

        v0.3.0 limitation: works on the 4 builtin DCPS topics only.
        Arbitrary user topics require XTypes/IDL discovery — raise with
        a clear roadmap pointer rather than returning silent empty
        results.
        """
        if topic not in _BUILTIN_DCPS_TOPICS:
            raise AdapterError(_USER_TOPIC_ROADMAP_MSG)
        if count < 0:
            raise AdapterError("count must be >= 0")

        topic_class = _BUILTIN_DCPS_TOPICS[topic]
        try:
            reader = BuiltinDataReader(self._dp, topic_class)
            samples_raw = list(reader.take_iter(timeout=duration(seconds=_SAMPLE_TIMEOUT_SEC)))[
                :count
            ]
        except Exception as exc:
            raise AdapterError(f"cyclone peek failed: {exc}") from exc

        samples = [
            MessageSample(
                topic=topic,
                message_type=f"dds_builtin/{topic}",
                timestamp_ns=0,
                payload={
                    "vendor": canonicalize_vendor_id(_extract_vendor_id(s)),
                    "guid": format_guid(_extract_guid(s)),
                    "topic_name": _extract_topic_name(s),
                    "_raw_text": repr(s),
                },
            )
            for s in samples_raw
        ]
        return SampleResult(
            topic=topic,
            count=len(samples),
            samples=samples,
            mode_effective="live",
        )


# ---------------------------------------------------------------------------
# Sample-introspection helpers — defensive against binding shape variations.
# Each helper returns None / "unknown" / safe defaults rather than raising,
# so a single odd discovery sample never breaks the whole tool call.
# ---------------------------------------------------------------------------


def _extract_guid(sample: Any) -> bytes | None:
    """Pull the 16-byte GUID off a discovery sample, if present."""
    for attr in ("key", "participant_key", "guid"):
        v = getattr(sample, attr, None)
        if v is None:
            continue
        if isinstance(v, bytes):
            return v
        inner = getattr(v, "value", None)
        if isinstance(inner, bytes):
            return inner
    return None


def _extract_vendor_id(sample: Any) -> tuple[int, int] | None:
    """Pull the 2-byte OMG vendor_id off a discovery sample, if present."""
    v = getattr(sample, "vendor_id", None)
    if v is None:
        v = getattr(sample, "vendor", None)
    if v is None:
        return None
    if isinstance(v, bytes) and len(v) >= 2:
        return (v[0], v[1])
    inner = getattr(v, "vendorId", None)
    if isinstance(inner, (bytes, tuple, list)) and len(inner) >= 2:
        return (inner[0], inner[1])
    if isinstance(v, (tuple, list)) and len(v) >= 2:
        return (v[0], v[1])
    return None


def _extract_hostname(sample: Any) -> str | None:
    """Pull a hostname / participant-name hint off a sample, if exposed."""
    for attr in ("hostname", "participant_name", "user_data"):
        v = getattr(sample, attr, None)
        if isinstance(v, (bytes, bytearray)):
            try:
                decoded = v.decode("utf-8", errors="replace")
            except (UnicodeError, AttributeError):
                continue
            if decoded:
                return decoded
        if isinstance(v, str) and v:
            return v
    return None


def _extract_topic_name(sample: Any) -> str | None:
    v = getattr(sample, "topic_name", None)
    if v is None:
        v = getattr(sample, "topic", None)
    if isinstance(v, str) and v:
        return v
    return None


# QoS Policy class-name → canonical string maps. CycloneDDS exposes
# policies as instances of nested classes under `cyclonedds.qos.Policy.*`
# — we read them by simple class name to stay binding-version-agnostic.
_RELIABILITY_NAMES = {"Reliable": "RELIABLE", "BestEffort": "BEST_EFFORT"}
_DURABILITY_NAMES = {
    "Volatile": "VOLATILE",
    "TransientLocal": "TRANSIENT_LOCAL",
    "Transient": "TRANSIENT",
    "Persistent": "PERSISTENT",
}
_HISTORY_NAMES = {"KeepLast": "KEEP_LAST", "KeepAll": "KEEP_ALL"}


def _cyclone_qos_to_profile(sample: Any) -> QosProfile | None:
    """Map a Cyclone discovery sample's QoS into the canonical QosProfile.

    Returns `None` when essential QoS policies (reliability, durability,
    history) are missing — the analyzer needs all three present to
    produce a meaningful pair report.
    """
    qos = getattr(sample, "qos", None)
    if qos is None:
        return None

    reliability: str | None = None
    durability: str | None = None
    history: str | None = None
    history_depth: int | None = None
    deadline_ns: int | None = None

    try:
        for policy in qos:
            cls_name = type(policy).__name__
            if cls_name in _RELIABILITY_NAMES:
                reliability = _RELIABILITY_NAMES[cls_name]
            elif cls_name in _DURABILITY_NAMES:
                durability = _DURABILITY_NAMES[cls_name]
            elif cls_name in _HISTORY_NAMES:
                history = _HISTORY_NAMES[cls_name]
                depth = getattr(policy, "depth", None)
                if isinstance(depth, int):
                    history_depth = depth
            elif cls_name == "Deadline":
                d = getattr(policy, "duration", None)
                if d is None:
                    d = getattr(policy, "deadline", None)
                if hasattr(d, "to_nanoseconds"):
                    deadline_ns = int(d.to_nanoseconds())
                elif isinstance(d, int):
                    deadline_ns = d
    except (TypeError, AttributeError):  # defensive against odd qos shapes
        return None

    if reliability is None or durability is None or history is None:
        return None

    return QosProfile(
        reliability=reliability,  # type: ignore[arg-type]
        durability=durability,  # type: ignore[arg-type]
        history=history,  # type: ignore[arg-type]
        history_depth=history_depth,
        deadline_ns=deadline_ns,
    )
