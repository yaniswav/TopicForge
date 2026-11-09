"""Cyclone DDS adapter.

Joins the bus as a read-only DDS-RTPS participant through the
`cyclonedds.builtin` readers and serves the discovery tools from them. A
daemon `DiscoveryTracker` thread is the only code that takes from the three
builtin readers and fills the caches of `common/discovery_tracker.py`; tool
handlers read those caches, so lifecycle does not depend on tool calls. Every
binding call goes through one process-wide lock, because the Python binding
is not thread-safe. User-topic payloads are not decoded (`topic_metrics` has
data only for builtin topics) unless `TOPICFORGE_DDS_DECODE=on`, where a bounded
read on a separate participant serves them (`user_read.py`). ROS2 graph methods raise
`AdapterError(DDS_ONLY_ERROR_MSG)`; `CompositeAdapter` pairs it with
`Ros2CliAdapter`. Bag methods read the file with `rosbags`, which needs no ROS 2.
"""

from __future__ import annotations

import atexit
import logging
import threading
import time
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

from topicforge import budget
from topicforge.adapters.base import AdapterError, AdapterName, EffectiveMode
from topicforge.adapters.common import (
    DDS_ONLY_ERROR_MSG,
    BufferedTake,
    DiscoveryCaches,
    DiscoveryTracker,
    MetricsBuffer,
    SampleCache,
    announced_ns_of,
    bag_read,
    builtin_payload,
    declared_hz_from_endpoints,
    endpoint_infos_from_samples,
    format_participant_key,
    listing_from_samples,
    metrics_status,
    participant_names,
    resolve_user_topic,
    scan_endpoints,
    user_topic_result,
    validate_domain_id,
    with_endpoint_counts,
)
from topicforge.adapters.common import (
    cyclone_extract_topic_name as _extract_topic_name,
)
from topicforge.adapters.common.dds_helpers import BUILTIN_DCPS_TOPICS, format_guid
from topicforge.adapters.common.dds_introspection import cyclone_extract_guid, cyclone_extract_pid
from topicforge.adapters.common.lifecycle import with_event_node_names
from topicforge.adapters.common.log_filter import LogFilter
from topicforge.adapters.common.ros_decode import (
    METRICS_MAX_SAMPLES,
    METRICS_READ_S,
    PEEK_READ_S,
    DecodePlan,
    DecodeRefused,
    ReadOutcome,
    cap_decoded,
    fitting_reads,
    message_samples,
    plan_decode,
    rate_metrics,
    read_note,
    writer_facts,
)
from topicforge.adapters.common.ros_discovery import with_node_names
from topicforge.adapters.common.topic_filter import resolve_topic_filter
from topicforge.adapters.dds_cyclone.ros_info import make_ros_info_reader, take_raw
from topicforge.adapters.dds_cyclone.user_read import read_user_topic
from topicforge.constants import DEFAULT_MAX_ARRAY_LENGTH, DEFAULT_SAMPLE_TIMEOUT_S
from topicforge.models import (
    BagAnalysis,
    EndpointListing,
    MessageSample,
    MismatchScan,
    NodeInfo,
    NodeListing,
    ParticipantEvent,
    ParticipantInfo,
    SampleResult,
    TfCapture,
    TopicInfo,
    TopicListItem,
    TopicMetrics,
)
from topicforge.models.image import CapturedImage

log = logging.getLogger(__name__)

_MAX_PARTICIPANTS = 256
_READ_RESERVE_S = 2.0
_MAX_ENDPOINTS = 1024

# Builtin topics `peek_dds_samples` serves with structured payloads.
_BUILTIN_DCPS_TOPICS: dict[str, Any] = {
    "DCPSParticipant": BuiltinTopicDcpsParticipant,
    "DCPSSubscription": BuiltinTopicDcpsSubscription,
    "DCPSPublication": BuiltinTopicDcpsPublication,
}


# The cyclonedds binding is not thread-safe when converting QoS: two threads
# inside take() at once (two adapters in one process) corrupted the heap on
# Windows (0xc0000374). Every binding call goes through this lock.
_BINDING_LOCK = threading.RLock()


