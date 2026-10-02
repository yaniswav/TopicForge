"""Cyclone DDS adapter: real implementation (v0.3.0+).

Joins the bus as a read-only DDS-RTPS participant on the configured
domain via the `cyclonedds.builtin` builtin data readers, and observes
every conformant vendor on the wire: see `docs/dds-interop-matrix.md`
for the canonical multi-vendor positioning.

The DDS / observability methods call into the CycloneDDS Python
bindings ; the ROS2 graph methods raise `AdapterError(DDS_ONLY_ERROR_MSG)`
(this adapter is DDS-only; pair with `Ros2CliAdapter` via the
v0.4.0 `CompositeAdapter` to get both surfaces simultaneously). The
factory only loads this module when `TOPICFORGE_DDS_BACKEND=cyclone`
(or `auto` resolving to cyclone): see `services/factory.py`.

Current scope (v0.4.0+):

Discovery is tracked continuously: a daemon `DiscoveryTracker` thread is the
ONLY code that touches the three builtin readers (it `take()`s, which would
hide samples from any other reader), and folds what it takes into the caches
of `common/discovery_tracker.py`. Every tool below reads those caches, so no
handler does DDS reads and the lifecycle does not depend on tool calls.

* `list_participants`: the `LifecycleBuffer` (first/last seen, status,
  seen_count, announced_ns, lost_ns).
* `detect_qos_mismatches`: cached DCPSSubscription + DCPSPublication paired
  by topic, run through the vendor-neutral pure analyzer in
  `adapters/common/qos_analyzer.py`.
* `peek_dds_samples`: structured payloads on the 3 builtin DCPS topics
  (DCPSParticipant, DCPSSubscription, DCPSPublication), served from the
  caches: the current discovery state, not a stream. Arbitrary user
  topics go through `_peek_user_topic`, which confirms the topic is on the
  bus and returns one annotated placeholder (`_decode_status="raw"`, empty
  bytes). Dynamic XTypes decode of user-topic payloads is DISABLED in this
  release pending real-bus validation: see `_try_dynamic_decode_cyclone`.
  The placeholder is not a received sample and never feeds `topic_metrics`.
* `participant_events`: `discovered` / `lost` events from the
  `LifecycleBuffer`, timed by the DDS source timestamps of the discovery
  samples. Caveat: a participant cycle faster than the builtin reader's
  history depth between two tracker passes can still be missed.
* `topic_metrics`: opportunistic frequency / sequence-gap / latency
  metrics buffered as `peek_dds_samples` surfaces samples of the builtin
  DCPS topics (no native at-sample-receive callback in cyclonedds 2.6.x
  Python). User topics currently surface no sample, so they stay at
  `samples_observed=0`.

Sample-introspection helpers below are defensive against binding-version
shape variations: they read attributes via `getattr` with fallbacks and
collapse missing data to `None` / `"unknown"` rather than raising. A
single odd discovery sample must not break the whole tool call.
"""

from __future__ import annotations

import logging
import time
from itertools import islice
from typing import Any

# Top-level imports: the factory only loads this module when the
# cyclonedds bindings are importable. ImportError here propagates to
# the factory which falls back to mock with a logged warning.
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
    format_guid,
    format_participant_key,
    iter_field_names,
    listing_from_samples,
    metrics_status,
    participant_names,
    scan_endpoints,
    take_bounded,
    user_topic_result,
    validate_domain_id,
)
from topicforge.adapters.common import (
    cyclone_extract_guid as _extract_guid,
)
from topicforge.adapters.common import (
    cyclone_extract_topic_name as _extract_topic_name,
)
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

# v0.4.0 Phase 3: the 6 helpers below were extracted into
# `adapters/common/cdr_decoder.py` so the same dynamic-type decode logic
# powers both live Cyclone XTypes samples (Phase 1.5) and recorded
# bag samples (Phase 3 `services/bag_service.py`). The `_underscore`
# aliases stay in this module so the pre-Phase-3 call sites
# (_try_dynamic_decode_cyclone, etc.) keep working without rewrites.
_decode_dynamic_sample = decode_dynamic_sample
_iter_field_names = iter_field_names
_decode_field_value = decode_field_value
_dynamic_type_name = dynamic_type_name
_extract_seq_from_payload = extract_seq_from_payload
_extract_publish_ns_from_payload = extract_publish_ns_from_payload

log = logging.getLogger(__name__)

# Tunables: kept module-level so a future env-var hook is a one-line
# change. The builtin readers are drained by the tracker thread alone, with
# `take()`; the rest of the adapter reads the tracker's caches.
# `read_iter(timeout=...)` (used by the unreachable dynamic-decode code)
# resets its timeout on every received sample, so it never ends on a topic
# publishing faster than the timeout: bound it with `take_bounded`.
_DISCOVERY_TIMEOUT_SEC = 2.0
_SAMPLE_TIMEOUT_SEC = 1.0
_MAX_PARTICIPANTS = 256
_MAX_ENDPOINTS = 1024

