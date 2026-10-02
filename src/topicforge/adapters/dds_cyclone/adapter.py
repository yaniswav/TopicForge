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

* `list_participants`: DCPSParticipant discovery via builtin reader,
  enriched with `LifecycleBuffer` reconciliation (first/last seen,
  status, seen_count).
* `detect_qos_mismatches`: DCPSSubscription + DCPSPublication paired by
  topic, run through the vendor-neutral pure analyzer in
  `adapters/common/qos_analyzer.py`.
* `peek_dds_samples`: structured payloads on the 3 builtin DCPS topics
  (DCPSParticipant, DCPSSubscription, DCPSPublication). Arbitrary user
  topics go through `_peek_user_topic`, which confirms the topic is on the
  bus and returns one annotated placeholder (`_decode_status="raw"`, empty
  bytes). Dynamic XTypes decode of user-topic payloads is DISABLED in this
  release pending real-bus validation: see `_try_dynamic_decode_cyclone`.
  The placeholder is not a received sample and never feeds `topic_metrics`.
* `participant_events`: `discovered` / `lost` events from the
  `LifecycleBuffer`. Caveat : Cyclone updates the buffer only on
  `list_participants` poll calls (no native at-discovery callbacks).
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
    DYNAMIC_DECODE_DISABLED_NOTE,
    LifecycleBuffer,
    MetricsBuffer,
    canonicalize_vendor_id,
    decode_dynamic_sample,
    decode_field_value,
    detect_mismatches_across_endpoints,
    dynamic_type_name,
    extract_publish_ns_from_payload,
    extract_seq_from_payload,
    format_guid,
    iter_field_names,
    take_bounded,
    user_topic_placeholder,
    validate_domain_id,
)
from topicforge.adapters.common import (
    cyclone_extract_guid as _extract_guid,
)
from topicforge.adapters.common import (
    cyclone_extract_hostname as _extract_hostname,
)
from topicforge.adapters.common import (
    cyclone_extract_participant_name as _extract_participant_name,
)
from topicforge.adapters.common import (
    cyclone_extract_topic_name as _extract_topic_name,
)
from topicforge.adapters.common import (
    cyclone_extract_type_name as _extract_type_name,
)
from topicforge.adapters.common import (
    cyclone_extract_vendor_id as _extract_vendor_id,
)
from topicforge.adapters.common import (
    cyclone_qos_to_profile as _cyclone_qos_to_profile,
)
from topicforge.adapters.common import (
    is_alive_sample as _is_alive,
)
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
# change. Discovery + sample reads use `read_iter` (non-destructive) rather
# than `take_iter`, so observing the builtin discovery topics does not drain
# the reader cache and cause spurious lost / re-discovered participant
# flapping across polls. (Audit P1-5; the read-vs-take semantics on a real
# bus must be confirmed on the `scripts/integration/` rig before this ships.)
# `read_iter(timeout=...)` resets its timeout on every received sample, so it
# never ends on a topic publishing faster than the timeout: every call site
# bounds the iterator with `take_bounded` (itertools.islice) instead of
# materializing it and truncating afterwards.
_DISCOVERY_TIMEOUT_SEC = 2.0
_SAMPLE_TIMEOUT_SEC = 1.0
_MAX_PARTICIPANTS = 256
_MAX_ENDPOINTS = 1024
# Discovery needs a moment after our participant joins before the first
# snapshot is complete: SPDP/SEDP exchanges take a few hundred ms locally.
_DISCOVERY_WARMUP_SEC = 2.0

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
    caller surfaces the annotated placeholder (`DYNAMIC_DECODE_DISABLED_NOTE`).

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
        # v0.4.0 Phase 1: lifecycle tracking. Cyclone uses polling-delta
        # reconciliation: see `list_participants` for the feed pattern.
        self._lifecycle = LifecycleBuffer()
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
        self._joined_at = time.monotonic()
        # One reader per builtin discovery topic, kept for the adapter's
        # lifetime. Creating one per call leaked a DDS reader per tool call.
        self._builtin: dict[Any, tuple[Any, Any]] = {}
        for topic_class in (
            BuiltinTopicDcpsParticipant,
            BuiltinTopicDcpsPublication,
            BuiltinTopicDcpsSubscription,
        ):
            self._builtin_reader(topic_class)

    def _builtin_reader(self, topic_class: Any) -> tuple[Any, Any]:
        """The adapter's reader for one builtin topic, and an any-state read condition."""
        entry = self._builtin.get(topic_class)
        if entry is None:
            reader = BuiltinDataReader(self._dp, topic_class)
            # Alive instances only, in any read state: the limit then applies
            # to live entries, so departed ones can never crowd them out.
            condition = ReadCondition(reader, SampleState.Any | ViewState.Any | InstanceState.Alive)
            entry = self._builtin[topic_class] = (reader, condition)
        return entry

    def _discovery_snapshot(self, topic_class: Any, limit: int) -> list[Any]:
        """Every live entry the discovery cache holds for one builtin topic.

        Non-blocking, and blind to the read/unread state, so each call sees
        the whole current cache rather than only what arrived since the
        previous call. Disposed entries (participants or endpoints that are
        gone) are filtered out.
        """
        # Paid once, by a call made within 2 s of startup. It blocks the MCP
        # event loop like every handler does today (handlers are synchronous).
        wait = _DISCOVERY_WARMUP_SEC - (time.monotonic() - self._joined_at)
        if wait > 0:
            time.sleep(wait)
        reader, condition = self._builtin_reader(topic_class)
        return [s for s in reader.read(N=limit, condition=condition) if _is_alive(s)]

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

        v0.4.0 Phase 1: each call feeds the `LifecycleBuffer`. GUIDs seen
        in this snapshot are recorded as `seen` ; previously-known GUIDs
        absent from the snapshot are reconciled to `lost`. The returned
        list comes from the buffer (carrying `first_seen_ns`,
        `last_seen_ns`, `status`, `seen_count`), not directly from the
        raw discovery samples.
        """
        try:
            samples = self._discovery_snapshot(BuiltinTopicDcpsParticipant, _MAX_PARTICIPANTS)
        except Exception as exc:
            raise AdapterError(
                f"CycloneDDS participant discovery failed on domain {self._domain_id} "
                f"({type(exc).__name__}: {exc}). Common causes: DDS domain mismatch, "
                f"firewall blocking RTPS multicast, or CYCLONEDDS_URI pointing at an "
                f"unreadable config."
            ) from exc

        observed_guids: set[str] = set()
        for sample in samples:
            guid = format_guid(_extract_guid(sample))
            observed_guids.add(guid)
            self._lifecycle.record_seen(
                guid=guid,
                vendor=canonicalize_vendor_id(_extract_vendor_id(sample)),
                hostname=_extract_hostname(sample),
                name=_extract_participant_name(sample),
                domain_id=self._domain_id,
                mode_effective="live",
            )
        # Reconcile: previously-active GUIDs missing from this snapshot
        # flip to "left" + emit a `lost` event.
        self._lifecycle.reconcile(
            observed_guids=observed_guids,
            domain_id=self._domain_id,
            mode_effective="live",
        )
        return self._lifecycle.snapshot_participants(domain_id=self._domain_id)

    def detect_qos_mismatches(self, topic: str | None = None) -> list[MismatchReport]:
        """Pair reader/writer endpoints by topic, run the shared analyzer on each.

        The pairing / reporting logic lives in
        `common.qos_endpoints.detect_mismatches_across_endpoints` (shared with
        the Fast adapter, unit-tested without a binding). This method only
        gathers the vendor-native endpoint samples and hands them over.
        """
        try:
            subs = self._discovery_snapshot(BuiltinTopicDcpsSubscription, _MAX_ENDPOINTS)
            pubs = self._discovery_snapshot(BuiltinTopicDcpsPublication, _MAX_ENDPOINTS)
        except Exception as exc:
            raise AdapterError(
                f"CycloneDDS endpoint discovery failed on domain {self._domain_id} "
                f"({type(exc).__name__}: {exc})."
            ) from exc

        return detect_mismatches_across_endpoints(
            subs=subs,
            pubs=pubs,
            topic=topic,
            qos_to_profile=_cyclone_qos_to_profile,
            extract_topic_name=_extract_topic_name,
            extract_guid=_extract_guid,
        )

    def peek_dds_samples(self, topic: str, count: int) -> SampleResult:
        """Peek recent samples on a DDS topic.

        The 3 builtin DCPS topics keep their v0.3.0 structured-payload
        shape. For a user topic, dynamic decode is disabled in this
        release: a topic announced on the bus yields one placeholder
        sample (`_decode_status="raw"`, empty bytes, with a note saying so)
        and no payload is decoded. The placeholder is not a received
        sample and is not recorded for `topic_metrics`.

        Raises `AdapterError` only when the topic has not been
        discovered on the bus (no endpoint claims it).
        """
        if count < 0:
            raise AdapterError("count must be >= 0")

        if topic in _BUILTIN_DCPS_TOPICS:
            return self._peek_builtin(topic, count)

        return self._peek_user_topic(topic, count)

    def _peek_builtin(self, topic: str, count: int) -> SampleResult:
        """Builtin DCPS topic peek: unchanged from v0.3.0."""
        topic_class = _BUILTIN_DCPS_TOPICS[topic]
        try:
            samples_raw = self._discovery_snapshot(topic_class, count)
        except Exception as exc:
            raise AdapterError(
                f"CycloneDDS sample peek failed on topic {topic!r} ({type(exc).__name__}: {exc})."
            ) from exc

        import time

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
                    "type_name": _extract_type_name(s),
                    "_raw_text": repr(s),
                },
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

    def _peek_user_topic(self, topic: str, count: int) -> SampleResult:
        """User-topic peek: topic presence plus an annotated placeholder.

        1. Confirm the topic is announced on the bus (subscription or
           publication present). If not -> `AdapterError`.
        2. `_try_dynamic_decode_cyclone` is disabled and returns `None`, so
           the placeholder path below is the one that runs. The decoded
           branch is kept for the future rewrite and is currently
           unreachable.
        3. The placeholder carries `DYNAMIC_DECODE_DISABLED_NOTE`. It is not
           a received sample, so it is never recorded into `MetricsBuffer`.
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
            import time

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

        # Placeholder only: nothing was received, so nothing is recorded into
        # the metrics buffer (a placeholder must never count as a sample).
        fallback_samples = user_topic_placeholder(topic, count, note=DYNAMIC_DECODE_DISABLED_NOTE)
        return SampleResult(
            topic=topic,
            count=len(fallback_samples),
            samples=fallback_samples,
            mode_effective="live",
        )

    def _is_topic_on_bus(self, topic: str) -> bool:
        """True iff a sub or pub for `topic` has been discovered."""
        try:
            subs = self._discovery_snapshot(BuiltinTopicDcpsSubscription, _MAX_ENDPOINTS)
            pubs = self._discovery_snapshot(BuiltinTopicDcpsPublication, _MAX_ENDPOINTS)
        except Exception:  # pragma: no cover: defensive
            log.exception("cyclone discovery probe for topic %r failed", topic)
            return False
        return any(_extract_topic_name(sample) == topic for sample in subs + pubs)

    def participant_events(
        self, domain_id: int = 0, lookback_seconds: int = 300
    ) -> list[ParticipantEvent]:
        """Return lifecycle events for `self._domain_id` within the window.

        Cyclone's lifecycle log is populated lazily by `list_participants`
        calls: see the docstring there. A GUID that joined and left
        between two `list_participants` calls will not produce events.
        The `participant_events` tool description makes this explicit.
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
        return self._metrics.compute_metrics(
            topic=topic,
            window_seconds=window_seconds,
            domain_id=self._domain_id,
            mode_effective="live",
        )


# Sample-introspection helpers (_extract_guid / _extract_vendor_id /
# _extract_hostname / _extract_topic_name) and the QoS normalizer
# (_cyclone_qos_to_profile) were moved to the binding-free
# `topicforge.adapters.common.dds_introspection` /
# `.qos_normalize` modules (Lot 0, audit 2026-07-08) so they are
# unit-testable without the cyclonedds bindings installed. They are
# imported and aliased back to their original names at the top of this
# module, so the call sites above are unchanged.
