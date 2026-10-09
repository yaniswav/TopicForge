"""Tool input/output schemas.

Models are frozen and JSON-friendly. `extra="forbid"` makes an accidental
extra key fail in tests instead of reaching clients.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_CONFIG = ConfigDict(extra="forbid", frozen=True)

_MODE_EFFECTIVE_DESC = (
    "Runtime mode the adapter served this response in: `live` (real ROS2 "
    "introspection) or `mock` (deterministic fixtures). Lets a caller tell a "
    "real graph from a demo one without calling `health_check`."
)

# `mode_effective` is on every top-level response model except `HealthReport`
# (it has `mode` / `requested_mode`) and `MessageSample` (nested in
# `SampleResult`, which carries it). Do not add it to those two.


class QosProfile(BaseModel):
    """QoS profile of one DDS endpoint (reader or writer).

    Covers the policies behind most "subscriber receives nothing" cases.
    Vendor-specific extensions are ignored.
    """

    model_config = _CONFIG

    reliability: Literal["RELIABLE", "BEST_EFFORT"] = Field(
        description=(
            "DDS Reliability QoS. `RELIABLE` retries lost samples; "
            "`BEST_EFFORT` does not. A `RELIABLE` reader cannot match a "
            "`BEST_EFFORT` writer."
        )
    )
    durability: Literal["VOLATILE", "TRANSIENT_LOCAL", "TRANSIENT", "PERSISTENT"] = Field(
        description=(
            "DDS Durability QoS. `VOLATILE` writers do not retain samples "
            "for late joiners; `TRANSIENT_LOCAL` writers do. A "
            "`TRANSIENT_LOCAL` reader cannot match a `VOLATILE` writer."
        )
    )
    history: Literal["KEEP_LAST", "KEEP_ALL"] | None = Field(
        default=None,
        description=(
            "DDS History QoS. `KEEP_LAST` keeps a bounded ring buffer "
            "of size `history_depth`; `KEEP_ALL` keeps every sample "
            "(memory permitting). Mixed `KEEP_ALL` reader with "
            "`KEEP_LAST` writer is risky but not strictly incompatible. "
            "`None` when it is not known: DDS discovery does not carry History, "
            "so it is reported only for TopicForge's own endpoints and for "
            "Cyclone DDS peers that set something other than the default "
            "(see `history_note`)."
        ),
    )
    history_depth: int | None = Field(
        default=None,
        ge=0,
        description="Depth for `KEEP_LAST`. `None` for `KEEP_ALL` or when `history` is `None`.",
    )
    history_note: str | None = Field(
        default=None,
        description="Set when `history` was left out because discovery did not announce it.",
    )
    deadline_ns: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Deadline QoS in nanoseconds. `None` means no deadline (infinite, "
            "the DDS default). "
            "A reader deadline tighter (smaller) than a writer deadline is "
            "incompatible: the writer cannot guarantee the reader's promise."
        ),
    )
    liveliness_kind: Literal["AUTOMATIC", "MANUAL_BY_PARTICIPANT", "MANUAL_BY_TOPIC"] | None = (
        Field(
            default=None,
            description=(
                "Liveliness QoS kind. `AUTOMATIC` is asserted by the middleware; "
                "the two `MANUAL_*` kinds need the application to write or assert "
                "liveliness, and the endpoint is declared not alive when it does "
                "not within `liveliness_lease_ns`. `None` when not announced."
            ),
        )
    )
    liveliness_lease_ns: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Liveliness lease duration in nanoseconds. `None` means infinite "
            "(the DDS default) or not announced."
        ),
    )
    ownership_kind: Literal["SHARED", "EXCLUSIVE"] | None = Field(
        default=None,
        description=(
            "Ownership QoS kind. With `EXCLUSIVE`, only the writer with the "
            "highest `ownership_strength` delivers samples to a reader. "
            "`None` when not announced."
        ),
    )
    ownership_strength: int | None = Field(
        default=None,
        description="Ownership strength of a writer (`EXCLUSIVE` ownership). `None` when not announced.",
    )
    partitions: list[str] | None = Field(
        default=None,
        description=(
            "Partition names the endpoint's Publisher or Subscriber belongs "
            'to. No Partition policy is the default partition, reported as `[""]` '
            "(never an empty list). Endpoints only match when their partitions "
            "intersect. `None` only when the backend cannot read partitions at all "
            "(unknown, not the default)."
        ),
    )
    latency_budget_ns: int | None = Field(
        default=None,
        ge=0,
        description=(
            "LatencyBudget QoS in nanoseconds (a delivery-delay hint). `0` is "
            "the DDS default. `None` means infinite or not announced."
        ),
    )
    destination_order: Literal["BY_RECEPTION_TIMESTAMP", "BY_SOURCE_TIMESTAMP"] | None = Field(
        default=None,
        description="DestinationOrder QoS. `None` when not announced.",
    )
    data_representation: list[str] | None = Field(
        default=None,
        description=(
            "Data representations the endpoint accepts or offers, from "
            "`XCDR1` and `XCDR2`. `None` when not announced."
        ),
    )
    unknown_policies: list[str] | None = Field(
        default=None,
        description=(
            "Policies the endpoint announced but whose value could not be read "
            "(for example `Deadline`). Their duration fields are `None` here, which "
            "does NOT mean infinite for these: the scan does not compare them."
        ),
    )


TimeSource = Literal["dds_source_timestamp", "observed_local"]
"""Origin of a lifecycle timestamp: see `ParticipantEvent.time_source`."""

_DdsVendor = Literal[
    "cyclone",
    "fast",
    "rti",
    "rti_micro",
    "opensplice",
    "opendds",
    "coredx",
    "intercom",
    "dust",
    "mock",
    "unknown",
]
"""Wire values of the `vendor` field on `ParticipantInfo` / `ParticipantEvent`.

