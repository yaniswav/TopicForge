"""eProsima Fast DDS adapter — listener-driven discovery (v0.3.0).

Joins the bus as a read-only DDS-RTPS participant via the eProsima
Fast DDS Python bindings. A duck-typed listener accumulates discovery
state under an RLock so the public API methods read consistent
snapshots without racing the discovery callbacks.

See `docs/dds-interop-matrix.md` for the canonical multi-vendor
positioning. The factory only loads this module when
`TOPICFORGE_DDS_BACKEND=fast` (or `auto` resolving to fast) — see
`services/factory.py`.

v0.3.0 scope mirrors `CycloneDdsAdapter`:
  * `list_participants` — snapshot of discovered participants
  * `detect_qos_mismatches` — paired subs/pubs by topic + pure analyzer
  * `peek_dds_samples` — builtin discovery snapshots (DCPSParticipant /
    DCPSSubscription / DCPSPublication). Arbitrary user topics raise
    `AdapterError` pointing at the v0.3.x XTypes/IDL roadmap.

Sample-introspection helpers are defensive against binding-version
shape variations — same convention as the Cyclone adapter's helpers.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from typing import Any

# Top-level import — the factory only loads this module when fastdds is
# importable. ImportError here propagates to the factory which falls
# back to mock with a logged warning.
import fastdds

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

_DEFAULT_DISCOVERY_WAIT_MS = 1500
_MAX_PARTICIPANTS = 256
_MAX_ENDPOINTS = 1024

_BUILTIN_DCPS_TOPICS = frozenset({"DCPSParticipant", "DCPSSubscription", "DCPSPublication"})

_USER_TOPIC_ROADMAP_MSG = (
    "peek_dds_samples on arbitrary user topics requires IDL/XTypes "
    "discovery, which is a TopicForge v0.3.x roadmap item. The builtin "
    "DCPS snapshots (DCPSParticipant, DCPSSubscription, DCPSPublication) "
    "work today. See docs/projet-file/mcp-02-spec.md §7."
)


class _DiscoveryListener:
    """Aggregates Fast DDS discovery callbacks under a single RLock.

    We do not subclass `fastdds.DomainParticipantListener` at module
    import — that would surface as a hard error on hosts where the
    binding ships listener as a virtual C++ base whose Python proxy
    requires SWIG setup. The Fast DDS Python binding accepts a
    duck-typed listener: any object exposing the expected method
    signatures is bound via `create_participant(..., listener, mask)`.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._participants: dict[str, Any] = {}
        self._subscriptions: dict[str, Any] = {}
        self._publications: dict[str, Any] = {}

    def on_participant_discovery(self, dp: Any, info: Any, should_be_ignored: Any = None) -> None:
        try:
            status = getattr(info, "status", None)
            data = getattr(info, "info", None) or getattr(info, "participant_data", None) or info
            guid = format_guid(_extract_guid(data))
            with self._lock:
                if _is_removal(status):
                    self._participants.pop(guid, None)
                else:
                    self._participants[guid] = data
        except Exception:  # pragma: no cover — defensive
            log.exception("on_participant_discovery callback failed")

    def on_data_reader_discovery(self, dp: Any, info: Any, should_be_ignored: Any = None) -> None:
        try:
            status = getattr(info, "status", None)
            data = getattr(info, "info", None) or info
            guid = format_guid(_extract_guid(data))
            with self._lock:
                if _is_removal(status):
                    self._subscriptions.pop(guid, None)
                else:
                    self._subscriptions[guid] = data
        except Exception:  # pragma: no cover — defensive
            log.exception("on_data_reader_discovery callback failed")

    def on_data_writer_discovery(self, dp: Any, info: Any, should_be_ignored: Any = None) -> None:
        try:
            status = getattr(info, "status", None)
            data = getattr(info, "info", None) or info
            guid = format_guid(_extract_guid(data))
            with self._lock:
                if _is_removal(status):
                    self._publications.pop(guid, None)
                else:
                    self._publications[guid] = data
        except Exception:  # pragma: no cover — defensive
            log.exception("on_data_writer_discovery callback failed")

    def snapshot_participants(self) -> list[Any]:
        with self._lock:
            return list(self._participants.values())[:_MAX_PARTICIPANTS]

    def snapshot_subscriptions(self) -> list[Any]:
        with self._lock:
            return list(self._subscriptions.values())[:_MAX_ENDPOINTS]

    def snapshot_publications(self) -> list[Any]:
        with self._lock:
            return list(self._publications.values())[:_MAX_ENDPOINTS]