class CycloneDdsAdapter:
    """Read-only adapter backed by Eclipse CycloneDDS Python bindings."""

    name: AdapterName = "cyclone"

    def __init__(self, domain_id: int = 0, *, decode: bool = False) -> None:
        validate_domain_id(domain_id)
        self._domain_id = domain_id
        self._decode = decode
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
            self._ros_info = self._open_ros_info_reader()
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

    def _open_ros_info_reader(self) -> Any | None:
        """The `ros_discovery_info` reader (node names), `None` if the binding refuses it."""
        try:
            return make_ros_info_reader(self._dp)
        except Exception:
            log.warning("ros_discovery_info reader unavailable: no node names", exc_info=True)
            return None

    def _take_ros_info(self, sink: list[Any]) -> None:
        """Take every new `ros_discovery_info` payload into `sink` (tracker thread only)."""
        reader = self._ros_info
        if reader is None:
            return
        while True:
            with _BINDING_LOCK:
                batch = take_raw(reader, _MAX_PARTICIPANTS)
            sink.extend(batch)
            if len(batch) < _MAX_PARTICIPANTS:
                return

    def _build_take_all(self) -> BufferedTake:
        """The tracker's take function: participants, publications, subscriptions, node info."""
        return BufferedTake(
            lambda sink: self._take_new(BuiltinTopicDcpsParticipant, _MAX_PARTICIPANTS, sink),
            lambda sink: self._take_new(BuiltinTopicDcpsPublication, _MAX_ENDPOINTS, sink),
            lambda sink: self._take_new(BuiltinTopicDcpsSubscription, _MAX_ENDPOINTS, sink),
            self._take_ros_info if self._ros_info is not None else None,
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

    def list_nodes(self) -> NodeListing:
        raise AdapterError(DDS_ONLY_ERROR_MSG)

    def get_node_info(self, node: str, timeout_s: float = 8.0) -> NodeInfo:
        raise AdapterError(DDS_ONLY_ERROR_MSG)

    def capture_tf(self, window_s: float) -> TfCapture:
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
        min_log_level: str | None = None,
        node: str | None = None,
        window_s: float | None = None,
    ) -> SampleResult:
        raise AdapterError(DDS_ONLY_ERROR_MSG)

    def get_image(
        self, topic: str, *, max_edge: int, max_bytes: int, timeout_s: float
    ) -> CapturedImage:
        raise AdapterError(DDS_ONLY_ERROR_MSG)

    def analyze_bag(self, path: str) -> BagAnalysis:
        """Read the bag with `rosbags`: it needs no ROS 2 install (`reader="rosbags"`)."""
        return bag_read.analyze_bag_file(path)

    def peek_bag_samples(
        self,
        path: str,
        topic: str,
        count: int,
        *,
        start_s: float | None = None,
        end_s: float | None = None,
        min_log_level: str | None = None,
        node: str | None = None,
    ) -> SampleResult:
        """Decode samples from the bag with `rosbags`; no ROS 2 install is needed."""
        return bag_read.peek_bag_samples(
            path,
            topic,
            count,
            start_s=start_s,
            end_s=end_s,
            log_filter=LogFilter.of(min_log_level, node),
        )

    @property
    def dds_decode_enabled(self) -> bool:
        """Whether user-topic payloads are decoded (`TOPICFORGE_DDS_DECODE=on`)."""
        return self._decode

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
        snap = self._caches.snapshot(self._domain_id)
        infos = [
            p.model_copy(update={"is_observer": True}) if p.guid == observer else p
            for p in snap.participant_infos
        ]
        named = with_node_names(infos, snap.node_names, snap.node_names_last)
        return with_endpoint_counts(named, snap.publications, snap.subscriptions)

    def participant_pids(self) -> dict[str, int]:
        """Pid announced by each cached participant (`__Pid` property), keyed by guid."""
        pids = {}
        for sample in self._caches.snapshot(self._domain_id).participants:
            pid = cyclone_extract_pid(sample)
            if pid is not None:
                pids[format_guid(cyclone_extract_guid(sample))] = pid
        return pids

    def detect_qos_mismatches(self, topic: str | None = None) -> MismatchScan:
        """Pair cached readers and writers per topic and scan them.

        Builds `EndpointInfo` records from the tracker caches and hands them
        to `common.qos_scan.scan_endpoints`.
        """
        snap = self._caches.snapshot(self._domain_id)
        endpoints = snap.endpoint_infos(self._observer_guid())
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
            nodes_by_guid=snap.endpoint_nodes,
            departed=[(r.role, r.sample, r.gone_ns, r.participant_name) for _, r in snap.departed],
        )

    def peek_dds_samples(self, topic: str, count: int) -> SampleResult:
        """Peek samples on a builtin DCPS topic or a user topic.

        A user topic announced on the bus yields no samples and a `note`, or
        decoded samples with `TOPICFORGE_DDS_DECODE=on`, and records nothing for
        `topic_metrics`.
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
        """User-topic peek: `AdapterError` if the topic is not on the bus, else see below.

        Without `TOPICFORGE_DDS_DECODE=on`, an empty result with a note. Nothing is ever
        recorded into `MetricsBuffer`: the buffer is the builtin topics' peek cadence.
        """
        resolved, resolution_note = resolve_user_topic(
            topic, self._discovered_topic_names(), self._domain_id
        )
        if not self._decode:
            return user_topic_result(topic, "live", resolution_note)
        try:
            plan = self._plan_decode(resolved)
            return self._peek_decoded(topic, plan, count, resolution_note)
        except DecodeRefused as exc:
            return user_topic_result(topic, "live", resolution_note, refused=str(exc))

    def _peek_decoded(
        self, topic: str, plan: DecodePlan, count: int, resolution_note: str | None
    ) -> SampleResult:
        """Read and decode up to `count` samples; `count` 0 creates no reader."""
        if count == 0:
            return SampleResult(
                topic=topic, count=0, samples=[], mode_effective="live", note=resolution_note
            )
        outcome = self._read(plan, count, PEEK_READ_S)
        reads, oversize_note = fitting_reads(outcome)
        kept, cap_note = cap_decoded(message_samples(topic, plan, reads))
        notes = (
            f"{resolution_note}." if resolution_note else None,
            read_note(outcome, count),
            oversize_note,
            cap_note,
            plan.store_note,
        )
        return SampleResult(
            topic=topic,
            count=len(kept),
            samples=kept,
            mode_effective="live",
            note=" ".join(n for n in notes if n) or None,
        )

    def _plan_decode(self, dds_topic: str) -> DecodePlan:
        """Clear a discovered topic for decoding from its live writers, or `DecodeRefused`."""
        writers = [
            writer_facts(s)
            for s in self._caches.publications.values()
            if _extract_topic_name(s) == dds_topic
        ]
        return plan_decode(dds_topic, writers)

    def _read(
        self, plan: DecodePlan, max_samples: int, seconds: float, *, for_rate: bool = False
    ) -> ReadOutcome:
        """The bounded read, inside the guard's budget; any failure becomes a refusal."""
        left = budget.clamp(seconds, reserve_s=_READ_RESERVE_S)
        if left <= 0:
            raise DecodeRefused("the call's time budget is spent")
        try:
            return read_user_topic(
                self._domain_id,
                plan,
                max_samples=max_samples,
                budget_s=left,
                lock=_BINDING_LOCK,
                own=self._caches.own_participants.add,
                keep_payload=not for_rate,
                volatile=for_rate,
            )
        except DecodeRefused:
            raise
        except Exception as exc:  # binding-side errors vary by version
            log.warning("user-topic read failed on %s", plan.dds_topic, exc_info=True)
            raise DecodeRefused(f"the read failed: {exc}") from exc

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
        events = self._lifecycle.events_since(
            lookback_s=lookback_s,
            domain_id=self._domain_id,
        )
        return with_event_node_names(events, self.list_participants(self._domain_id))

    def topic_metrics(self, topic: str, window_s: int = 60, domain_id: int = 0) -> TopicMetrics:
        """Metrics from the buffer that `peek_dds_samples` fills.

        There is no per-sample callback in cyclonedds Python, so a topic not
        peeked recently has `samples_observed=0`, and user topics stay empty.
        With `TOPICFORGE_DDS_DECODE=on` a user topic gets its own bounded read.
        """
        if window_s < 1 or window_s > 3600:
            raise AdapterError(f"window_s must be in 1..3600, got {window_s}")
        if self._decode and topic not in BUILTIN_DCPS_TOPICS:
            return self._rate_of_user_topic(topic, window_s)
        metrics = self._metrics.compute_metrics(
            topic=topic,
            window_s=window_s,
            domain_id=self._domain_id,
            declared_hz=self._declared_hz(topic),
            mode_effective="live",
        )
        update: dict[str, Any] = {"status": metrics_status(topic, metrics.samples_observed)}
        if self._decode:
            update["frequency_basis"] = "peek_cadence"
        return metrics.model_copy(update=update)

    def _rate_of_user_topic(self, topic: str, window_s: int) -> TopicMetrics:
        """Publish rate from a bounded read; `unsupported_user_topic` and the reason if refused."""
        resolved, _ = resolve_topic_filter(topic, self._discovered_topic_names())
        declared = self._declared_hz(topic if resolved is None else resolved)
        try:
            if resolved is None:
                raise DecodeRefused("no endpoint is discovered on this topic")
            outcome = self._read(
                self._plan_decode(resolved),
                METRICS_MAX_SAMPLES,
                min(float(window_s), METRICS_READ_S),
                for_rate=True,
            )
        except DecodeRefused as exc:
            return TopicMetrics(
                topic=topic,
                window_s=window_s,
                window_actual_s=0.0,
                samples_observed=0,
                declared_frequency_hz=declared,
                status="unsupported_user_topic",
                mode_effective="live",
                note=f"Not read: {exc}.",
            )
        return rate_metrics(topic, window_s, outcome, declared_hz=declared)

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
