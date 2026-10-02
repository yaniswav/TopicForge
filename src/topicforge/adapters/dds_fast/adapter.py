"""eProsima Fast DDS adapter: listener-driven discovery (v0.3.0+).

Joins the bus as a read-only DDS-RTPS participant via the eProsima
Fast DDS Python bindings. A duck-typed listener accumulates discovery
state under an RLock so the public API methods read consistent
snapshots without racing the discovery callbacks. Lifecycle events
(`discovered` / `lost`) are captured natively in
`on_participant_discovery`: no polling reconciliation needed,
contrasted with the Cyclone polling path.

See `docs/dds-interop-matrix.md` for the canonical multi-vendor
positioning. The factory only loads this module when
`TOPICFORGE_DDS_BACKEND=fast` (or `auto` resolving to fast): see
`services/factory.py`. Pair with `Ros2CliAdapter` via the v0.4.0
`CompositeAdapter` to serve both ROS2 and DDS surfaces simultaneously.

Current scope (v0.4.0+) mirrors `CycloneDdsAdapter` :

  * `list_participants`: snapshot of discovered participants enriched
    with `LifecycleBuffer` fields (first/last seen, status, seen_count).
  * `detect_qos_mismatches`: paired subs/pubs by topic + pure analyzer.
  * `peek_dds_samples`: structured payloads on the 3 builtin DCPS topics
    (DCPSParticipant, DCPSSubscription, DCPSPublication). A user topic
    announced on the bus yields one annotated placeholder
    (`_decode_status="raw"`, empty bytes): no payload is decoded, because
    `_try_dynamic_decode_fast` is not implemented. The placeholder is not a
    received sample and never feeds `topic_metrics`.
  * `participant_events`: `discovered` + `lost` from native listener
    callbacks ; no polling required.
  * `topic_metrics`: opportunistic metrics buffered as `peek_dds_samples`
    surfaces builtin DCPS samples (same caveat as Cyclone; no
    at-sample-receive callback in fastdds 2.6.x Python). User topics stay
    at `samples_observed=0`.

Sample-introspection helpers are defensive against binding-version
shape variations: same convention as the Cyclone adapter's helpers.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from typing import Any

# Top-level import: the factory only loads this module when fastdds is
# importable. ImportError here propagates to the factory which falls
# back to mock with a logged warning.
import fastdds

from topicforge.adapters.base import AdapterError, AdapterName, EffectiveMode
from topicforge.adapters.common import (
    DDS_ONLY_ERROR_MSG,
    LifecycleBuffer,
    MetricsBuffer,
    canonicalize_vendor_id,
    detect_mismatches_across_endpoints,
    format_guid,
    metrics_status,
    user_topic_result,
    validate_domain_id,
)
from topicforge.adapters.common import (
    fast_extract_guid as _extract_guid,
)
from topicforge.adapters.common import (
    fast_extract_hostname as _extract_hostname,
)
from topicforge.adapters.common import (
    fast_extract_topic_name as _extract_topic_name,
)
from topicforge.adapters.common import (
    fast_extract_vendor_id as _extract_vendor_id,
)
from topicforge.adapters.common import (
    fast_qos_to_profile as _common_fast_qos_to_profile,
)
from topicforge.adapters.common import (
    is_removal as _is_removal,
)
from topicforge.models import (
    BagAnalysis,
    EndpointListing,
    MessageSample,
    MismatchScan,
    ParticipantEvent,
    ParticipantInfo,
    QosProfile,
    SampleResult,
    TopicInfo,
    TopicMetrics,
)

log = logging.getLogger(__name__)

_DEFAULT_DISCOVERY_WAIT_MS = 1500
_MAX_PARTICIPANTS = 256
_MAX_ENDPOINTS = 1024

_BUILTIN_DCPS_TOPICS = frozenset({"DCPSParticipant", "DCPSSubscription", "DCPSPublication"})


class _DiscoveryListener:
    """Aggregates Fast DDS discovery callbacks under a single RLock.

    We do not subclass `fastdds.DomainParticipantListener` at module
    import: that would surface as a hard error on hosts where the
    binding ships listener as a virtual C++ base whose Python proxy
    requires SWIG setup. The Fast DDS Python binding accepts a
    duck-typed listener: any object exposing the expected method
    signatures is bound via `create_participant(..., listener, mask)`.
    """

    def __init__(self, *, lifecycle: LifecycleBuffer | None = None, domain_id: int = 0) -> None:
        self._lock = threading.RLock()
        self._participants: dict[str, Any] = {}
        self._subscriptions: dict[str, Any] = {}
        self._publications: dict[str, Any] = {}
        # v0.4.0 Phase 1: listener callbacks feed the lifecycle buffer
        # directly (no polling reconciliation needed; Fast DDS gives us
        # arrival AND removal events). `lifecycle=None` keeps the
        # listener usable in isolation for tests that don't care.
        self._lifecycle = lifecycle
        self._domain_id = domain_id

    def on_participant_discovery(self, dp: Any, info: Any, should_be_ignored: Any = None) -> None:
        try:
            status = getattr(info, "status", None)
            data = getattr(info, "info", None) or getattr(info, "participant_data", None) or info
            guid = format_guid(_extract_guid(data))
            removed = _is_removal(status)
            with self._lock:
                if removed:
                    self._participants.pop(guid, None)
                else:
                    self._participants[guid] = data
            if self._lifecycle is not None:
                if removed:
                    self._lifecycle.record_lost(
                        guid=guid,
                        vendor=canonicalize_vendor_id(_extract_vendor_id(data)),
                        hostname=_extract_hostname(data),
                        domain_id=self._domain_id,
                        mode_effective="live",
                    )
                else:
                    self._lifecycle.record_seen(
                        guid=guid,
                        vendor=canonicalize_vendor_id(_extract_vendor_id(data)),
                        hostname=_extract_hostname(data),
                        domain_id=self._domain_id,
                        mode_effective="live",
                    )
        except Exception:  # pragma: no cover: defensive
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
        except Exception:  # pragma: no cover: defensive
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
        except Exception:  # pragma: no cover: defensive
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
        validate_domain_id(domain_id)
        self._domain_id = domain_id
        # v0.4.0 Phase 1: lifecycle buffer fed by listener callbacks.
        self._lifecycle = LifecycleBuffer()
        # v0.4.0 Phase 2: metrics buffer fed opportunistically by
        # `peek_dds_samples` flows.
        self._metrics = MetricsBuffer()
        self._listener = _DiscoveryListener(lifecycle=self._lifecycle, domain_id=domain_id)
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
                raise AdapterError(
                    f"Fast DDS DomainParticipant creation returned None on domain "
                    f"{domain_id}. Likely an ABI mismatch between the `fastdds` Python "
                    f"binding and the installed Fast DDS core library: pin "
                    f"`fastdds>=2.6.1,<3` and reinstall, or check the FastDDS_DEFAULT_PROFILES_FILE "
                    f"env var if you set one."
                )
            self._factory = factory
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(
                f"Failed to create Fast DDS DomainParticipant on domain {domain_id} "
                f"({type(exc).__name__}: {exc})."
            ) from exc
        # Bounded warm-up: discovery callbacks fire asynchronously after
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
            except Exception:  # pragma: no cover: defensive on shutdown
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

    def peek_bag_samples(self, path: str, topic: str, count: int) -> SampleResult:
        raise AdapterError(DDS_ONLY_ERROR_MSG)

    # ----- DDS surface (v0.3.0) -----

    def list_endpoints(
        self,
        topic: str | None = None,
        participant_guid: str | None = None,
        include_observer: bool = False,
        include_departed: bool = False,
    ) -> EndpointListing:
        # The Fast listener keeps raw discovery info objects whose layout has
        # never been read on a real bus, so no endpoint record is built from them.
        raise AdapterError("list_endpoints is not supported on the Fast backend yet.")

    def list_participants(self, domain_id: int = 0) -> list[ParticipantInfo]:
        """Snapshot of discovered participants from the lifecycle buffer.

        v0.4.0 Phase 1: the listener callbacks have already populated
        the buffer with lifecycle fields. We read the snapshot from the
        buffer rather than rebuilding `ParticipantInfo` from the raw
        listener cache so `first_seen_ns`, `last_seen_ns`, `status`,
        and `seen_count` are exposed.
        """
        return self._lifecycle.snapshot_participants(domain_id=self._domain_id)

    def detect_qos_mismatches(self, topic: str | None = None) -> MismatchScan:
        """Pair reader/writer endpoints by topic via the shared analyzer.

        The pairing / reporting logic lives in
        `common.qos_endpoints.detect_mismatches_across_endpoints` (shared with
        the Cyclone adapter, unit-tested without a binding). This method only
        supplies the listener's discovery snapshots and the Fast helpers.
        """
        return detect_mismatches_across_endpoints(
            subs=self._listener.snapshot_subscriptions(),
            pubs=self._listener.snapshot_publications(),
            topic=topic,
            qos_to_profile=_fast_qos_to_profile,
            extract_topic_name=_extract_topic_name,
            extract_guid=_extract_guid,
        )

    def peek_dds_samples(self, topic: str, count: int) -> SampleResult:
        """Builtin DCPS snapshots, or a placeholder for a discovered user topic.

        The 3 builtin DCPS topics keep their v0.3.0 structured-payload
        shape. A user topic discovered on the bus returns one placeholder
        sample (`_decode_status="raw"`, empty bytes, with a note saying the
        payload is not decoded); it is not a received sample and is not
        recorded for `topic_metrics`. Unknown topics raise `AdapterError`.
        """
        if count < 0:
            raise AdapterError("count must be >= 0")

        if topic in _BUILTIN_DCPS_TOPICS:
            return self._peek_builtin(topic, count)

        return self._peek_user_topic(topic, count)

    def _peek_builtin(self, topic: str, count: int) -> SampleResult:
        if topic == "DCPSParticipant":
            raw = self._listener.snapshot_participants()
        elif topic == "DCPSSubscription":
            raw = self._listener.snapshot_subscriptions()
        else:
            raw = self._listener.snapshot_publications()

        now_ns = time.time_ns()
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
        # v0.4.0 Phase 2: opportunistic metrics fill.
        for _ in samples:
            self._metrics.record(
                topic=topic,
                receive_ns=now_ns,
                sequence_number=None,
                publish_ns=None,
                domain_id=self._domain_id,
            )
        return SampleResult(
            topic=topic,
            count=len(samples),
            samples=samples,
            mode_effective="live",
        )

    def _peek_user_topic(self, topic: str, count: int) -> SampleResult:
        """User-topic peek: topic presence plus an annotated placeholder.

        Confirms the topic is announced on the bus (else `AdapterError`).
        `_try_dynamic_decode_fast` always returns `None` today, so the
        placeholder path runs. The placeholder is not a received sample and
        is never recorded into `MetricsBuffer`.
        """
        if not self._is_topic_on_bus(topic):
            raise AdapterError(
                f"DDS topic {topic!r} not discovered on domain {self._domain_id}. "
                "Confirm a publisher is alive and reachable, or call "
                "list_participants / detect_qos_mismatches first to inspect "
                "current bus state."
            )

        decoded = _try_dynamic_decode_fast(topic, count)
        if decoded is not None:
            return SampleResult(
                topic=topic,
                count=len(decoded),
                samples=decoded,
                mode_effective="live",
            )

        # Nothing was received, so nothing is recorded into the metrics buffer.
        return user_topic_result(topic, "live")

    def _is_topic_on_bus(self, topic: str) -> bool:
        """True iff `topic` appears in any subscription or publication."""
        for sample in (
            self._listener.snapshot_subscriptions() + self._listener.snapshot_publications()
        ):
            if _extract_topic_name(sample) == topic:
                return True
        return False

    def participant_events(
        self, domain_id: int = 0, lookback_seconds: int = 300
    ) -> list[ParticipantEvent]:
        """Return lifecycle events captured by the listener callbacks.

        Fast DDS' listener fires on its worker thread ; the lifecycle
        buffer's RLock serializes writes against this method's read.
        """
        if lookback_seconds < 1 or lookback_seconds > 86400:
            raise AdapterError(f"lookback_seconds must be in 1..86400, got {lookback_seconds}")
        return self._lifecycle.events_since(
            lookback_seconds=lookback_seconds,
            domain_id=self._domain_id,
        )

    def topic_metrics(
        self, topic: str, window_seconds: int = 60, domain_id: int = 0
    ) -> TopicMetrics:
        """Return temporal metrics computed from the metrics buffer.

        Fast DDS shares the same opportunistic-fill semantics as the
        Cyclone adapter: the buffer accumulates only when
        `peek_dds_samples` surfaces builtin DCPS samples. The listener-driven
        discovery surface does NOT today expose at-sample-receive
        callbacks for Python (Fast DDS 2.6.x), so we cannot push
        samples in the background.
        """
        if window_seconds < 1 or window_seconds > 3600:
            raise AdapterError(f"window_seconds must be in 1..3600, got {window_seconds}")
        metrics = self._metrics.compute_metrics(
            topic=topic,
            window_seconds=window_seconds,
            domain_id=self._domain_id,
            mode_effective="live",
        )
        return metrics.model_copy(
            update={"status": metrics_status(topic, metrics.samples_observed)}
        )


def _try_dynamic_decode_fast(topic: str, count: int) -> list[MessageSample] | None:
    """Dynamic decode of a user topic via Fast DDS: NOT IMPLEMENTED, returns `None`.

    The fastdds Python binding does not expose a stable remote-TypeObject
    lookup, so there is nothing to decode with. The caller surfaces the
    annotated placeholder instead. The probe below only logs whether
    `fastdds.TypeObjectFactory` exists; it never decodes anything.

    TODO(roadmap): wire `TypeObjectFactory` against the discovered
    TypeIdentifier once the binding exposes it, and validate on a real bus.
    """
    factory_cls = getattr(fastdds, "TypeObjectFactory", None)
    log.debug(
        "fastdds dynamic XTypes decode not implemented (TypeObjectFactory %s) ; "
        "topic %r (count=%d) falls back to annotated placeholder",
        "present" if factory_cls is not None else "missing",
        topic,
        count,
    )
    return None


# Sample-introspection helpers (_is_removal / _extract_guid /
# _extract_vendor_id / _extract_hostname / _extract_topic_name) were moved
# to the binding-free `topicforge.adapters.common.dds_introspection` module
# (Lot 0, audit 2026-07-08) so they are unit-testable without the fastdds
# bindings installed. They are imported and aliased back to their original
# names at the top of this module, so the call sites above are unchanged.
#
# The QoS enum maps below stay here because they read integer values from
# the `fastdds` binding itself. The normalization logic that consumes them
# lives in `common.qos_normalize.fast_qos_to_profile` (also testable with
# synthetic maps): `_fast_qos_to_profile` below binds the two together.


# QoS enum integer values come from the binding's own constants rather
# than being hardcoded: they can shift across major binding versions.
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
    """Bind the shared Fast normalizer to this binding's enum maps.

    The pure normalization logic lives in
    `common.qos_normalize.fast_qos_to_profile` ; this thin wrapper feeds it
    the `fastdds`-derived int->str maps so the call sites in
    `detect_qos_mismatches` stay unchanged.
    """
    return _common_fast_qos_to_profile(
        sample,
        reliability_map=_RELIABILITY_MAP,
        durability_map=_DURABILITY_MAP,
        history_map=_HISTORY_MAP,
    )