class FastDdsAdapter:
    """Read-only adapter backed by eProsima Fast DDS Python bindings."""

    name: AdapterName = "fast"

    def __init__(
        self,
        domain_id: int = 0,
        *,
        discovery_wait_ms: int = _DEFAULT_DISCOVERY_WAIT_MS,
    ) -> None:
        if domain_id < 0 or domain_id > 232:
            raise AdapterError(f"domain_id must be in 0..232, got {domain_id}")
        self._domain_id = domain_id
        self._listener = _DiscoveryListener()
        self._participant: Any | None = None
        self._factory: Any | None = None
        try:
            factory = fastdds.DomainParticipantFactory.get_instance()
            qos = fastdds.DomainParticipantQos()
            # Some binding versions expose `get_default_participant_qos` and
            # populate qos in-place ; older versions don't. Tolerant either way.
            with contextlib.suppress(Exception):
                factory.get_default_participant_qos(qos)
            mask = fastdds.StatusMask.all()
            self._participant = factory.create_participant(domain_id, qos, self._listener, mask)
            if self._participant is None:
                raise AdapterError("DomainParticipantFactory.create_participant returned None")
            self._factory = factory
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(
                f"Failed to create Fast DDS DomainParticipant on domain {domain_id}: {exc}"
            ) from exc
        # Bounded warm-up — discovery callbacks fire asynchronously after
        # the participant joins.
        if discovery_wait_ms > 0:
            time.sleep(discovery_wait_ms / 1000.0)

    @property
    def effective_mode(self) -> EffectiveMode:
        return "live"

    def is_available(self) -> bool:
        return self._participant is not None

    def close(self) -> None:
        """Release the underlying Fast DDS participant.

        Idempotent. Callers (typically test fixtures) should invoke
        this in teardown ; production code can rely on Python GC, but
        explicit cleanup is recommended on long-running processes.
        """
        if self._participant is not None and self._factory is not None:
            try:
                self._factory.delete_participant(self._participant)
            except Exception:  # pragma: no cover — defensive on shutdown
                log.exception("delete_participant failed on FastDdsAdapter.close()")
            self._participant = None

    # ----- ROS2 surface: not served by this adapter -----

    def list_topics(self) -> list[TopicInfo]:
        raise AdapterError(DDS_ONLY_ERROR_MSG)

    def get_topic_info(self, topic: str) -> TopicInfo:
        raise AdapterError(DDS_ONLY_ERROR_MSG)

    def sample_messages(self, topic: str, count: int) -> list[MessageSample]:
        raise AdapterError(DDS_ONLY_ERROR_MSG)

    def analyze_bag(self, path: str) -> BagAnalysis:
        raise AdapterError(DDS_ONLY_ERROR_MSG)

    # ----- DDS surface (v0.3.0) -----

    def list_participants(self, domain_id: int = 0) -> list[ParticipantInfo]:
        """Snapshot of discovered participants from the listener state."""
        samples = self._listener.snapshot_participants()
        return [
            ParticipantInfo(
                guid=format_guid(_extract_guid(s)),
                vendor=canonicalize_vendor_id(_extract_vendor_id(s)),
                hostname=_extract_hostname(s),
                domain_id=self._domain_id,
                mode_effective="live",
            )
            for s in samples
        ]

    def detect_qos_mismatches(self, topic: str | None = None) -> list[MismatchReport]:
        subs = self._listener.snapshot_subscriptions()
        pubs = self._listener.snapshot_publications()

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
                reader_profile = _fast_qos_to_profile(reader_sample)
                if reader_profile is None:
                    continue
                for writer_sample in writers:
                    writer_profile = _fast_qos_to_profile(writer_sample)
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
        """v0.3.0 scope: builtin DCPS snapshots only.

        Arbitrary user topics require XTypes/IDL discovery — raise
        with a v0.3.x roadmap pointer rather than returning silent
        empty results.
        """
        if topic not in _BUILTIN_DCPS_TOPICS:
            raise AdapterError(_USER_TOPIC_ROADMAP_MSG)
        if count < 0:
            raise AdapterError("count must be >= 0")

        if topic == "DCPSParticipant":
            raw = self._listener.snapshot_participants()
        elif topic == "DCPSSubscription":
            raw = self._listener.snapshot_subscriptions()
        else:
            raw = self._listener.snapshot_publications()

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
            for s in raw[:count]
        ]
        return SampleResult(
            topic=topic,
            count=len(samples),
            samples=samples,
            mode_effective="live",
        )


# ---------------------------------------------------------------------------
# Internal helpers — defensive against binding shape variations across
# Fast DDS Python binding versions. Same convention as the Cyclone helpers:
# never raise, collapse missing data to None / "unknown" / safe defaults.
# ---------------------------------------------------------------------------


def _is_removal(status: Any) -> bool:
    """Detect a 'participant/endpoint removed' discovery status across
    binding versions. Fast DDS exposes status as either an enum value
    or a string label — accept both.
    """
    if status is None:
        return False
    s = str(status).upper()
    return "REMOVED" in s or "DISPOSED" in s or "DROPPED" in s