Must stay identical to `adapters/common/dds_helpers.py:VendorTag` (pinned by
`tests/test_dds_helpers.py`); the models layer cannot import from adapters.
"""


class ParticipantInfo(BaseModel):
    """DDS participant discovered on the configured domain.

    Carries identity (`guid`, `name`, `vendor`) plus lifecycle fields
    (`first_seen_ns`, `last_seen_ns`, `status`, `seen_count`).
    """

    model_config = _CONFIG

    guid: str = Field(
        description=(
            "DDS Global Unique Identifier of the participant: hex string, "
            "stable across discovery events within a single deployment."
        )
    )
    name: str | None = Field(
        default=None,
        description=(
            "Participant name the application announced through the standard "
            "DDS EntityName QoS (PID_ENTITY_NAME in RTPS discovery), for "
            "example `lidar_driver`. `None` when the application did not set "
            "one (Dust DDS cannot) or the backend does not expose it."
        ),
    )
    vendor: _DdsVendor = Field(
        description=(
            "DDS implementation that announced this participant, decoded "
            "from the OMG-RTPS `vendor_id` field on the discovery sample. "
            "`cyclone` (Eclipse Cyclone DDS), `fast` (eProsima Fast DDS), "
            "`rti` (RTI Connext), `rti_micro` (RTI Connext Micro), "
            "`opensplice` (ADLink OpenSplice), `opendds` (OCI OpenDDS), "
            "`coredx` (Twin Oaks CoreDX), `intercom` (Kongsberg InterCOM), "
            "`dust` (S2E Dust DDS). `mock` is reserved for synthetic "
            "fixtures. `unknown` means the vendor could not be determined: "
            "TopicForge reads it from the vendor prefix of the participant "
            "GUID, and some vendors (for example Dust DDS and RTI Connext) do "
            "not put their vendor id there, while the Cyclone Python binding "
            "does not expose the RTPS header vendor id. The participant is "
            "still reported; see `vendor_source`. "
            "Any conformant DDS-RTPS participant should appear through "
            "standard discovery; Cyclone DDS and Dust DDS have been observed "
            "on a live bus. See `docs/dds-interop-matrix.md`."
        )
    )
    hostname: str | None = Field(
        default=None,
        description=(
            "Hostname announced in discovery (Cyclone `__Hostname` property). "
            "`None` when the vendor does not announce it."
        ),
    )
    vendor_source: Literal["guid_prefix", "none"] = Field(
        default="none",
        description=(
            "Where `vendor` came from: `guid_prefix` when it was read from the "
            "vendor prefix of the participant GUID, `none` when the vendor is "
            "unknown (or synthetic in mock mode)."
        ),
    )
    is_observer: bool = Field(
        default=False,
        description=(
            "True for TopicForge's own read-only observer participant, which "
            "joins the domain to watch it and appears in this list; false for "
            "every other participant."
        ),
    )
    domain_id: int = Field(
        ge=0,
        le=232,
        description="DDS domain id the participant is bound to.",
    )
    mode_effective: Literal["mock", "live"] = Field(description=_MODE_EFFECTIVE_DESC)
    first_seen_ns: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Wall-clock timestamp (nanoseconds since epoch) of the **first** "
            "discovery sample TopicForge observed for this participant. "
            "`None` when the adapter does not track lifecycle."
        ),
    )
    last_seen_ns: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Wall-clock timestamp (nanoseconds since epoch) of the **most "
            "recent** discovery sample observed. Used by `participant_events` "
            "to distinguish currently-active participants from those that "
            "left the bus. `None` when the adapter does not track lifecycle."
        ),
    )
    status: Literal["active", "left", "unknown"] = Field(
        default="unknown",
        description=(
            "Lifecycle status. `active` means TopicForge has a recent "
            "discovery sample for this GUID and the bus has not signalled "
            "removal. `left` means the participant was observed earlier "
            "but has since disappeared (a Fast DDS `REMOVED` callback or a "
            "Cyclone polling delta). `unknown` when the adapter does not "
            "track lifecycle."
        ),
    )
    seen_count: int = Field(
        default=1,
        ge=1,
        description=(
            "Number of distinct discovery samples observed for this "
            "participant across all calls to `list_participants` during "
            "this server's lifetime. Starts at 1 and "
            "increments on each observation."
        ),
    )
    announced_ns: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Nanoseconds since epoch (DDS source timestamp of the participant's "
            "most recent announcement, taken from the announcing side's clock; "
            "the local clock when the announcer sent none). `None` when the "
            "backend does not expose it. Distinct from `first_seen_ns` / "
            "`last_seen_ns`, which are TopicForge's own local clock."
        ),
    )
    lost_ns: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Nanoseconds since epoch of the moment the participant left, once "
            "`status` is `left`; `None` while it is active. How precise it is "
            "depends on `lost_time_source`. It is an upper bound of when the "
            "participant died: after a clean shutdown it is the exact leave "
            "time, after a crash it is when the lease expired, and the two "
            "cannot be told apart. The process died at or before `lost_ns`, "
            "and at most one lease earlier (10 s Cyclone default, 20 s Fast, "
            "100 s RTI)."
        ),
    )
    lost_time_source: TimeSource | None = Field(
        default=None,
        description=(
            "Where `lost_ns` comes from: `dds_source_timestamp` (timestamp "
            "carried by the discovery dispose) or `observed_local` (the "
            "moment TopicForge noticed, the weakest). `None` while active."
        ),
    )


class ParticipantEvent(BaseModel):
    """A point-in-time lifecycle event (discovered or lost) for a DDS participant.

    `ParticipantInfo` is the current state; this is one event in its history.
    Returned by `participant_events`.
    """

    model_config = _CONFIG

    guid: str = Field(description="DDS Global Unique Identifier of the participant involved.")
    event_type: Literal["discovered", "lost"] = Field(
        description=(
            "`discovered` when the participant first joined the bus (or "
            "rejoined after leaving). `lost` when TopicForge observed a "
            "`REMOVED` callback (Fast DDS) or a polling delta where the "
            "GUID no longer appears in the DCPSParticipant builtin reader "
            "snapshot (Cyclone). The transition is reported once per "
            "state change."
        )
    )
    vendor: _DdsVendor = Field(
        description=(
            "DDS implementation tag for the participant involved, decoded "
            "the same way as `ParticipantInfo.vendor`."
        )
    )
    timestamp_ns: int = Field(
        ge=0,
        description=(
            "Wall-clock timestamp (nanoseconds since epoch) when TopicForge "
            "captured the event. For Fast DDS this is when the listener "
            "callback ran; for Cyclone this is when the polling delta was "
            "computed; for mock fixtures this is a deterministic anchor."
        ),
    )
    hostname: str | None = Field(
        default=None,
        description="Hostname announced by the participant when known, else `None`.",
    )
    name: str | None = Field(
        default=None,
        description="Participant name (EntityName QoS) when known, else `None`.",
    )
    domain_id: int = Field(
        ge=0,
        le=232,
        description="DDS domain id the event occurred on.",
    )
    mode_effective: Literal["mock", "live"] = Field(description=_MODE_EFFECTIVE_DESC)
    time_source: TimeSource = Field(
        default="observed_local",
        description=(
            "Where `timestamp_ns` comes from. `dds_source_timestamp`: the DDS "
            "timestamp of the discovery announcement or dispose. For a "
            "`lost` event it is an upper bound: exact after a clean "
            "shutdown, the lease expiry after a crash (the process died up "
            "to one lease earlier: 10 s Cyclone default, 20 s Fast, 100 s "
            "RTI), and the two cannot be told apart. `observed_local`: the "
            "moment TopicForge noticed (weakest)."
        ),
    )
    observed_ns: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Local wall-clock time (ns since epoch) at which TopicForge noticed "
            "the event. Always at or after `timestamp_ns` when the latter is "
            "DDS-derived. `None` when not tracked."
        ),
    )


class TopicMetrics(BaseModel):
    """Temporal metrics for a single DDS topic over a recent window.

    Built from samples that pass through `peek_dds_samples`. The buffer is
    filled on demand because neither the `cyclonedds` nor the `fastdds`
    Python binding has a reliable per-sample callback, so a burst between two
    calls is not seen.

    Numeric fields are `None` (`0` for `sequence_gaps_count`) when the data is
    missing: no samples, no source timestamps, no sequence number in the
    payload. `samples_observed=0` is a valid result, not an error.
    """

    model_config = _CONFIG

    topic: str = Field(description="Topic the metrics were computed for.")
    window_seconds: int = Field(
        ge=1,
        le=3600,
        description=(
            "Requested window in seconds (1..3600). Echoed back from "
            "the tool call so the LLM can correlate the request."
        ),
    )
    window_seconds_actual: float = Field(
        ge=0,
        description=(
            "Actual elapsed seconds within the window. May be smaller "
            "than `window_seconds` when the adapter buffered samples "
            "for less time than the requested window (e.g., the server "
            "just started). `0.0` when `samples_observed=0`."
        ),
    )
    samples_observed: int = Field(
        ge=0,
        description=(
            "Number of samples in the buffer matching `topic` within "
            "the window. `0` means TopicForge has not seen any sample "
            "on this topic recently: it does NOT mean the topic has "
            "no publisher, only that no `peek_dds_samples` call "
            "captured one in the window."
        ),
    )
    frequency_hz_observed: float | None = Field(
        default=None,
        description=(
            "`samples_observed / window_seconds_actual`. `None` when "
            "fewer than 2 samples were observed (a single sample does "
            "not define a frequency)."
        ),
    )
    frequency_hz_declared: float | None = Field(
        default=None,
        description=(
            "Declared, not measured: `1 / deadline` for the shortest QoS "
            "Deadline period announced by a writer on this topic in discovery. "
            "`None` when no writer announced a finite Deadline or the topic "
            "is not announced. It is the rate the application promised, not "
            "the rate observed."
        ),
    )
    status: Literal["ok", "no_samples_yet", "unsupported_user_topic"] = Field(
        default="ok",
        description=(
            "How to read the numbers. `unsupported_user_topic`: the topic is a "
            "user topic, whose payload is not decoded, so no metric exists and "
            "the null fields are not a measurement. `no_samples_yet`: a "
            "supported topic with nothing buffered in the window. `ok`: "
            "metrics computed from buffered samples."
        ),
    )
    sequence_gaps_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of missing sequence numbers detected in the "
            "buffered samples. `0` either means no gaps observed OR "
            "the sample type did not expose a sequence number (check "
            "`sequence_numbers_available` to disambiguate)."
        ),
    )
    sequence_numbers_available: bool = Field(
        default=False,
        description=(
            "True when the adapter successfully extracted sequence "
            "numbers from at least one sample. Sequence number support "
            "depends on the message type: `Header`-stamped messages "
            "with a `seq` field expose it; primitives like "
            "`std_msgs/String` do not."
        ),
    )
    latency_ns_p50: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Median publish-to-receive latency in nanoseconds, "
            "computed only when the sample type exposes a publish "
            "timestamp (typically via `header.stamp` on "
            "`Header`-stamped messages). `None` when "
            "`latency_available=False`."
        ),
    )
    latency_ns_p95: int | None = Field(
        default=None,
        ge=0,
        description="95th-percentile publish-to-receive latency (ns).",
    )
    latency_ns_p99: int | None = Field(
        default=None,
        ge=0,
        description="99th-percentile publish-to-receive latency (ns).",
    )
    latency_available: bool = Field(
        default=False,
        description=(
            "True when at least one sample in the window carried both "
            "a publish timestamp and a receive timestamp. The percentile "
            "fields are `None` when this is False."
        ),
    )
    mode_effective: Literal["mock", "live"] = Field(description=_MODE_EFFECTIVE_DESC)


class PolicyMismatch(BaseModel):
    """One QoS policy of a reader/writer pair, with the values that were compared."""

    model_config = _CONFIG

    policy: str = Field(
        description=(
            "Policy name: `Reliability`, `Durability`, `Deadline`, `Liveliness`, "
            "`LatencyBudget`, `Ownership`, `DestinationOrder`, `DataRepresentation` "
            "or `History`."
        )
    )
    requested: str = Field(
        description='What the reader requests, human-readable: `"RELIABLE"`, `"100 ms"`, `"infinite"`.'
    )
    offered: str = Field(description="What the writer offers, same format as `requested`.")
    rule: str = Field(description="The compatibility rule that failed, one sentence.")


class MismatchReport(BaseModel):
    """A single reader/writer QoS incompatibility detected on a topic.

    Only pairs that share a partition and a type name are reported here: those
    separated by partition or type are `NotMatchedPair`s in the same scan.
    """

    model_config = _CONFIG

    topic: str = Field(description="Topic name where the mismatch was detected.")
    reader_guid: str | None = Field(
        default=None,
        description="GUID of the reader endpoint involved in the mismatch, if known.",
    )
    writer_guid: str | None = Field(
        default=None,
        description="GUID of the writer endpoint involved in the mismatch, if known.",
    )
    reader_participant_guid: str | None = Field(
        default=None, description="GUID of the participant that owns the reader, if known."
    )
    reader_participant_name: str | None = Field(
        default=None, description="Announced name of the reader's participant, if it set one."
    )
    writer_participant_guid: str | None = Field(
        default=None, description="GUID of the participant that owns the writer, if known."
    )
    writer_participant_name: str | None = Field(
        default=None, description="Announced name of the writer's participant, if it set one."
    )
    reader_type_name: str | None = Field(
        default=None, description="Type name the reader announced."
    )
    writer_type_name: str | None = Field(
        default=None, description="Type name the writer announced."
    )
    incompatible_policies: list[str] = Field(
        description=(
            "Names of the QoS policies that block communication or risk "
            "degradation: see `details` for the compared values."
        )
    )
    severity: Literal["incompatible", "risky"] = Field(
        description=(
            "`incompatible` means communication is definitely blocked; "
            "`risky` means it may degrade but is not strictly blocked by "
            "the DDS spec (History only). Useful for an LLM to triage user-facing advice."
        )
    )
    details: list[PolicyMismatch] = Field(
        default_factory=list,
        description="Requested and offered value and the failed rule, one entry per policy.",
    )
    unchecked: list[str] = Field(
        default_factory=list,
        description=(
            "Policies that could not be compared for this pair because one side "
            "did not announce a value."
        ),
    )
    mode_effective: Literal["mock", "live"] = Field(description=_MODE_EFFECTIVE_DESC)


class NotMatchedPair(BaseModel):
    """A reader and a writer on one topic that DDS will not match, whatever their QoS."""

    model_config = _CONFIG

    topic: str = Field(description="Topic name both endpoints use.")
    reader_guid: str = Field(description="GUID of the reader endpoint.")
    reader_participant_guid: str = Field(description="GUID of the reader's participant.")
    reader_participant_name: str | None = Field(
        default=None, description="Announced name of the reader's participant."
    )
    writer_guid: str = Field(description="GUID of the writer endpoint.")
    writer_participant_guid: str = Field(description="GUID of the writer's participant.")
    writer_participant_name: str | None = Field(
        default=None, description="Announced name of the writer's participant."
    )
    reason: Literal["partition", "type_name"] = Field(
        description=(
            "`partition`: no reader partition matches a writer partition. "
            "`type_name`: the endpoints announced different type names. The "
            "RxO QoS rules are not evaluated for such a pair."
        )
    )
    detail: str = Field(description="The two partition lists or the two type names.")
    latent_incompatible_policies: list[PolicyMismatch] = Field(
        default_factory=list,
        description=(
            "RxO policies that would ALSO be incompatible once the partition / type "
            "issue is fixed (same shape as `MismatchReport.details`). Empty when the "
            "QoS of the two endpoints would be compatible, or could not be compared."
        ),
    )


class MatchedPair(BaseModel):
    """A reader and a writer that DDS will connect given their announced QoS."""

    model_config = _CONFIG

    topic: str = Field(description="Topic name both endpoints use.")
    type_name: str | None = Field(default=None, description="Type name both announced.")
    reader_guid: str = Field(description="GUID of the reader endpoint.")
    reader_participant_guid: str = Field(description="GUID of the reader's participant.")
    reader_participant_name: str | None = Field(
        default=None, description="Announced name of the reader's participant."
    )
    writer_guid: str = Field(description="GUID of the writer endpoint.")
    writer_participant_guid: str = Field(description="GUID of the writer's participant.")
    writer_participant_name: str | None = Field(
        default=None, description="Announced name of the writer's participant."
    )
    late_joiner: bool = Field(
        default=False,
        description=(
            "True when the writer is VOLATILE and the reader was announced more than 1 s "
            "after it, both on the same host: samples published before the reader joined "
            "are not delivered to it. Normal for a VOLATILE writer, not a fault."
        ),
    )
    late_joiner_note: str | None = Field(
        default=None, description="One-line explanation, set together with `late_joiner`."
    )


class MismatchScan(BaseModel):
    """Result of `detect_qos_mismatches`: findings plus what was and was not checked."""

    model_config = _CONFIG

    reports: list[MismatchReport] = Field(
        description="Pairs that share a partition and a type but have incompatible or risky QoS."
    )
    not_matched: list[NotMatchedPair] = Field(
        description=(
            "Pairs separated by partition or type name. No data flows between them. "
            "An empty `reports` with a non-empty `not_matched` does not mean the bus is healthy."
        )
    )
    matched: list[MatchedPair] = Field(
        default_factory=list,
        description=(
            "Pairs that will be matched by DDS given the announced QoS: same topic and "
            "type name, overlapping partitions, no incompatible RxO policy (a pair "
            "with only a `risky` History finding still counts). Actual data flow is "
            "not observed."
        ),
    )
    hints: list[str] = Field(
        description=(
            "Leads that are not findings: orphan topics with near-identical names "
            "(typos), type id differences, pairs that could not be fully checked."
        )
    )
    reports_total: int = Field(default=0, ge=0, description="Reports before the size cap.")
    matched_total: int = Field(default=0, ge=0, description="Matched pairs before the size cap.")
    not_matched_total: int = Field(
        default=0, ge=0, description="Not-matched pairs before the size cap."
    )
    truncated: bool = Field(
        default=False,
        description=(
            "True when `reports`, `matched` or `not_matched` was cut to its cap "
            "(200 entries each, incompatible reports first): see the `*_total` fields. "
            "Narrow the scan with `topic`."
        ),
    )
    pairs_checked: int = Field(
        ge=0,
        description=(
            "Same-topic (reader, writer) pairs examined, including those reported in `not_matched`."
        ),
    )
    topics_scanned: int = Field(ge=0, description="Topics that had at least one endpoint in scope.")
    policies_checked: list[str] = Field(description="Policies compared on every pair.")
    policies_unchecked: list[str] = Field(
        description=(
            "Policies and facts this scan does not cover, each with a one-line reason. "
            "A clean result says nothing about them."
        )
    )
    mode_effective: Literal["mock", "live"] = Field(description=_MODE_EFFECTIVE_DESC)


class TopicInfo(BaseModel):
    """Description of a single ROS2 topic.

    Carries optional DDS-side enrichment fields when the active middleware
    backend can resolve them (CycloneDDS / Fast DDS). The ROS2 CLI adapter and
    the mock ROS2 path leave them `None`.
    """

    model_config = _CONFIG

    name: str = Field(description="Fully qualified topic name, e.g. `/cmd_vel`.")
    message_type: str = Field(description="ROS2 message type, e.g. `geometry_msgs/msg/Twist`.")
    publisher_count: int = Field(ge=0, description="Publishers known to the graph.")
    subscriber_count: int = Field(ge=0, description="Subscribers known to the graph.")
    qos_reliability: str | None = Field(
        default=None,
        description=(
            "Reliability announced by the topic's publishers: `reliable`, "
            "`best_effort`, or `mixed` when publishers disagree. `null` when "
            "unknown: the topic has no publisher, or the value was not read "
            "(`list_topics` does not read QoS; `get_topic_info` does)."
        ),
    )
    qos_durability: str | None = Field(
        default=None,
        description=(
            "Durability announced by the topic's publishers: `volatile`, "
            "`transient_local` (late subscribers receive the last samples; "
            "typical of latched topics such as `/tf_static`), or `mixed` when "
            "publishers disagree. `null` when unknown, with the same rules as "
            "`qos_reliability`."
        ),
    )
    reader_count: int | None = Field(
        default=None,
        ge=0,
        description=(
            "DDS reader-endpoint count when the active backend can resolve "
            "endpoint-level info (Cyclone / Fast DDS). `None` from the ROS2 CLI "
            "adapter or when the DDS module is inactive."
        ),
    )
    writer_count: int | None = Field(
        default=None,
        ge=0,
        description=(
            "DDS writer-endpoint count when the active backend can resolve "
            "endpoint-level info. `None` from the ROS2 CLI adapter or when "
            "the DDS module is inactive."
        ),
    )
    qos_profile: QosProfile | None = Field(
        default=None,
        description=(
            "Effective DDS QoS profile for this topic when resolvable. "
            "`None` from the ROS2 CLI adapter or when the DDS module is "
            "inactive. The DDS module populates this on a best-effort basis "
            "(picks one representative endpoint if reader/writer QoS differ)."
        ),
    )
    mode_effective: Literal["mock", "live"] = Field(description=_MODE_EFFECTIVE_DESC)


class MessageSample(BaseModel):
    """A single sampled message on a topic."""

    model_config = _CONFIG

    topic: str = Field(
        description="Fully qualified topic name the sample was taken from, e.g. `/cmd_vel`."
    )
    message_type: str = Field(
        description="ROS2 message type of this sample, e.g. `geometry_msgs/msg/Twist`."
    )
    timestamp_ns: int = Field(
        description=(
            "Nanoseconds from the message's own top-level `header.stamp` "
            "(`stamp_source` `header`); else the time kept in the body of "
            "`rosgraph_msgs/Clock` (`clock`), `tf2_msgs/TFMessage` "
            "(`transforms[0].header.stamp`, when not empty) or "
            "`rcl_interfaces/Log` (`stamp`) (`stamp_source` `payload`); else 0 "
            "(`stamp_source` `none`, e.g. `std_msgs/String`, "
            "`geometry_msgs/Twist`). It is the publisher's clock, not the "
            "arrival time: on simulated time it is sim time since the "
            "simulation started (a few seconds, not a date) and 0 is a valid "
            "value. From `peek_bag_samples` a message without a header gets "
            "the bag record time instead (`stamp_source` `recorded`), also "
            "given in `recorded_ns`. Use `received_ns` for when the CLI "
            "printed the message. Mock samples with a header carry synthetic "
            "increasing values."
        )
    )
    stamp_source: Literal["header", "payload", "none", "recorded"] | None = Field(
        default=None,
        description=(
            "Where `timestamp_ns` comes from: `header` (the message's "
            "`header.stamp`), `payload` (no header, but the time is in the body of a "
            "`Clock`, `TFMessage` or `Log` message), `none` (no time in the live "
            "message, `timestamp_ns` is 0) or `recorded` (no time in a message "
            "from a bag, `timestamp_ns` is the bag record time). `None` when the backend "
            "does not say."
        ),
    )
    recorded_ns: int | None = Field(
        default=None,
        description=(
            "Nanoseconds at which the recorder wrote this message into the "
            "bag (the bag's own record time, wall clock unless the recorder "
            "ran on simulated time). `peek_bag_samples` only; `None` for live "
            "samples, where `received_ns` plays that role."
        ),
    )
    received_ns: int | None = Field(
        default=None,
        description=(
            "Wall-clock nanoseconds since epoch at which the `ros2` CLI "
            "printed this message, after it deserialized and formatted it: not "
            "the network receive time, and it lags more for large messages. "
            "Accurate to a fraction of a second. Live `sample_messages` only; "
            "`None` otherwise."
        ),
    )
    payload: dict[str, object] = Field(
        default_factory=dict,
        description=(
            'Message fields as nested named values, e.g. `{"header": '
            '{"stamp": {"sec": 12, "nanosec": 5}, "frame_id": '
            '"base_laser"}, "ranges": [1.0, 1.001]}`. Non-finite floats '
            "are the strings `nan`, `inf`, `-inf`. Live `sample_messages` "
            "adds `_truncated_fields` (dotted paths of arrays, strings or "
            "bytes cut at `max_array_length`; a string of exactly that length plus "
            "`...` that was not cut is listed too) when something was cut, and "
            "`_raw_text` (the unparsed output) only when a message could not "
            "be parsed. With `arrays_summary_only` an array is a string like "
            "`<sequence type: float, length: 541>`. Large messages may be "
            "dropped to keep tool output bounded."
        ),
    )


class BagTopicStats(BaseModel):
    """Per-topic statistics inside a bag analysis result."""

    model_config = _CONFIG

    name: str = Field(description="Fully qualified topic name as recorded in the bag.")
    message_type: str = Field(description="ROS2 message type recorded for this topic.")
    message_count: int = Field(
        ge=0, description="Number of messages recorded on this topic across the bag."
    )
    frequency_hz: float | None = Field(
        default=None,
        ge=0,
        description=(
            "Average publish rate in Hz. With `frequency_basis` `topic_span` "
            "it is `(message_count - 1) / (last - first message time)` for "
            "this topic; with `bag_duration` it is `message_count / bag "
            "duration`, which understates the rate of a topic that started "
            "late or stopped early. `null` when fewer than 2 messages, a "
            "zero time span, or the topic is `latched` and all its messages "
            "fall within 1 second (a start-up burst, not a rate). A latched "
            "topic published over a longer span keeps its rate. Not a "
            "periodic rate for event-driven topics such as `/parameter_events`."
        ),
    )
    first_timestamp_ns: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Recorded time of the first message on this topic, in "
            "nanoseconds (bag receive time, normally epoch). `null` when the "
            "bag was summarized from `ros2 bag info` text only, or the topic "
            "has no messages."
        ),
    )
    last_timestamp_ns: int | None = Field(
        default=None,
        ge=0,
        description="Recorded time of the last message on this topic; same rules as `first_timestamp_ns`.",
    )
    frequency_basis: Literal["topic_span", "bag_duration"] | None = Field(
        default=None,
        description=(
            "How `frequency_hz` was computed: `topic_span` (per-topic first "
            "and last message times, the accurate one) or `bag_duration` "
            "(count divided by whole-bag duration, used when per-topic times "
            "could not be read). `null` when there is no rate."
        ),
    )
    latched: bool | None = Field(
        default=None,
        description=(
            "True when a publisher recorded this topic with transient_local "
            "durability (late joiners get the last samples, e.g. `/tf_static`, "
            "`/rosout`). Such a topic often publishes a start-up burst; when "
            "all its messages fall within 1 second `frequency_hz` is `null`. "
            "`false` when every "
            "recorded publisher was volatile; `null` when the bag carries no "
            "QoS information (ROS 1 bags report their latching flag)."
        ),
    )


class BagAnalysis(BaseModel):
    """Structured summary of a ROS2 bag.

    The fields `bag_format`, `samples_decoded_count`,
    `recording_duration_ns` and `participants_recorded` are populated only
    when the `rosbags`-backed reader runs; the `ros2 bag info` path leaves
    them at their defaults.
    """

    model_config = _CONFIG

    path: str = Field(
        description=(
            "Path to the analyzed bag, as supplied by the caller. May point to a "
            "file (`.mcap`, `.db3`, or ROS 1 `.bag` for `peek_bag_samples`) or to a "
            "`rosbag2_*` directory."
        )
    )
    storage_format: str | None = Field(
        default=None,
        description="`mcap`, `sqlite3`, or other storage identifier when known.",
    )
    duration_seconds: float = Field(
        ge=0,
        description="Total bag duration, in seconds (wall clock between first and last message).",
    )
    message_count: int = Field(
        ge=0, description="Total number of messages across all recorded topics."
    )
    topics: list[BagTopicStats] = Field(
        description="Per-topic statistics for every topic present in the bag."
    )
    anomalies: list[str] = Field(
        default_factory=list,
        description=(
            "Human-readable notes about gaps, clock jumps, or other oddities. "
            "Populated in mock mode only; live mode does not detect anomalies."
        ),
    )
    mode_effective: Literal["mock", "live"] = Field(description=_MODE_EFFECTIVE_DESC)
    note: str | None = Field(
        default=None,
        description=(
            "Why the result is less detailed than usual, for example per-topic "
            "rates computed over the whole bag duration because the bag was "
            "too large to read per-topic message times. `None` when there is "
            "nothing to add."
        ),
    )
    bag_format: Literal["mcap", "db3", "bag", "unknown"] | None = Field(
        default=None,
        description=(
            "Concrete bag container format detected by the reader: `mcap` "
            "(Foxglove MCAP), `db3` (ROS2 rosbag2 SQLite), `bag` (ROS1 "
            "legacy chunked), or `unknown` when the reader could not "
            "classify. `None` when the bag was summarized from "
            "`ros2 bag info` text, which carries no format information."
        ),
    )
    samples_decoded_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Total decoded sample count across all topics produced by the "
            "bag reader. `0` when the reader only parsed metadata or when `rosbags` is not installed "
            "on the host. Use `peek_bag_samples` to pull the actual "
            "sample payloads for a specific topic."
        ),
    )
    recording_duration_ns: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Recording duration in nanoseconds when readable from the "
            "bag's index. `None` when only `ros2 bag info` text was parsed; "
            "`duration_seconds` (float) is the always-populated fallback "
            "that downstream LLM consumers should prefer when this is "
            "`None`."
        ),
    )
    participants_recorded: list[ParticipantInfo] = Field(
        default_factory=list,
        description=(
            "DDS participants recorded in the bag when the container "
            "format embeds participant metadata. MCAP can carry it via "
            "channel metadata records; ROS2 `.db3` and ROS1 `.bag` "
            "generally do not. Empty list when not available, which "
            "is the common case."
        ),
    )


class SampleResult(BaseModel):
    """Envelope returned by the `sample_messages` tool."""

    model_config = _CONFIG

    topic: str = Field(description="Topic the samples were taken from, echoed from the request.")
    count: int = Field(
        ge=0,
        description=(
            "Number of samples actually returned. May be 0 (no publisher active "
            "in live mode, or empty mock fixture), less than the requested count "
            "(topic yielded fewer messages within the timeout), or capped by the "
            "the silent maximum of 50: request `count > 50` and you will "
            "receive at most 50 without warning."
        ),
    )
    samples: list[MessageSample] = Field(
        description="The sampled messages, ordered as received from the backend."
    )
    mode_effective: Literal["mock", "live"] = Field(description=_MODE_EFFECTIVE_DESC)
    note: str | None = Field(
        default=None,
        description=(
            "Why `samples` is empty or limited, when the cause is not obvious "
            "(for example payload decoding is disabled for DDS user topics). "
            "`None` when there is nothing to add."
        ),
    )


class HealthReport(BaseModel):
    """Result of `health_check`. Always succeeds, even when the host is unhealthy."""

    model_config = _CONFIG

    mode: str = Field(
        description=(
            "Runtime mode of the adapter actually serving requests: `mock` "
            "or `live`. `live` with `ros_backend` `none` means the DDS tools "
            "are live and the ROS 2 tools are not available. Can differ from `requested_mode` when a live backend "
            "could not start (e.g. `live` requested without `ros2` installed "
            "falls back to `mock`)."
        )
    )
    requested_mode: str = Field(description="Mode requested via configuration (may be `auto`).")
    ros2_available: bool = Field(description="Whether a `ros2` CLI is on PATH.")
    ros2_distro: str | None = Field(
        default=None,
        description=(
            "Value of `ROS_DISTRO` if set in the environment. **Env "
            "disclosure, by design**: under the local-trust threat model "
            "(see README 'Security model'), the MCP client is a trusted "
            "agent on a machine the user controls, and exposing the ROS2 "
            "distro lets it adapt to e.g. `humble`/`jazzy` differences. "
            "For a hosted multi-tenant TopicForge endpoint this field "
            "would be scrubbed ."
        ),
    )
    server_version: str = Field(
        description="TopicForge server version (matches the PyPI release of the `topicforge` package)."
    )
    max_sample_count: int = Field(
        ge=0,
        description=(
            "Server-side cap on the number of samples returned per "
            "`sample_messages` call. Requests above this limit are silently "
            "clamped; the value is exposed here so a client can size its "
            "requests proactively. Constant within a given server version."
        ),
    )
    dds_backend: Literal[
        "mock",
        "cyclone",
        "fast",
        "opendds",
        "dust",
        "none",
    ] = Field(
        default="none",
        description=(
            "DDS backend of the adapter actually serving requests. `none` "
            "when the DDS module is not active (default for ROS2-only "
            "installs). `mock` for synthetic fixtures. `cyclone` requires "
            '`pip install "topicforge[dds-cyclone]"` (Eclipse CycloneDDS); '
            "`fast` requires a Fast DDS Python binding built from eProsima "
            "sources (not on PyPI); `opendds` and `dust` are permanent stub "
            "adapters that never serve."
        ),
    )
    dds_inactive_reason: str | None = Field(
        default=None,
        description=(
            "Why `dds_backend` is `none` while the ROS 2 CLI serves: the "
            "backend was not selected (`TOPICFORGE_DDS_BACKEND` unset or "
            "`mock`), its Python binding is not installed, or the binding "
            "is installed but the adapter failed to start. `null` when a "
            "DDS backend is serving or the cause is not known."
        ),
    )
    dds_domain_id: int | None = Field(
        default=None,
        ge=0,
        le=232,
        description="DDS domain id observed when the DDS module is active.",
    )
    observed_domain_note: str | None = Field(
        default=None,
        description=(
            "Plain statement of which DDS domain is observed, set when a DDS module "
            "is active: only the domain joined at startup is visible, a program on "
            "another domain is invisible."
        ),
    )
    middleware_available: bool = Field(
        default=False,
        description=(
            "True when a DDS backend is serving (`dds_backend` is not "
            "`none`). When the DDS module is inactive (`dds_backend == "
            "'none'`), whether the *configured* backend's Python bindings "
            "are importable, so a missing binding is visible."
        ),
    )
    now_ns: int | None = Field(
        default=None,
        ge=0,
        description="Server wall-clock time (ns since epoch) when this report was built.",
    )
    observer_started_ns: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Wall-clock time (ns since epoch) when the DDS observer joined the "
            "bus. Nothing earlier than this was watched: `now_ns` minus this "
            "is how long TopicForge has been observing. `None` without a live "
            "DDS observer."
        ),
    )
    tracker_running: bool | None = Field(
        default=None,
        description=(
            "Whether the continuous discovery tracker thread is alive "
            "(Cyclone). `None` when the backend has no tracker."
        ),
    )
    tracker_passes: int | None = Field(
        default=None, ge=0, description="Completed discovery tracker passes since start."
    )
    tracker_errors: int | None = Field(
        default=None,
        ge=0,
        description="Discovery tracker passes that raised (swallowed and logged). Non-zero means gaps.",
    )
    tracker_last_pass_ns: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Wall-clock time (ns since epoch) of the last completed tracker "
            "pass. A value far older than `now_ns` means lifecycle is stale."
        ),
    )
    tracker_cache_evictions: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Discovery entries dropped because a tracker cache was full (4096 "
            "per cache). Non-zero means the bus is bigger than what is listed."
        ),
    )
    ros_tools_available: bool = Field(
        default=False,
        description=(
            "True when the ROS 2 tools (`list_topics`, `get_topic_info`, "
            "`sample_messages`, `analyze_bag`, `peek_bag_samples`) can run, "
            "i.e. `ros_backend` is not `none`. False on a DDS-only setup: "
            "use `list_endpoints` for topics and wiring there."
        ),
    )
    sim_clock_published: bool | None = Field(
        default=None,
        description=(
            "Whether `/clock` has at least one publisher on the ROS 2 graph. "
            "True suggests nodes may run on simulated time (`use_sim_time`), "
            "so `header.stamp` values are sim time, not wall time (compare "
            "with `received_ns`). It is a hint: a publisher on `/clock` does "
            "not prove a given node follows it. `None` in mock mode or when "
            "no `list_topics` call (or `get_topic_info` on `/clock`) has read the "
            "graph in the last two minutes: `health_check` never runs the `ros2` "
            "CLI itself, it reports what the last graph read saw."
        ),
    )
    payload_decoding: Literal["disabled", "enabled"] = Field(
        default="disabled",
        description=(
            "Whether DDS user-topic payloads are decoded. `disabled` today: "
            "`peek_dds_samples` and `topic_metrics` do not return message "
            "content for user topics."
        ),
    )
    payload_decoding_reason: str | None = Field(
        default=(
            "user-topic payload decoding is switched off until it is validated "
            "on a real bus; builtin discovery topics are still readable"
        ),
        description="One-line reason for `payload_decoding`.",
    )
    dds_security: Literal["not_supported"] = Field(
        default="not_supported",
        description=(
            "DDS Security is not handled. On a secured domain TopicForge can "
            "show participants but not protected endpoints or data."
        ),
    )
    ros_backend: Literal["mock", "ros2_cli", "none"] = Field(
        default="none",
        description=(
            "Active ROS2 backend. `ros2_cli` when the `ros2` CLI is on "
            "PATH and live mode resolves to a Ros2CliAdapter (alone or "
            "as the ROS half of a composite). `mock` when MockAdapter "
            "serves the ROS surface. `none` when no ROS2 backend is "
            "active (e.g. DDS-only live install with no `ros2` CLI). "
            "Together with `dds_backend` it tells the ROS2 and DDS halves "
            "of the runtime apart."
        ),
    )


class EndpointInfo(BaseModel):
    """One DDS endpoint (writer or reader) announced through discovery."""

    model_config = _CONFIG

    guid: str = Field(description="GUID of the endpoint, `xxxxxxxx.xxxxxxxx.xxxxxxxx.xxxxxxxx`.")
    role: Literal["writer", "reader"] = Field(
        description="`writer` publishes the topic; `reader` subscribes to it."
    )
    participant_guid: str = Field(
        description="GUID of the owning participant, same format as `list_participants`."
    )
    participant_name: str | None = Field(
        default=None,
        description="Announced name of the owning participant, `None` when it set none.",
    )
    participant_vendor: _DdsVendor = Field(
        default="unknown",
        description=(
            "Vendor of the owning participant, same value as `list_participants` "
            "(`unknown` when it cannot be determined)."
        ),
    )
    topic: str = Field(description="DDS topic name.")
    type_name: str | None = Field(default=None, description="Announced data type name.")
    type_id: str | None = Field(
        default=None,
        description="Compact XTypes type identifier (`COMPLETE:<hex>`), `None` when not announced.",
    )
    qos: QosProfile | None = Field(
        default=None,
        description=(
            "QoS the endpoint announced. Durations (`deadline_ns`, "
            "`liveliness_lease_ns`, `latency_budget_ns`) of `None` mean "
            "infinite or not set. `None` when reliability or durability "
            "could not be resolved."
        ),
    )
    announced_ns: int | None = Field(
        default=None,
        description=(
            "Source timestamp of the discovery announcement, ns since epoch, "
            "read on the announcing side's clock (it can differ from this "
            "host's clock). `None` when not available."
        ),
    )
    is_observer: bool = Field(
        description="True when the endpoint belongs to TopicForge's own observer participant."
    )
    gone_ns: int | None = Field(
        default=None,
        description=(
            "`None` for a live endpoint. For a departed endpoint (only listed with "
            "`include_departed`): when its participant was lost (the participant's "
            "`lost_ns`, an upper bound of the death)."
        ),
    )
    activity: None = Field(
        default=None,
        description="Reserved for a future liveness signal. Always `None` today.",
    )
    activity_note: str = Field(
        default=(
            "not observed: TopicForge holds no reader on user topics, so it cannot "
            "tell a silent or hung writer from a healthy one"
        ),
        description="Why `activity` is not populated.",
    )
    domain_id: int = Field(ge=0, le=232, description="DDS domain the endpoint was observed on.")
    mode_effective: Literal["mock", "live"] = Field(description=_MODE_EFFECTIVE_DESC)


class DepartedEndpoint(BaseModel):
    """An endpoint whose participant left the bus (crash, clean exit or lease expiry)."""

    model_config = _CONFIG

    guid: str = Field(description="GUID of the departed endpoint.")
    participant_guid: str = Field(description="GUID of the participant that owned it.")
    participant_name: str | None = Field(
        default=None, description="Announced name of that participant, `None` when it set none."
    )
    gone_ns: int | None = Field(
        default=None, description="When the participant was lost, ns since epoch (upper bound)."
    )


class TopicSummary(BaseModel):
    """Per-topic roll-up of the listed endpoints, for spotting orphans."""

    model_config = _CONFIG

    topic: str = Field(description="DDS topic name.")
    type_names: list[str] = Field(description="Distinct type names announced on this topic.")
    writer_count: int = Field(ge=0, description="Number of listed writers.")
    reader_count: int = Field(ge=0, description="Number of listed readers.")
    partitions: list[str] = Field(
        description='Union of the endpoints\' partitions, sorted. `""` is the default partition.'
    )
    departed_writers: list[DepartedEndpoint] = Field(
        default_factory=list,
        description=(
            "Writers on this topic whose participant left, newest first (bounded "
            "memory: last 200 departed endpoints, 1 h). Explains a topic that lost "
            "its only writer."
        ),
    )
    departed_readers: list[DepartedEndpoint] = Field(
        default_factory=list,
        description="Readers on this topic whose participant left, newest first.",
    )
    orphan: Literal["no_reader", "no_writer"] | None = Field(
        default=None,
        description=(
            "`no_reader`: writers but no reader. `no_writer`: readers but no "
            "writer. `None` when both sides exist."
        ),
    )


class EndpointListing(BaseModel):
    """Envelope returned by `list_endpoints`."""

    model_config = _CONFIG

    domain_id: int = Field(ge=0, le=232, description="DDS domain observed.")
    snapshot_ns: int = Field(description="Wall-clock time of the snapshot, ns since epoch.")
    observer_guid: str | None = Field(
        default=None, description="GUID of TopicForge's own participant, `None` in mock."
    )
    endpoints: list[EndpointInfo] = Field(
        description="Matching endpoints, capped (see `truncated`)."
    )
    by_topic: list[TopicSummary] = Field(description="Roll-up over every matching endpoint.")
    total_discovered: int = Field(
        ge=0, description="Endpoints in the discovery cache before any filter."
    )
    returned: int = Field(ge=0, description="Length of `endpoints`.")
    truncated: bool = Field(description="True when matching endpoints exceeded the cap.")
    departed_endpoints: int = Field(
        default=0,
        ge=0,
        description=(
            "Departed endpoints (their participant left) matching the filters. They "
            "are in `endpoints` only with `include_departed`; `by_topic` always "
            "carries them as `departed_writers` / `departed_readers`."
        ),
    )
    excluded_observer_endpoints: int = Field(
        default=0,
        ge=0,
        description=(
            "Endpoints of TopicForge's own observer participant left out of "
            "`endpoints` (they are counted in `total_discovered`). Explains "
            "`total_discovered` vs `returned` together with the filters."
        ),
    )
    note: str | None = Field(
        default=None,
        description=("Hint when a `topic` filter matched nothing: names the closest known topics."),
    )
    mode_effective: Literal["mock", "live"] = Field(description=_MODE_EFFECTIVE_DESC)
