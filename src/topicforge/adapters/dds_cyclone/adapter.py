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
* `peek_dds_samples`: full-fidelity on the 4 builtin DCPS topics ;
  arbitrary user topics go through `_peek_user_topic` with best-effort
  `cyclonedds.dynamic` XTypes decode (Phase 1.5). Each sample carries a
  `_decode_status` annotation (`"full"` / `"partial"` / `"raw"`) so the
  LLM caller knows whether to trust the payload or fall back on
  `_raw_bytes_hex`.
* `participant_events`: `discovered` / `lost` events from the
  `LifecycleBuffer`. Caveat : Cyclone updates the buffer only on
  `list_participants` poll calls (no native at-discovery callbacks).
* `topic_metrics`: opportunistic frequency / sequence-gap / latency
  metrics buffered as `peek_dds_samples` surfaces samples (no native
  at-sample-receive callback in cyclonedds 2.6.x Python).

Sample-introspection helpers below are defensive against binding-version
shape variations: they read attributes via `getattr` with fallbacks and
collapse missing data to `None` / `"unknown"` rather than raising. A
single odd discovery sample must not break the whole tool call.
"""

from __future__ import annotations

import logging
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
from cyclonedds.domain import DomainParticipant
from cyclonedds.util import duration

from topicforge.adapters.base import AdapterError, AdapterName, EffectiveMode
from topicforge.adapters.common import (
    DDS_ONLY_ERROR_MSG,
    LifecycleBuffer,
    MetricsBuffer,
    annotate_raw,
    canonicalize_vendor_id,
    decode_dynamic_sample,
    decode_field_value,
    detect_mismatches_across_endpoints,
    dynamic_type_name,
    extract_publish_ns_from_payload,
    extract_seq_from_payload,
    format_guid,
    iter_field_names,
    validate_domain_id,
)
from topicforge.adapters.common import (
    cyclone_extract_guid as _extract_guid,
)
from topicforge.adapters.common import (
    cyclone_extract_hostname as _extract_hostname,
)
from topicforge.adapters.common import (
    cyclone_extract_topic_name as _extract_topic_name,
)
from topicforge.adapters.common import (
    cyclone_extract_vendor_id as _extract_vendor_id,
)
from topicforge.adapters.common import (
    cyclone_qos_to_profile as _cyclone_qos_to_profile,
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
_DISCOVERY_TIMEOUT_SEC = 2.0
_SAMPLE_TIMEOUT_SEC = 1.0
_MAX_PARTICIPANTS = 256
_MAX_ENDPOINTS = 1024

# Builtin DCPS topics that `peek_dds_samples` serves with full-fidelity
# structured payloads. Arbitrary user topics route through
# `_peek_user_topic` (Phase 1.5 best-effort XTypes decode -> annotated
# raw-bytes fallback).
_BUILTIN_DCPS_TOPICS: dict[str, Any] = {
    "DCPSParticipant": BuiltinTopicDcpsParticipant,
    "DCPSSubscription": BuiltinTopicDcpsSubscription,
    "DCPSPublication": BuiltinTopicDcpsPublication,
}


def _try_dynamic_decode_cyclone(dp: Any, topic: str, count: int) -> list[MessageSample] | None:
    """Best-effort dynamic decode of `topic` via `cyclonedds.dynamic`.

    v0.4.0 Phase 1.5 attempts an actual decode pipeline:

      1. Discover an endpoint for `topic` via the DCPSPublication builtin
         reader and extract its type identifier.
      2. Resolve the TypeObject via `cyclonedds.dynamic.get_types_for_typeid`
         (or fallback entry points across binding versions).
      3. Build a typed Topic + DataReader against the resolved type.
      4. Take up to `count` samples.
      5. Decode each sample field-by-field via reflection on the dynamic
         type. Per-sample try/except so a single bad sample falls back
         to `annotate_raw` without breaking the whole list.
      6. Per-field try/except. Fields that decode cleanly land at the
         top level of the payload ; fields that raise an exception (union
         variants, optional fields, recursive types the binding cannot
         resolve) trigger `annotate_partial` with a short note.

    Returns `None` when the binding doesn't expose any of the known
    dynamic entry points, when no endpoint announces a type id, or when
    every step before sample collection fails. The caller then surfaces
    the existing annotated raw fallback.
    """
    try:
        from cyclonedds import dynamic as cyclone_dynamic  # type: ignore[import-not-found]
    except ImportError:
        return None

    type_resolver = _find_dynamic_resolver(cyclone_dynamic)
    if type_resolver is None:
        log.debug(
            "cyclonedds.dynamic exposes no known resolver entry point ; "
            "user-topic %r falls back to raw bytes annotation",
            topic,
        )
        return None

    type_id = _discover_type_id_for_topic(dp, topic)
    if type_id is None:
        log.debug(
            "no type id discovered for topic %r in DCPSPublication ; "
            "falls back to raw bytes annotation",
            topic,
        )
        return None

    try:
        type_object = type_resolver(type_id)
    except Exception:  # pragma: no cover: binding-side error
        log.debug("dynamic type resolution failed for topic %r", topic, exc_info=True)
        return None
    if type_object is None:
        return None

    samples_raw = _collect_dynamic_samples(dp, topic, type_object, count)
    if samples_raw is None:
        return None  # binding could not build the reader

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


def _find_dynamic_resolver(cyclone_dynamic: Any) -> Any | None:
    """Return the first callable resolver on `cyclonedds.dynamic`.

    The dynamic-IDL entry point has been renamed across CycloneDDS
    Python binding versions. We probe the cited names in order and
    return the first attribute that is callable.
    """
    for attr in ("get_types_for_typeid", "get_type_for_endpoint", "get_type"):
        candidate = getattr(cyclone_dynamic, attr, None)
        if callable(candidate):
            return candidate
    return None


def _discover_type_id_for_topic(dp: Any, topic: str) -> Any | None:
    """Pull a type identifier off the first DCPSPublication sample for `topic`.

    Returns `None` when no publication for `topic` is in the discovery
    cache, or when the binding does not expose a `type_id` / `type_info`
    attribute on the sample.
    """
    try:
        reader = BuiltinDataReader(dp, BuiltinTopicDcpsPublication)
        for sample in reader.read_iter(timeout=duration(seconds=_DISCOVERY_TIMEOUT_SEC)):
            if _extract_topic_name(sample) != topic:
                continue
            for attr in ("type_id", "type_identifier", "type_info"):
                tid = getattr(sample, attr, None)
                if tid is not None:
                    return tid
    except Exception:  # pragma: no cover: defensive
        log.debug("type-id discovery probe failed for topic %r", topic, exc_info=True)
    return None


def _collect_dynamic_samples(dp: Any, topic: str, type_object: Any, count: int) -> list[Any] | None:
    """Build a typed reader against the resolved `type_object` and take samples.

    Returns a list of raw sample objects (the binding's typed
    representation) on success, `None` when the typed reader could not
    be constructed. Bounded by `count` and by `_SAMPLE_TIMEOUT_SEC`.
    """
    try:
        from cyclonedds.sub import DataReader as DynamicDataReader  # type: ignore[import-not-found]
        from cyclonedds.topic import Topic as DynamicTopic  # type: ignore[import-not-found]

        dynamic_topic = DynamicTopic(dp, topic, type_object)
        reader = DynamicDataReader(dp, dynamic_topic)
        return list(reader.read_iter(timeout=duration(seconds=_SAMPLE_TIMEOUT_SEC)))[:count]
    except Exception:  # pragma: no cover: binding-side error
        log.debug("typed reader construction failed for topic %r", topic, exc_info=True)
        return None


# The 6 dynamic-type decoders (decode_dynamic_sample, iter_field_names,
# decode_field_value, dynamic_type_name, extract_seq_from_payload,
# extract_publish_ns_from_payload) live in
# `topicforge.adapters.common.cdr_decoder` since v0.4.0 Phase 3 ; the
# `_underscore` aliases at the top of this module preserve the original
# Cyclone call sites without rewrites.


_USER_TOPIC_ROADMAP_MSG = (
    "peek_dds_samples could not decode user topic via cyclonedds.dynamic. "
    "v0.4.0 Phase 1.5 attempts a typed-reader decode pipeline ; on miss "
    "(binding version without dynamic XTypes support, type id missing, "
    "or per-sample decode failures) we surface raw bytes annotation. "
    "Real Cyclone bus feedback will refine the decode branch."
)


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
            reader = BuiltinDataReader(self._dp, BuiltinTopicDcpsParticipant)
            samples = list(reader.read_iter(timeout=duration(seconds=_DISCOVERY_TIMEOUT_SEC)))
        except Exception as exc:
            raise AdapterError(
                f"CycloneDDS participant discovery failed on domain {self._domain_id} "
                f"({type(exc).__name__}: {exc}). Common causes: DDS domain mismatch, "
                f"firewall blocking RTPS multicast, or CYCLONEDDS_URI pointing at an "
                f"unreadable config."
            ) from exc

        observed_guids: set[str] = set()
        for sample in samples[:_MAX_PARTICIPANTS]:
            guid = format_guid(_extract_guid(sample))
            observed_guids.add(guid)
            self._lifecycle.record_seen(
                guid=guid,
                vendor=canonicalize_vendor_id(_extract_vendor_id(sample)),
                hostname=_extract_hostname(sample),
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
            sub_reader = BuiltinDataReader(self._dp, BuiltinTopicDcpsSubscription)
            pub_reader = BuiltinDataReader(self._dp, BuiltinTopicDcpsPublication)
            subs = list(sub_reader.read_iter(timeout=duration(seconds=_DISCOVERY_TIMEOUT_SEC)))[
                :_MAX_ENDPOINTS
            ]
            pubs = list(pub_reader.read_iter(timeout=duration(seconds=_DISCOVERY_TIMEOUT_SEC)))[
                :_MAX_ENDPOINTS
            ]
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

        v0.4.0 Phase 1: arbitrary user topics are decoded best-effort
        via `cyclonedds.dynamic` ; on failure each sample falls back to
        an annotated raw-bytes payload (`_decode_status="raw"`). The 4
        builtin DCPS topics keep their v0.3.0 structured-payload shape
        for backward compatibility.

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
            reader = BuiltinDataReader(self._dp, topic_class)
            samples_raw = list(reader.read_iter(timeout=duration(seconds=_SAMPLE_TIMEOUT_SEC)))[
                :count
            ]
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
        """User-topic peek: dynamic XTypes decode with raw-bytes fallback.

        Strategy (D2 from `floating-napping-meteor.md`):
          1. Confirm the topic is announced on the bus (subscription
             or publication present). If not -> `AdapterError`.
          2. Attempt `cyclonedds.dynamic.get_types_for_typeid` against
             the discovered endpoint metadata. On success, build a
             typed reader and decode samples.
          3. On any decode failure (binding does not support the IDL
             construct, type resolution returned None, etc.) emit
             samples with `annotate_raw` so the caller still gets
             something usable.

        v0.4.0 Phase 1 ships the structural plumbing + raw fallback. A
        v0.4.0 Phase 2 patch extends the dynamic branch to cover more
        IDL constructs once we collect feedback from real buses.
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
            # v0.4.0 Phase 2: opportunistic metrics fill on the
            # decoded user-topic path. Pull seq# and publish_ns from
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

        # Best-effort raw fallback: surface a single annotated placeholder
        # so the caller knows the topic is present but the binding could
        # not decode the IDL. Hex payload is empty here (no bytes captured
        # by the plumbing-only branch) ; a Phase 1+ patch will populate
        # it with the actual serialized payload when the dynamic-reader
        # path is wired through.
        fallback_samples: list[MessageSample] = []
        if count > 0:
            fallback_samples.append(
                MessageSample(
                    topic=topic,
                    message_type="dds/unknown",
                    timestamp_ns=0,
                    payload=annotate_raw(
                        b"",
                        note=_USER_TOPIC_ROADMAP_MSG,
                    ),
                )
            )
        return SampleResult(
            topic=topic,
            count=len(fallback_samples),
            samples=fallback_samples,
            mode_effective="live",
        )

    def _is_topic_on_bus(self, topic: str) -> bool:
        """True iff a sub or pub for `topic` has been discovered."""
        try:
            sub_reader = BuiltinDataReader(self._dp, BuiltinTopicDcpsSubscription)
            pub_reader = BuiltinDataReader(self._dp, BuiltinTopicDcpsPublication)
            subs = list(sub_reader.read_iter(timeout=duration(seconds=_DISCOVERY_TIMEOUT_SEC)))[
                :_MAX_ENDPOINTS
            ]
            pubs = list(pub_reader.read_iter(timeout=duration(seconds=_DISCOVERY_TIMEOUT_SEC)))[
                :_MAX_ENDPOINTS
            ]
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
        calls (no per-sample callback in cyclonedds Python). A topic
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