# Builtin DCPS topics that `peek_dds_samples` serves with structured
# payloads. Arbitrary user topics route through `_peek_user_topic`, which
# returns an annotated placeholder (dynamic decode is disabled).
_BUILTIN_DCPS_TOPICS: dict[str, Any] = {
    "DCPSParticipant": BuiltinTopicDcpsParticipant,
    "DCPSSubscription": BuiltinTopicDcpsSubscription,
    "DCPSPublication": BuiltinTopicDcpsPublication,
}


def _try_dynamic_decode_cyclone(dp: Any, topic: str, count: int) -> list[MessageSample] | None:
    """Dynamic XTypes decode of a user topic: DISABLED, always returns `None`.

    The pipeline in `_decode_dynamic_unvalidated` has never run against a
    real bus, and reading the cyclonedds 11.0.1 binding shows it cannot work
    as written. Rather than ship a repair that has never executed, this
    release returns `None` immediately, before any type resolution, so the
    caller reports an empty result with a note.

    Known defects, for the future rewrite (validate on `scripts/integration/`):

      1. Arity: `cyclonedds.dynamic.get_types_for_typeid(participant,
         type_id, timeout)` takes three arguments; the old code called
         `type_resolver(type_id)` with one (TypeError, swallowed at DEBUG).
         It also needs Cyclone built with `ENABLE_TYPE_DISCOVERY`.
      2. Tuple not unpacked: that function returns `(type, nested_types)`;
         the old code passed the whole tuple to `Topic(dp, name, data_type)`,
         which requires an IDL type (TypeError, swallowed at DEBUG).
      3. Unbounded read: `read_iter(timeout=...)` resets its timeout on
         every received sample, so `list(reader.read_iter(...))[:count]`
         never returns on a topic publishing faster than the timeout.
         Bound it with `take_bounded` / `islice` instead.
    """
    return None


