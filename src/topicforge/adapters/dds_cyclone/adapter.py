"""Cyclone DDS adapter.

Joins the bus as a read-only DDS-RTPS participant through the
`cyclonedds.builtin` readers and serves the discovery tools from them. A
daemon `DiscoveryTracker` thread is the only code that takes from the three
builtin readers and fills the caches of `common/discovery_tracker.py`; tool
handlers read those caches, so lifecycle does not depend on tool calls. Every
binding call goes through one process-wide lock, because the Python binding
is not thread-safe. User-topic payloads are not decoded (`topic_metrics` has
data only for builtin topics). ROS2 graph methods raise
`AdapterError(DDS_ONLY_ERROR_MSG)`; `CompositeAdapter` pairs it with
`Ros2CliAdapter`.
"""

from __future__ import annotations

import atexit
import logging
import threading
import time
from itertools import islice
from typing import Any

# ImportError here propagates to the factory, which falls back to mock.
from cyclonedds.builtin import (
    BuiltinDataReader,
    BuiltinTopicDcpsParticipant,
    BuiltinTopicDcpsPublication,
    BuiltinTopicDcpsSubscription,
)
from cyclonedds.core import InstanceState, Policy, Qos, ReadCondition, SampleState, ViewState
from cyclonedds.domain import DomainParticipant
from cyclonedds.util import duration

from topicforge.adapters.base import AdapterError, AdapterName, EffectiveMode
from topicforge.adapters.common import (
    DDS_ONLY_ERROR_MSG,
    BufferedTake,
    DiscoveryCaches,
    DiscoveryTracker,
    MetricsBuffer,
    SampleCache,
    announced_ns_of,
    builtin_payload,
    declared_hz_from_endpoints,
    decode_dynamic_sample,
    decode_field_value,
    dynamic_type_name,
    endpoint_infos_from_samples,
    extract_publish_ns_from_payload,
    extract_seq_from_payload,
    format_participant_key,
    iter_field_names,
    listing_from_samples,
    metrics_status,
    participant_names,
    resolve_user_topic,
    scan_endpoints,
    take_bounded,
    user_topic_result,
    validate_domain_id,
)
from topicforge.adapters.common import (
    cyclone_extract_topic_name as _extract_topic_name,
)
from topicforge.constants import DEFAULT_MAX_ARRAY_LENGTH, DEFAULT_SAMPLE_TIMEOUT_S
from topicforge.models import (
    BagAnalysis,
    EndpointListing,
    MessageSample,
    MismatchScan,
    ParticipantEvent,
    ParticipantInfo,
    SampleResult,
    TopicInfo,
    TopicListItem,
    TopicMetrics,
)

# Aliases for the shared decoders in `adapters/common/cdr_decoder.py`.
_decode_dynamic_sample = decode_dynamic_sample
_iter_field_names = iter_field_names
_decode_field_value = decode_field_value
_dynamic_type_name = dynamic_type_name
_extract_seq_from_payload = extract_seq_from_payload
_extract_publish_ns_from_payload = extract_publish_ns_from_payload

log = logging.getLogger(__name__)

# `read_iter(timeout=...)` (used only by the unreachable dynamic-decode code)
# resets its timeout on every sample, so bound it with `take_bounded`.
_DISCOVERY_TIMEOUT_SEC = 2.0
_SAMPLE_TIMEOUT_SEC = 1.0
_MAX_PARTICIPANTS = 256
_MAX_ENDPOINTS = 1024

# Builtin topics `peek_dds_samples` serves with structured payloads.
_BUILTIN_DCPS_TOPICS: dict[str, Any] = {
    "DCPSParticipant": BuiltinTopicDcpsParticipant,
    "DCPSSubscription": BuiltinTopicDcpsSubscription,
    "DCPSPublication": BuiltinTopicDcpsPublication,
}