def _extract_guid(sample: Any) -> bytes | None:
    """Pull a 16-byte GUID off a Fast DDS discovery sample."""
    for attr in ("guid", "key", "participant_key"):
        v = getattr(sample, attr, None)
        if v is None:
            continue
        if isinstance(v, bytes):
            return v
        for inner_attr in ("value", "data", "guidPrefix"):
            inner = getattr(v, inner_attr, None)
            if isinstance(inner, bytes):
                return inner
            if isinstance(inner, (tuple, list)) and inner:
                try:
                    return bytes(int(b) & 0xFF for b in inner)
                except (TypeError, ValueError):
                    continue
        if isinstance(v, (tuple, list)) and v:
            try:
                return bytes(int(b) & 0xFF for b in v)
            except (TypeError, ValueError):
                continue
    return None


def _extract_vendor_id(sample: Any) -> tuple[int, int] | None:
    v = getattr(sample, "vendor_id", None)
    if v is None:
        info = getattr(sample, "info", None)
        if info is not None:
            v = getattr(info, "vendor_id", None)
    if v is None:
        return None
    if isinstance(v, bytes) and len(v) >= 2:
        return (v[0], v[1])
    if isinstance(v, (tuple, list)) and len(v) >= 2:
        try:
            return (int(v[0]), int(v[1]))
        except (TypeError, ValueError):
            return None
    inner = getattr(v, "vendor_id", None)
    if isinstance(inner, (bytes, tuple, list)) and len(inner) >= 2:
        try:
            return (int(inner[0]), int(inner[1]))
        except (TypeError, ValueError):
            return None
    return None


def _extract_hostname(sample: Any) -> str | None:
    for attr in ("hostname", "participant_name", "name", "user_data"):
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
    if isinstance(v, str) and v:
        return v
    return None


# QoS enum integer values come from the binding's own constants rather
# than being hardcoded — they can shift across major binding versions.
def _build_reliability_map() -> dict[int, str]:
    return {
        getattr(fastdds, "RELIABLE_RELIABILITY_QOS", 1): "RELIABLE",
        getattr(fastdds, "BEST_EFFORT_RELIABILITY_QOS", 0): "BEST_EFFORT",
    }


def _build_durability_map() -> dict[int, str]:
    return {
        getattr(fastdds, "VOLATILE_DURABILITY_QOS", 0): "VOLATILE",
        getattr(fastdds, "TRANSIENT_LOCAL_DURABILITY_QOS", 1): "TRANSIENT_LOCAL",
        getattr(fastdds, "TRANSIENT_DURABILITY_QOS", 2): "TRANSIENT",
        getattr(fastdds, "PERSISTENT_DURABILITY_QOS", 3): "PERSISTENT",
    }


def _build_history_map() -> dict[int, str]:
    return {
        getattr(fastdds, "KEEP_LAST_HISTORY_QOS", 0): "KEEP_LAST",
        getattr(fastdds, "KEEP_ALL_HISTORY_QOS", 1): "KEEP_ALL",
    }


_RELIABILITY_MAP = _build_reliability_map()
_DURABILITY_MAP = _build_durability_map()
_HISTORY_MAP = _build_history_map()


def _fast_qos_to_profile(sample: Any) -> QosProfile | None:
    qos = getattr(sample, "qos", None)
    if qos is None:
        return None

    reliability: str | None = None
    durability: str | None = None
    history: str | None = None
    history_depth: int | None = None
    deadline_ns: int | None = None

    try:
        rel = getattr(qos, "reliability", None) or getattr(qos, "m_reliability", None)
        if rel is not None:
            kind = getattr(rel, "kind", None)
            if kind is not None:
                reliability = _RELIABILITY_MAP.get(kind)

        dur = getattr(qos, "durability", None) or getattr(qos, "m_durability", None)
        if dur is not None:
            kind = getattr(dur, "kind", None)
            if kind is not None:
                durability = _DURABILITY_MAP.get(kind)

        hist = getattr(qos, "history", None) or getattr(qos, "m_history", None)
        if hist is not None:
            kind = getattr(hist, "kind", None)
            if kind is not None:
                history = _HISTORY_MAP.get(kind)
            depth = getattr(hist, "depth", None)
            if isinstance(depth, int):
                history_depth = depth

        ddl = getattr(qos, "deadline", None) or getattr(qos, "m_deadline", None)
        if ddl is not None:
            period = getattr(ddl, "period", None)
            if period is not None:
                sec = getattr(period, "seconds", None)
                if sec is None:
                    sec = getattr(period, "sec", None) or 0
                nsec = getattr(period, "nanosec", None)
                if nsec is None:
                    nsec = getattr(period, "nanoseconds", None) or 0
                if sec or nsec:
                    deadline_ns = int(sec) * 1_000_000_000 + int(nsec)
    except (TypeError, AttributeError):  # defensive
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