def _decode_dynamic_unvalidated(  # pragma: no cover: unreachable, never validated
    dp: Any, topic: str, count: int
) -> list[MessageSample] | None:
    """Former dynamic decode pipeline, kept unreachable for the rewrite.

    NOT wired to anything and NOT counted as tested: see the defect list in
    `_try_dynamic_decode_cyclone`. Do not call it before those are fixed.

    Intended steps: discover a type id via DCPSPublication, resolve the
    TypeObject, build a typed Topic + DataReader, read up to `count` samples
    and decode them field by field into `annotate_partial` / `annotate_raw`
    payloads.
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
                payload=payload,
            )
        )
    return samples


def _find_dynamic_resolver(cyclone_dynamic: Any) -> Any | None:  # pragma: no cover
    """Return the first callable resolver on `cyclonedds.dynamic`.

    Only used by the unreachable `_decode_dynamic_unvalidated`.

    The dynamic-IDL entry point has been renamed across CycloneDDS
    Python binding versions. We probe the cited names in order and
    return the first attribute that is callable.
    """
    for attr in ("get_types_for_typeid", "get_type_for_endpoint", "get_type"):
        candidate = getattr(cyclone_dynamic, attr, None)
        if callable(candidate):
            return candidate
    return None


def _discover_type_id_for_topic(dp: Any, topic: str) -> Any | None:  # pragma: no cover
    """Pull a type identifier off the first DCPSPublication sample for `topic`.

    Only used by the unreachable `_decode_dynamic_unvalidated`. Reads at most
    `_MAX_ENDPOINTS` samples.

    Returns `None` when no publication for `topic` is in the discovery
    cache, or when the binding does not expose a `type_id` / `type_info`
    attribute on the sample.
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
    """Build a typed reader against the resolved `type_object` and read samples.

    Only used by the unreachable `_decode_dynamic_unvalidated`. Returns a
    list of at most `count` raw sample objects (the binding's typed
    representation) on success, `None` when the typed reader could not be
    constructed. `count` is the hard bound: `_SAMPLE_TIMEOUT_SEC` is only the
    per-sample wait, because `read_iter` resets it on every received sample.
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


# The 6 dynamic-type decoders (decode_dynamic_sample, iter_field_names,
# decode_field_value, dynamic_type_name, extract_seq_from_payload,
# extract_publish_ns_from_payload) live in
# `topicforge.adapters.common.cdr_decoder` since v0.4.0 Phase 3 ; the
# `_underscore` aliases at the top of this module preserve the original
# Cyclone call sites without rewrites.


class CycloneDdsAdapter:
    """Read-only adapter backed by Eclipse CycloneDDS Python bindings."""

    name: AdapterName = "cyclone"

    def __init__(self, domain_id: int = 0) -> None:
        validate_domain_id(domain_id)
        self._domain_id = domain_id
        # Lifecycle buffer + endpoint caches, fed by the tracker thread below.
        self._caches = DiscoveryCaches()
        self._lifecycle = self._caches.lifecycle
        # v0.4.0 Phase 2: metrics buffer fed opportunistically by
        # `peek_dds_samples` flows. See `_peek_builtin` / `_peek_user_topic`.
        self._metrics = MetricsBuffer()
        try:
            # Announce ourselves by name, so that TopicForge's own read-only
            # participant is recognizable in every listing, ours included.
            self._dp = DomainParticipant(domain_id, qos=Qos(Policy.EntityName("topicforge")))
        except Exception as exc:  # binding-side errors vary by version
            raise AdapterError(
                f"Failed to create CycloneDDS DomainParticipant on domain {domain_id}: {exc}"
            ) from exc
        self.observer_started_ns = time.time_ns()
        # One reader per builtin discovery topic, kept for the adapter's
        # lifetime, and read by the tracker thread only.
        self._builtin: dict[Any, tuple[Any, Any]] = {}
        for topic_class in (
            BuiltinTopicDcpsParticipant,
            BuiltinTopicDcpsPublication,
            BuiltinTopicDcpsSubscription,
        ):
            self._builtin_reader(topic_class)
        self._tracker = DiscoveryTracker(self._take_all, self._caches, domain_id=domain_id)
        self._tracker.start()

    def _builtin_reader(self, topic_class: Any) -> tuple[Any, Any]:
        """The adapter's reader for one builtin topic, and an any-state read condition."""
        entry = self._builtin.get(topic_class)
        if entry is None:
            reader = BuiltinDataReader(self._dp, topic_class)
            # Every instance state: disposed and no-writers instances come back
            # as invalid samples (key + sample_info), which is how a leave is seen.
            condition = ReadCondition(reader, SampleState.Any | ViewState.Any | InstanceState.Any)
            entry = self._builtin[topic_class] = (reader, condition)
        return entry

    def _take_new(self, topic_class: Any, limit: int) -> list[Any]:
        """Take everything new from one builtin reader (tracker thread only)."""
        reader, condition = self._builtin_reader(topic_class)
        taken: list[Any] = []
        while True:
            batch = reader.take(N=limit, condition=condition)
            taken.extend(batch)
            if len(batch) < limit:
                return taken

    def _take_all(self) -> tuple[list[Any], list[Any], list[Any]]:
        """One tracker pass worth of raw samples: participants, publications, subscriptions."""
        return (
            self._take_new(BuiltinTopicDcpsParticipant, _MAX_PARTICIPANTS),
            self._take_new(BuiltinTopicDcpsPublication, _MAX_ENDPOINTS),
            self._take_new(BuiltinTopicDcpsSubscription, _MAX_ENDPOINTS),
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

    # ----- DDS surface (v0.3.0 real implementation) -----

    def list_participants(self, domain_id: int = 0) -> list[ParticipantInfo]:
        """Discover DDS participants via the builtin DCPSParticipant reader.

        The `domain_id` argument is accepted for protocol uniformity but
        the adapter only observes the domain it joined at construction
        time. Callers asking for a different domain receive what *this*
        participant sees: spinning up a second participant on the fly
        would violate the "one bus join per adapter instance" rule.

        Served from the `LifecycleBuffer` the tracker thread keeps current
        (first/last seen, status, seen_count, announced_ns, lost_ns). The
        reconcile below is a safety net only: the tracker already records
        every loss from the builtin readers' dispose samples.
        """
        self._lifecycle.reconcile(
            observed_guids={
                format_guid(_extract_guid(s)) for s in self._caches.participants.values()
            },
            domain_id=self._domain_id,
            mode_effective="live",
        )
        observer = self._observer_guid()
        return [
            p.model_copy(update={"is_observer": True}) if p.guid == observer else p
            for p in self._lifecycle.snapshot_participants(domain_id=self._domain_id)
        ]

    def detect_qos_mismatches(self, topic: str | None = None) -> MismatchScan:
        """Pair cached reader/writer endpoints per topic and scan them.

        Builds `EndpointInfo` records (with participant names) from the tracker's
        caches, then hands them to the pure `common.qos_scan.scan_endpoints`:
        partition and type separation first, RxO rules after.
        """
        parts, pubs, subs = self._raw_endpoint_samples()
        endpoints = endpoint_infos_from_samples(
            parts,
            pubs,
            subs,
            domain_id=self._domain_id,
            mode_effective="live",
            observer_guid=self._observer_guid(),
        )
        return scan_endpoints(endpoints, topic=topic, mode_effective="live")

    def _observer_guid(self) -> str:
        """Formatted GUID of this adapter's own participant."""
        return format_participant_key(self._dp.guid)

    def list_endpoints(
        self,
        topic: str | None = None,
        participant_guid: str | None = None,
        include_observer: bool = False,
    ) -> EndpointListing:
        """List every discovered writer and reader from the builtin discovery readers.

        Joins DCPSPublication / DCPSSubscription samples with the DCPSParticipant
        names. A discovery fact, not data flow: it says what endpoints announced,
        not whether samples move. The pure assembly lives in
        `common.endpoints.build_endpoint_listing`.
        """
        parts, pubs, subs = self._raw_endpoint_samples()
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
        )

    def _raw_endpoint_samples(self) -> tuple[list[Any], list[Any], list[Any]]:
        """Cached builtin samples (participants, publications, subscriptions).

        The only place `list_endpoints` gets its input: every other step is a
        pure function over these lists.
        """
        return (
            self._caches.participants.values(),
            self._caches.publications.values(),
            self._caches.subscriptions.values(),
        )

    def peek_dds_samples(self, topic: str, count: int) -> SampleResult:
        """Peek recent samples on a DDS topic.

        The 3 builtin DCPS topics keep their v0.3.0 structured-payload
        shape. For a user topic, dynamic decode is disabled in this
        release: a topic announced on the bus yields no samples and a
        `note` saying so. Nothing is recorded for `topic_metrics`.

        Raises `AdapterError` only when the topic has not been
        discovered on the bus (no endpoint claims it).
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
                payload=builtin_payload(topic, s, names, observer),
            )
            for s in samples_raw
        ]
        # v0.4.0 Phase 2: opportunistic metrics fill. Builtin topics
        # do not carry application-level seq# or publish_ns, so both
        # are recorded as None ; the metrics buffer still tracks
        # frequency on them.
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
        """User-topic peek: topic presence plus an annotated placeholder.

        1. Confirm the topic is announced on the bus (subscription or
           publication present). If not -> `AdapterError`.
        2. `_try_dynamic_decode_cyclone` is disabled and returns `None`, so
           the placeholder path below is the one that runs. The decoded
           branch is kept for the future rewrite and is currently
           unreachable.
        3. The result is empty (count 0) with a `note` saying decoding is
           disabled; nothing is recorded into `MetricsBuffer`.
        """
        if not self._is_topic_on_bus(topic):
            raise AdapterError(
                f"DDS topic {topic!r} not discovered on domain {self._domain_id}. "
                "Confirm a publisher is alive and reachable, or call "
                "list_participants / detect_qos_mismatches first to inspect "
                "current bus state."
            )

        decoded = _try_dynamic_decode_cyclone(self._dp, topic, count)
        if decoded is not None:
            # Unreachable while dynamic decode is disabled. Metrics fill
            # on the decoded user-topic path. Pull seq# and publish_ns from
            # the decoded payload when available: best-effort.
            now_ns = time.time_ns()
            for sample in decoded:
                self._metrics.record(
                    topic=topic,
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
            )

        # Nothing was received, so nothing is recorded into the metrics buffer.
        return user_topic_result(topic, "live")

    def _is_topic_on_bus(self, topic: str) -> bool:
        """True iff a sub or pub for `topic` has been discovered."""
        endpoints = self._caches.subscriptions.values() + self._caches.publications.values()
        return any(_extract_topic_name(sample) == topic for sample in endpoints)

    def participant_events(
        self, domain_id: int = 0, lookback_seconds: int = 300
    ) -> list[ParticipantEvent]:
        """Return lifecycle events for `self._domain_id` within the window.

        The log is kept current by the tracker thread, independently of tool
        calls, with DDS-derived timestamps (`time_source`).
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

        The buffer fills opportunistically via `peek_dds_samples`
        calls on the builtin DCPS topics (no per-sample callback in
        cyclonedds Python). User topics stay empty. A topic
        that has not been peeked recently returns
        `samples_observed=0`. The tool description surfaces this
        caveat to LLM callers.
        """
        if window_seconds < 1 or window_seconds > 3600:
            raise AdapterError(f"window_seconds must be in 1..3600, got {window_seconds}")
        metrics = self._metrics.compute_metrics(
            topic=topic,
            window_seconds=window_seconds,
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
            domain_id=self._domain_id,
            mode_effective="live",
            observer_guid=None,
        )
        return declared_hz_from_endpoints(infos, topic)


# Sample-introspection helpers (_extract_guid / _extract_vendor_id /
# _extract_hostname / _extract_topic_name) and the QoS normalizer
# (_cyclone_qos_to_profile) were moved to the binding-free
# `topicforge.adapters.common.dds_introspection` /
# `.qos_normalize` modules (Lot 0, audit 2026-07-08) so they are
# unit-testable without the cyclonedds bindings installed. They are
# imported and aliased back to their original names at the top of this
# module, so the call sites above are unchanged.