def _try_dynamic_decode_cyclone(dp: Any, topic: str, count: int) -> list[MessageSample] | None:
    """Dynamic XTypes decode of a user topic: disabled, always returns `None`.

    `_decode_dynamic_unvalidated` has never run against a real bus and, per
    the cyclonedds 11.0.1 binding source, cannot work as written:

      1. `cyclonedds.dynamic.get_types_for_typeid(participant, type_id,
         timeout)` takes three arguments, and needs Cyclone built with
         `ENABLE_TYPE_DISCOVERY`; the code passes one.
      2. It returns `(type, nested_types)`; the code passes the whole tuple
         to `Topic(dp, name, data_type)`, which wants an IDL type.
      3. `list(reader.read_iter(...))[:count]` never returns on a topic
         publishing faster than the timeout; use `take_bounded`.

    Re-enable only behind a flag, after a run on a real bus
    (`scripts/integration/`).
    """
    return None


def _decode_dynamic_unvalidated(  # pragma: no cover: unreachable, never validated
    dp: Any, topic: str, count: int
) -> list[MessageSample] | None:
    """Unwired dynamic decode pipeline, kept as the starting point for a rewrite.

    Discovers a type id via DCPSPublication, resolves the TypeObject, reads
    up to `count` samples with a typed reader and decodes them. Do not call
    it before the defects listed in `_try_dynamic_decode_cyclone` are fixed.
    """
    try:
        from cyclonedds import dynamic as cyclone_dynamic  # type: ignore[import-not-found]
    except ImportError:
        return None

    type_resolver = _find_dynamic_resolver(cyclone_dynamic)
    if type_resolver is None:
        return None

    type_id = _discover_type_id_for_topic(dp, topic)
    if type_id is None:
        return None

    try:
        type_object = type_resolver(type_id)  # defects 1 (arity) and 2 (tuple)
    except Exception:
        log.debug("dynamic type resolution failed for topic %r", topic, exc_info=True)
        return None
    if type_object is None:
        return None

    samples_raw = _collect_dynamic_samples(dp, topic, type_object, count)
    if samples_raw is None:
        return None

    samples: list[MessageSample] = []
    for raw in samples_raw:
        payload = _decode_dynamic_sample(raw)
        samples.append(
            MessageSample(
                topic=topic,
                message_type=_dynamic_type_name(type_object),
                timestamp_ns=0,
                stamp_source="none",
                payload=payload,
            )
        )
    return samples


def _find_dynamic_resolver(cyclone_dynamic: Any) -> Any | None:  # pragma: no cover
    """First callable type resolver on `cyclonedds.dynamic`.

    The entry point was renamed across binding versions, so known names are
    probed in order.
    """
    for attr in ("get_types_for_typeid", "get_type_for_endpoint", "get_type"):
        candidate = getattr(cyclone_dynamic, attr, None)
        if callable(candidate):
            return candidate
    return None


def _discover_type_id_for_topic(dp: Any, topic: str) -> Any | None:  # pragma: no cover
    """Type identifier of the first DCPSPublication sample for `topic`, else `None`.

    Reads at most `_MAX_ENDPOINTS` samples.
    """
    try:
        reader = BuiltinDataReader(dp, BuiltinTopicDcpsPublication)
        samples = islice(
            reader.read_iter(timeout=duration(seconds=_DISCOVERY_TIMEOUT_SEC)), _MAX_ENDPOINTS
        )
        for sample in samples:
            if _extract_topic_name(sample) != topic:
                continue
            for attr in ("type_id", "type_identifier", "type_info"):
                tid = getattr(sample, attr, None)
                if tid is not None:
                    return tid
    except Exception:  # pragma: no cover: defensive
        log.debug("type-id discovery probe failed for topic %r", topic, exc_info=True)
    return None


def _collect_dynamic_samples(  # pragma: no cover: unreachable, never validated
    dp: Any, topic: str, type_object: Any, count: int
) -> list[Any] | None:
    """Read at most `count` samples with a typed reader; `None` if it cannot be built.

    `count` is the hard bound: `_SAMPLE_TIMEOUT_SEC` is only a per-sample wait.
    """
    try:
        from cyclonedds.sub import DataReader as DynamicDataReader  # type: ignore[import-not-found]
        from cyclonedds.topic import Topic as DynamicTopic  # type: ignore[import-not-found]

        dynamic_topic = DynamicTopic(dp, topic, type_object)
        reader = DynamicDataReader(dp, dynamic_topic)
        return take_bounded(reader.read_iter(timeout=duration(seconds=_SAMPLE_TIMEOUT_SEC)), count)
    except Exception:
        log.debug("typed reader construction failed for topic %r", topic, exc_info=True)
        return None


# The cyclonedds binding is not thread-safe when converting QoS: two threads
# inside take() at once (two adapters in one process) corrupted the heap on
# Windows (0xc0000374). Every binding call goes through this lock.
_BINDING_LOCK = threading.RLock()


class CycloneDdsAdapter:
    """Read-only adapter backed by Eclipse CycloneDDS Python bindings."""

    name: AdapterName = "cyclone"

    def __init__(self, domain_id: int = 0) -> None:
        validate_domain_id(domain_id)
        self._domain_id = domain_id
        # Fed by the tracker thread started below.
        self._caches = DiscoveryCaches()
        self._lifecycle = self._caches.lifecycle
        self._metrics = MetricsBuffer()
        try:
            # Named, so the observer is recognizable in every listing.
            with _BINDING_LOCK:
                self._dp = DomainParticipant(domain_id, qos=Qos(Policy.EntityName("topicforge")))
                self._observer = format_participant_key(self._dp.guid)
        except Exception as exc:  # binding-side errors vary by version
            raise AdapterError(
                f"Failed to create CycloneDDS DomainParticipant on domain {domain_id}: {exc}"
            ) from exc
        self.observer_started_ns = time.time_ns()
        # One reader per builtin topic, read by the tracker thread only.
        self._builtin: dict[Any, tuple[Any, Any]] = {}
        with _BINDING_LOCK:
            for topic_class in (
                BuiltinTopicDcpsParticipant,
                BuiltinTopicDcpsPublication,
                BuiltinTopicDcpsSubscription,
            ):
                self._builtin_reader(topic_class)
        self._tracker = DiscoveryTracker(self._build_take_all(), self._caches, domain_id=domain_id)
        self._tracker.start()
        # Stop the tracker before teardown: a pass still reading the builtin
        # readers while Cyclone shuts down crashed the process.
        atexit.register(self.close)

    def _builtin_reader(self, topic_class: Any) -> tuple[Any, Any]:
        """The adapter's reader for one builtin topic, and an any-state read condition."""
        entry = self._builtin.get(topic_class)
        if entry is None:
            reader = BuiltinDataReader(self._dp, topic_class)
            # Every instance state: a leave arrives as an invalid sample
            # (key + sample_info) for a disposed or no-writers instance.
            condition = ReadCondition(reader, SampleState.Any | ViewState.Any | InstanceState.Any)
            entry = self._builtin[topic_class] = (reader, condition)
        return entry

    def _take_new(self, topic_class: Any, limit: int, sink: list[Any]) -> None:
        """Take everything new from one builtin reader into `sink` (tracker thread only).

        Each batch lands in `sink` as soon as it is taken, so a later failure
        cannot drop samples `take()` already removed from the reader.
        """
        reader, condition = self._builtin_reader(topic_class)
        while True:
            with _BINDING_LOCK:
                batch = reader.take(N=limit, condition=condition)
            sink.extend(batch)
            if len(batch) < limit:
                return

    def _build_take_all(self) -> BufferedTake:
        """The tracker's take function: participants, publications, subscriptions."""
        return BufferedTake(
            lambda sink: self._take_new(BuiltinTopicDcpsParticipant, _MAX_PARTICIPANTS, sink),
            lambda sink: self._take_new(BuiltinTopicDcpsPublication, _MAX_ENDPOINTS, sink),
            lambda sink: self._take_new(BuiltinTopicDcpsSubscription, _MAX_ENDPOINTS, sink),
        )

    def close(self) -> None:
        """Stop the tracker thread. The participant is released with the process."""
        self._tracker.stop()

    def await_discovery_ready(self) -> bool:
        """Wait (at most 3 s) for the tracker to be warm: 2 passes, observer at least 2 s old."""
        return self._tracker.wait_warm()

    def observer_status(self) -> dict[str, Any]:
        """Observer start time and tracker counters, for `health_check`."""
        return {"observer_started_ns": self.observer_started_ns, **self._tracker.status()}

    @property
    def effective_mode(self) -> EffectiveMode:
        return "live"

    def is_available(self) -> bool:
        # __init__ only completes when DomainParticipant() succeeds.
        return True

    # ----- ROS2 surface: not served by this adapter -----

    def list_topics(self) -> list[TopicListItem]:
        raise AdapterError(DDS_ONLY_ERROR_MSG)

    def get_topic_info(self, topic: str) -> TopicInfo:
        raise AdapterError(DDS_ONLY_ERROR_MSG)

    def sample_messages(
        self,
        topic: str,
        count: int,
        *,
        max_array_length: int | None = DEFAULT_MAX_ARRAY_LENGTH,
        arrays_summary_only: bool = False,
        timeout_s: float = DEFAULT_SAMPLE_TIMEOUT_S,
    ) -> SampleResult:
        raise AdapterError(DDS_ONLY_ERROR_MSG)

    def analyze_bag(self, path: str) -> BagAnalysis:
        raise AdapterError(DDS_ONLY_ERROR_MSG)

    def peek_bag_samples(self, path: str, topic: str, count: int) -> SampleResult:
        raise AdapterError(DDS_ONLY_ERROR_MSG)

    @property
    def observed_domain_id(self) -> int:
        """The DDS domain joined at construction (the only one observed)."""
        return self._domain_id

    # ----- DDS surface -----

    def list_participants(self, domain_id: int = 0) -> list[ParticipantInfo]:
        """List participants from the `LifecycleBuffer` the tracker keeps current.

        `domain_id` exists for protocol uniformity: the adapter reports the
        domain it joined at construction.

        There is no reconcile here. The tracker records losses from dispose
        samples, and reconciling against the cache raced with a pass (a
        participant recorded but not yet cached read as lost).
        """
        observer = self._observer_guid()
        return [
            p.model_copy(update={"is_observer": True}) if p.guid == observer else p
            for p in self._caches.snapshot(self._domain_id).participant_infos
        ]

    def detect_qos_mismatches(self, topic: str | None = None) -> MismatchScan:
        """Pair cached readers and writers per topic and scan them.

        Builds `EndpointInfo` records from the tracker caches and hands them
        to `common.qos_scan.scan_endpoints`.
        """
        snap = self._caches.snapshot(self._domain_id)
        parts, pubs, subs = snap.participants, snap.publications, snap.subscriptions
        endpoints = endpoint_infos_from_samples(
            parts,
            pubs,
            subs,
            observer_guid=self._observer_guid(),
        )
        hostnames = {p.guid: p.hostname for p in snap.participant_infos}
        return scan_endpoints(endpoints, topic=topic, mode_effective="live", hostnames=hostnames)

    def _observer_guid(self) -> str:
        """Formatted GUID of this adapter's own participant."""
        return self._observer  # read at startup, so no binding call on the handler thread

    def list_endpoints(
        self,
        topic: str | None = None,
        participant_guid: str | None = None,
        include_observer: bool = False,
        include_departed: bool = False,
        include_internal: bool = False,
    ) -> EndpointListing:
        """List discovered writers and readers, with participant names.

        A discovery fact, not data flow: it shows what endpoints announced,
        not whether samples move. Assembly is in
        `common.endpoints.build_endpoint_listing`.
        """
        snap = self._caches.snapshot(self._domain_id)
        parts, pubs, subs = snap.participants, snap.publications, snap.subscriptions
        return listing_from_samples(
            parts,
            pubs,
            subs,
            domain_id=self._domain_id,
            mode_effective="live",
            observer_guid=self._observer_guid(),
            topic=topic,
            participant_guid=participant_guid,
            include_observer=include_observer,
            include_departed=include_departed,
            include_internal=include_internal,
            departed=[(r.role, r.sample, r.gone_ns, r.participant_name) for _, r in snap.departed],
        )

    def peek_dds_samples(self, topic: str, count: int) -> SampleResult:
        """Peek samples on a builtin DCPS topic or a user topic.

        A user topic announced on the bus yields no samples and a `note`
        (decoding is disabled) and records nothing for `topic_metrics`.
        Raises `AdapterError` when no endpoint claims the topic.
        """
        if count < 0:
            raise AdapterError("count must be >= 0")

        if topic in _BUILTIN_DCPS_TOPICS:
            return self._peek_builtin(topic, count)

        return self._peek_user_topic(topic, count)

    def _peek_builtin(self, topic: str, count: int) -> SampleResult:
        """Builtin DCPS topic peek: the cached current discovery state, not a stream."""
        samples_raw = self._cache_for(topic).values(count)
        now_ns = time.time_ns()
        names = participant_names(self._caches.participants.values())
        observer = self._observer_guid()
        samples = [
            MessageSample(
                topic=topic,
                message_type=f"dds_builtin/{topic}",
                timestamp_ns=announced_ns_of(s) or 0,
                stamp_source="dds_source" if announced_ns_of(s) else "none",
                payload=builtin_payload(topic, s, names, observer),
            )
            for s in samples_raw
        ]
        # Builtin topics carry no sequence number or publish time, so only
        # frequency is tracked.
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

    def _cache_for(self, topic: str) -> SampleCache:
        """The tracker cache that backs one builtin DCPS topic."""
        if topic == "DCPSParticipant":
            return self._caches.participants
        if topic == "DCPSPublication":
            return self._caches.publications
        return self._caches.subscriptions

    def _peek_user_topic(self, topic: str, count: int) -> SampleResult:
        """User-topic peek: `AdapterError` if the topic is not on the bus, else an empty result.

        `_try_dynamic_decode_cyclone` returns `None`, so the decoded branch
        is unreachable and the result carries a note that decoding is
        disabled. Nothing is recorded into `MetricsBuffer`.
        """
        resolved, resolution_note = resolve_user_topic(
            topic, self._discovered_topic_names(), self._domain_id
        )

        decoded = _try_dynamic_decode_cyclone(self._dp, resolved, count)
        if decoded is not None:
            # Unreachable while dynamic decode is disabled.
            now_ns = time.time_ns()
            for sample in decoded:
                self._metrics.record(
                    topic=resolved,
                    receive_ns=now_ns,
                    sequence_number=_extract_seq_from_payload(sample.payload),
                    publish_ns=_extract_publish_ns_from_payload(sample.payload),
                    domain_id=self._domain_id,
                )
            return SampleResult(
                topic=topic,
                count=len(decoded),
                samples=decoded,
                mode_effective="live",
                note=resolution_note,
            )

        # Nothing was received, so nothing is recorded into the metrics buffer.
        return user_topic_result(topic, "live", resolution_note)

    def _discovered_topic_names(self) -> set[str | None]:
        """Topic names of every discovered reader and writer."""
        endpoints = self._caches.subscriptions.values() + self._caches.publications.values()
        return {_extract_topic_name(sample) for sample in endpoints}

    def participant_events(
        self, domain_id: int = 0, lookback_s: int = 300
    ) -> list[ParticipantEvent]:
        """Lifecycle events for the joined domain within the window.

        The tracker thread keeps the log current, with DDS-derived timestamps
        (`time_source`).
        """
        if lookback_s < 1 or lookback_s > 86400:
            raise AdapterError(f"lookback_s must be in 1..86400, got {lookback_s}")
        return self._lifecycle.events_since(
            lookback_s=lookback_s,
            domain_id=self._domain_id,
        )

    def topic_metrics(self, topic: str, window_s: int = 60, domain_id: int = 0) -> TopicMetrics:
        """Metrics from the buffer that `peek_dds_samples` fills.

        There is no per-sample callback in cyclonedds Python, so a topic not
        peeked recently has `samples_observed=0`, and user topics stay empty.
        """
        if window_s < 1 or window_s > 3600:
            raise AdapterError(f"window_s must be in 1..3600, got {window_s}")
        metrics = self._metrics.compute_metrics(
            topic=topic,
            window_s=window_s,
            domain_id=self._domain_id,
            declared_hz=self._declared_hz(topic),
            mode_effective="live",
        )
        return metrics.model_copy(
            update={"status": metrics_status(topic, metrics.samples_observed)}
        )

    def _declared_hz(self, topic: str) -> float | None:
        """Rate implied by the shortest writer Deadline announced on `topic`, else `None`."""
        writers = [s for s in self._caches.publications.values() if _extract_topic_name(s) == topic]
        if not writers:
            return None
        infos = endpoint_infos_from_samples(
            [],
            writers,
            [],
            observer_guid=None,
        )
        return declared_hz_from_endpoints(infos, topic)
