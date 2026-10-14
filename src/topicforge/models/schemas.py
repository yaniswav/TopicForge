"""Tool input/output schemas.

Models are frozen and JSON-friendly. `extra="forbid"` makes an accidental
extra key fail in tests instead of reaching clients.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from topicforge.models.summaries import MessageSummary, TopicRate

_CONFIG = ConfigDict(extra="forbid", frozen=True)

_MODE_EFFECTIVE_DESC = "`live` or `mock` (fixtures)."
_NOTE_DESC = "One sentence of context, or `None`."

# `mode_effective` is on every top-level result model once, in the envelope,
# except `HealthReport` (it has `mode` / `requested_mode`). Items nested in a
# listing (`ParticipantInfo`, `ParticipantEvent`, `EndpointInfo`,
# `MismatchReport`, `MessageSample`, `TopicListItem`) do not repeat it, and
# `EndpointInfo` does not repeat `domain_id` either: see docs/CONTRACT.md 1.2.


class QosProfile(BaseModel):
    """QoS profile of one DDS endpoint (reader or writer).

    Covers the policies behind most "subscriber receives nothing" cases.
    Vendor-specific extensions are ignored.
    """

    model_config = _CONFIG

    reliability: Literal["RELIABLE", "BEST_EFFORT"] = Field(
        description=(
            "`RELIABLE` retries lost samples, `BEST_EFFORT` does not; a RELIABLE reader"
            " cannot match a BEST_EFFORT writer."
        )
    )
    durability: Literal["VOLATILE", "TRANSIENT_LOCAL", "TRANSIENT", "PERSISTENT"] = Field(
        description=(
            "`VOLATILE` keeps nothing for late joiners, `TRANSIENT_LOCAL` does; a "
            "TRANSIENT_LOCAL reader cannot match a VOLATILE writer."
        )
    )
    history: Literal["KEEP_LAST", "KEEP_ALL"] | None = Field(
        default=None,
        description=(
            "`KEEP_LAST` (ring of `history_depth`) or `KEEP_ALL`; `None` when discovery"
            " did not announce it (see `history_note`)."
        ),
    )
    history_depth: int | None = Field(
        default=None,
        ge=0,
        description="Depth for `KEEP_LAST`; else `None`.",
    )
    history_note: str | None = Field(
        default=None,
        description="Why `history` is `None`.",
    )
    deadline_ns: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Deadline in ns; `None` is infinite. A reader deadline shorter than the "
            "writer's is incompatible."
        ),
    )
    liveliness_kind: Literal["AUTOMATIC", "MANUAL_BY_PARTICIPANT", "MANUAL_BY_TOPIC"] | None = (
        Field(
            default=None,
            description=(
                "`AUTOMATIC`, or `MANUAL_*` (the app must assert liveliness within "
                "`liveliness_lease_ns`); `None` when not announced."
            ),
        )
    )
    liveliness_lease_ns: int | None = Field(
        default=None,
        ge=0,
        description=("Liveliness lease in ns; `None` is infinite or not announced."),
    )
    ownership_kind: Literal["SHARED", "EXCLUSIVE"] | None = Field(
        default=None,
        description=(
            "With `EXCLUSIVE` only the writer with the highest `ownership_strength` "
            "delivers; `None` when not announced."
        ),
    )
    ownership_strength: int | None = Field(
        default=None,
        description=("Writer strength under `EXCLUSIVE` ownership; `None` when not announced."),
    )
    partitions: list[str] | None = Field(
        default=None,
        description=(
            'Partition names; the default partition is `[""]`, never `[]`. Endpoints '
            "match only if partitions intersect; `None` when unreadable."
        ),
    )
    latency_budget_ns: int | None = Field(
        default=None,
        ge=0,
        description=(
            "LatencyBudget in ns (a hint); `0` is the default, `None` is infinite or not announced."
        ),
    )
    destination_order: Literal["BY_RECEPTION_TIMESTAMP", "BY_SOURCE_TIMESTAMP"] | None = Field(
        default=None,
        description="DestinationOrder QoS. `None` when not announced.",
    )
    data_representation: list[str] | None = Field(
        default=None,
        description=("`XCDR1` / `XCDR2` accepted or offered; `None` when not announced."),
    )
    unknown_policies: list[str] | None = Field(
        default=None,
        description=(
            "Policies announced but unreadable (e.g. `Deadline`): their fields are "
            "`None`, which does NOT mean infinite; the scan does not compare them."
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

    guid: str = Field()
    name: str | None = Field(
        default=None,
        description=(
            "Announced EntityName (e.g. `lidar_driver`); `None` when the application "
            "set none (Dust DDS cannot)."
        ),
    )
    vendor: _DdsVendor = Field(
        description=(
            "DDS implementation read from the GUID vendor prefix: cyclone, fast, rti, "
            "rti_micro, opensplice, opendds, coredx, intercom, dust, mock or `unknown` "
            "(Dust DDS and RTI Connext do not put theirs there); see `vendor_source`."
        )
    )
    hostname: str | None = Field(
        default=None,
    )
    vendor_source: Literal["guid_prefix", "none"] = Field(
        default="none",
        description=("`guid_prefix` when `vendor` was read from the GUID, else `none`."),
    )
    is_observer: bool = Field(
        default=False,
        description="True for TopicForge's own read-only observer participant.",
    )
    domain_id: int = Field(
        ge=0,
        le=232,
    )
    first_seen_ns: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Wall-clock ns of the first discovery sample seen; `None` without lifecycle tracking."
        ),
    )
    last_seen_ns: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Wall-clock ns of the latest discovery sample; `None` without lifecycle tracking."
        ),
    )
    status: Literal["active", "left", "unknown"] = Field(
        default="unknown",
        description=(
            "`active`, `left` (seen earlier, since gone) or `unknown` (no lifecycle tracking)."
        ),
    )
    seen_count: int = Field(
        default=1,
        ge=1,
        description=(
            "Discovery observations of this GUID in this server's lifetime. Not a "
            "restart count: a restart is a new GUID (see `restarts_by_name`)."
        ),
    )
    announced_ns: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Source timestamp of the latest announcement, on the announcer's clock; "
            "`None` if unavailable."
        ),
    )
    lost_ns: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Once `left`: ns upper bound of the death (exact after a clean shutdown; "
            "after a crash the lease expiry, up to one lease earlier: 10 s Cyclone, 20 "
            "s Fast, 100 s RTI). `None` while active."
        ),
    )
    lost_time_source: TimeSource | None = Field(
        default=None,
        description=(
            "Origin of `lost_ns`: `dds_source_timestamp` or `observed_local` (weakest);"
            " `None` while active."
        ),
    )
    node_names: list[str] = Field(
        default_factory=list,
        description=(
            "Fully qualified ROS 2 node names sharing this participant; empty when "
            "`node_names_source` is `none`."
        ),
    )
    node_names_source: Literal["ros_discovery_info", "ros2_cli", "none"] = Field(
        default="none",
        description="`ros_discovery_info`, `ros2_cli` or `none` (no link made).",
    )


class NameLifecycle(BaseModel):
    """How often one participant name appeared and left: lives = distinct GUIDs."""

    model_config = _CONFIG

    lives: int = Field(
        ge=1, description="Distinct GUIDs that carried this name; restarts = `lives` - 1."
    )
    departures: int = Field(ge=0, description="Times a participant with this name was lost.")


_RESTARTS_DESC = (
    "Names seen with several GUIDs or lost at least once (absent: ran once and stayed). "
    "A restart is a new GUID with the same name."
)
_RESTARTS_NOTE_DESC = "Set when unnamed participants were left out of `restarts_by_name`."


class ParticipantListing(BaseModel):
    """Envelope returned by `list_participants`."""

    model_config = _CONFIG

    participants: list[ParticipantInfo] = Field()
    returned: int = Field(
        ge=0,
    )
    total: int = Field(
        ge=0,
    )
    truncated: bool = Field()
    domain_id: int = Field(ge=0, le=232, description="DDS domain observed (joined at startup).")
    mode_effective: Literal["mock", "live"] = Field(description=_MODE_EFFECTIVE_DESC)
    note: str | None = Field(
        default=None,
        description=_NOTE_DESC,
    )
    restarts_by_name: dict[str, NameLifecycle] = Field(
        default_factory=dict, description=_RESTARTS_DESC
    )
    restarts_note: str | None = Field(default=None, description=_RESTARTS_NOTE_DESC)


class ParticipantEvent(BaseModel):
    """A point-in-time lifecycle event (discovered or lost) for a DDS participant.

    `ParticipantInfo` is the current state; this is one event in its history.
    Returned by `participant_events`.
    """

    model_config = _CONFIG

    guid: str = Field()
    event_type: Literal["discovered", "lost"] = Field(
        description=(
            "`discovered` (joined or rejoined) or `lost` (removed, or gone from "
            "discovery); reported once per change."
        )
    )
    vendor: _DdsVendor = Field()
    timestamp_ns: int = Field(
        ge=0,
        description="Wall-clock ns of the event; its origin is `time_source`.",
    )
    hostname: str | None = Field(
        default=None,
    )
    name: str | None = Field(
        default=None,
    )
    domain_id: int = Field(
        ge=0,
        le=232,
    )
    time_source: TimeSource = Field(
        default="observed_local",
        description=(
            "`dds_source_timestamp` (DDS timestamp of the announcement or dispose; for "
            "`lost` an upper bound of the death) or `observed_local` (when TopicForge "
            "noticed, weakest)."
        ),
    )
    observed_ns: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Local ns when TopicForge noticed the event, never before a DDS-derived "
            "`timestamp_ns`; `None` when untracked."
        ),
    )
    age_s: float | None = Field(
        default=None,
        ge=0,
        description="Seconds between `timestamp_ns` and this response; `None` in mock mode.",
    )


class ParticipantEventListing(BaseModel):
    """Envelope returned by `participant_events`."""

    model_config = _CONFIG

    events: list[ParticipantEvent] = Field()
    returned: int = Field(
        ge=0,
    )
    total: int = Field(
        ge=0,
    )
    truncated: bool = Field(description="True when cut to the 200-event cap.")
    domain_id: int = Field(ge=0, le=232, description="DDS domain observed (joined at startup).")
    mode_effective: Literal["mock", "live"] = Field(description=_MODE_EFFECTIVE_DESC)
    note: str | None = Field(
        default=None,
        description=_NOTE_DESC,
    )
    restarts_by_name: dict[str, NameLifecycle] = Field(
        default_factory=dict,
        description=_RESTARTS_DESC + " Window only.",
    )
    restarts_note: str | None = Field(default=None, description=_RESTARTS_NOTE_DESC)


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

    topic: str = Field()
    window_s: int = Field(
        ge=1,
        le=3600,
        description="Requested window in seconds (echoed).",
    )
    window_actual_s: float = Field(
        ge=0,
        description=(
            "Seconds actually covered; shorter than `window_s` after a recent start; "
            "`0.0` without samples."
        ),
    )
    samples_observed: int = Field(
        ge=0,
        description=(
            "Samples buffered in the window. `0` does NOT mean no publisher, only that "
            "no `peek_dds_samples` call captured one."
        ),
    )
    observed_frequency_hz: float | None = Field(
        default=None,
        description=("`samples_observed / window_actual_s`; `None` under 2 samples."),
    )
    declared_frequency_hz: float | None = Field(
        default=None,
        description=(
            "`1 / deadline` of the shortest writer Deadline announced: the promised "
            "rate, not the observed one; `None` when none announced."
        ),
    )
    status: Literal["ok", "no_samples_yet", "unsupported_user_topic"] = Field(
        default="ok",
        description=(
            "`ok`; `no_samples_yet` (empty buffer); `unsupported_user_topic` (payload "
            "not decoded: null fields are not measurements)."
        ),
    )
    sequence_gaps_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Missing sequence numbers in the buffered samples; `0` also when none exist"
            " (see `sequence_numbers_available`)."
        ),
    )
    sequence_numbers_available: bool = Field(
        default=False,
        description="True when a sequence number could be read from a sample.",
    )
    latency_ns_p50: int | None = Field(
        default=None,
        ge=0,
        description=("Median publish-to-receive latency in ns; `None` unless `latency_available`."),
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
        description=("True when a sample carried both a publish and a receive timestamp."),
    )
    mode_effective: Literal["mock", "live"] = Field(description=_MODE_EFFECTIVE_DESC)
    note: str | None = Field(
        default=None,
        description=_NOTE_DESC,
    )


class PolicyMismatch(BaseModel):
    """One QoS policy of a reader/writer pair, with the values that were compared."""

    model_config = _CONFIG

    policy: str = Field(
        description=("Policy name, e.g. `Reliability`, `Deadline`, `Ownership`, `History`.")
    )
    requested: str = Field(description='What the reader requests, e.g. `"RELIABLE"`, `"100 ms"`.')
    offered: str = Field()
    rule: str = Field(description="The compatibility rule that failed, one sentence.")


class SuggestedFix(BaseModel):
    """A fix proposed as text for a human to apply. TopicForge never applies it."""

    model_config = _CONFIG

    kind: Literal["cli", "qos_override", "dds_profile", "launch_param"] = Field(
        description="Form of `text`: a CLI command, a ROS 2 QoS override, a DDS profile or a launch parameter."
    )
    target: str = Field(description="Which node or side to change, writer or reader.")
    text: str = Field(description="The exact command, YAML or XML to apply.")
    tradeoff: str = Field(description="What is lost by applying it.")
    verify_with: str = Field(description="The TopicForge tool to run again to confirm the repair.")


class MismatchReport(BaseModel):
    """A single reader/writer QoS incompatibility detected on a topic.

    Only pairs that share a partition and a type name are reported here: those
    separated by partition or type are `NotMatchedPair`s in the same scan.
    """

    model_config = _CONFIG

    topic: str = Field()
    reader_guid: str | None = Field(
        default=None,
    )
    writer_guid: str | None = Field(
        default=None,
    )
    reader_participant_guid: str | None = Field(
        default=None,
    )
    reader_participant_name: str | None = Field(
        default=None,
    )
    writer_participant_guid: str | None = Field(
        default=None,
    )
    writer_participant_name: str | None = Field(
        default=None,
    )
    reader_type_name: str | None = Field(
        default=None,
    )
    writer_type_name: str | None = Field(
        default=None,
    )
    incompatible_policies: list[str] = Field(
        description="Blocking or risky policies; values in `details`."
    )
    severity: Literal["incompatible", "risky"] = Field(
        description=("`incompatible` (blocked) or `risky` (may degrade; History only).")
    )
    details: list[PolicyMismatch] = Field(
        default_factory=list,
        description="Requested and offered value and the failed rule, per policy.",
    )
    unchecked: list[str] = Field(
        default_factory=list,
        description="Policies not compared because one side announced no value.",
    )
    suggested_fixes: list[SuggestedFix] = Field(
        default_factory=list,
        description=("Fixes as text for a human to apply, never applied; empty when none."),
    )


class NotMatchedPair(BaseModel):
    """A reader and a writer on one topic that DDS will not match, whatever their QoS."""

    model_config = _CONFIG

    topic: str = Field()
    reader_guid: str = Field()
    reader_participant_guid: str = Field()
    reader_participant_name: str | None = Field(
        default=None,
    )
    writer_guid: str = Field()
    writer_participant_guid: str = Field()
    writer_participant_name: str | None = Field(
        default=None,
    )
    reason: Literal["partition", "type_name"] = Field(
        description=(
            "`partition` (none in common) or `type_name` (different types); QoS rules "
            "are not evaluated."
        )
    )
    detail: str = Field(description="The two partition lists or the two type names.")
    latent_incompatible_policies: list[PolicyMismatch] = Field(
        default_factory=list,
        description=(
            "RxO policies that would ALSO be incompatible once the partition or type is"
            " fixed (shape of `MismatchReport.details`)."
        ),
    )


class MatchedPair(BaseModel):
    """A reader and a writer that DDS will connect given their announced QoS."""

    model_config = _CONFIG

    topic: str = Field()
    type_name: str | None = Field(
        default=None,
    )
    reader_guid: str = Field()
    reader_participant_guid: str = Field()
    reader_participant_name: str | None = Field(
        default=None,
    )
    writer_guid: str = Field()
    writer_participant_guid: str = Field()
    writer_participant_name: str | None = Field(
        default=None,
    )
    late_joiner: bool = Field(
        default=False,
        description=(
            "True for a VOLATILE writer whose reader joined over 1 s later on the same "
            "host: earlier samples are not delivered. Normal."
        ),
    )
    late_joiner_note: str | None = Field(
        default=None, description="One-line explanation, set together with `late_joiner`."
    )


class MismatchScan(BaseModel):
    """Result of `detect_qos_mismatches`: findings plus what was and was not checked."""

    model_config = _CONFIG

    reports: list[MismatchReport] = Field(
        description=("Pairs sharing partition and type with incompatible or risky QoS.")
    )
    not_matched: list[NotMatchedPair] = Field(
        description=(
            "Pairs DDS never matches (partition or type name). Non-empty with empty "
            "`reports` is NOT all-clear: no data flows between them."
        )
    )
    matched: list[MatchedPair] = Field(
        default_factory=list,
        description=("Pairs DDS will connect given the announced QoS (data flow is not observed)."),
    )
    hints: list[str] = Field(
        description=(
            "Leads that are not findings: probable typos, type id notes, incomplete checks."
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
            "True when `reports`, `matched` or `not_matched` hit the 200-entry cap (see"
            " `*_total`); narrow with `topic`."
        ),
    )
    summary: str | None = Field(
        default=None,
        description="One-line reading of the scan: counts of incompatible, not matched and matched pairs.",
    )
    pairs_checked: int = Field(
        ge=0,
        description="Same-topic (reader, writer) pairs examined.",
    )
    topics_scanned: int = Field(ge=0, description="Topics that had at least one endpoint in scope.")
    policies_checked: list[str] = Field(description="Policies compared on every pair.")
    policies_unchecked: list[str] = Field(
        description=(
            "What the scan does not cover, each with a reason; a clean result says "
            "nothing about them."
        )
    )
    mode_effective: Literal["mock", "live"] = Field(description=_MODE_EFFECTIVE_DESC)
    note: str | None = Field(
        default=None,
        description=_NOTE_DESC,
    )


class TopicListItem(BaseModel):
    """One ROS2 topic as listed by `list_topics`: identity and endpoint counts, no QoS."""

    model_config = _CONFIG

    name: str = Field()
    message_type: str = Field()
    publisher_count: int = Field(
        ge=0,
    )
    subscriber_count: int = Field(
        ge=0,
    )


class TopicListing(BaseModel):
    """Envelope returned by `list_topics`."""

    model_config = _CONFIG

    topics: list[TopicListItem] = Field()
    returned: int = Field(
        ge=0,
    )
    total: int = Field(
        ge=0,
    )
    truncated: bool = Field()
    mode_effective: Literal["mock", "live"] = Field(description=_MODE_EFFECTIVE_DESC)
    note: str | None = Field(
        default=None,
        description=_NOTE_DESC,
    )


class SideQos(BaseModel):
    """QoS of one side (publishers or subscriptions) of a ROS2 topic, as the `ros2` CLI reports it."""

    model_config = _CONFIG

    reliability: Literal["reliable", "best_effort", "mixed"] = Field(
        description="Reliability shared by the endpoints of this side, or `mixed` when they disagree."
    )
    durability: Literal["volatile", "transient_local", "mixed"] = Field(
        description=(
            "Shared durability of this side, or `mixed`; `transient_local` marks a "
            "latched topic such as `/tf_static`."
        )
    )
    endpoint_count: int = Field(
        ge=1, description="Endpoints of this side the values were read from."
    )


class TopicInfo(BaseModel):
    """Detail of a single ROS2 topic: counts, QoS per side and the nodes on each side."""

    model_config = _CONFIG

    name: str = Field()
    message_type: str = Field()
    publisher_count: int = Field(
        ge=0,
    )
    subscriber_count: int = Field(
        ge=0,
    )
    publisher_qos: SideQos | None = Field(
        default=None,
        description="QoS of the publishers. `None` when it was not read: see `publisher_qos_note`.",
    )
    publisher_qos_note: str | None = Field(
        default=None, description="Why `publisher_qos` is `None`. `None` when it is set."
    )
    subscription_qos: SideQos | None = Field(
        default=None,
        description="QoS of the subscriptions. `None` when it was not read: see `subscription_qos_note`.",
    )
    subscription_qos_note: str | None = Field(
        default=None, description="Why `subscription_qos` is `None`. `None` when it is set."
    )
    publisher_nodes: list[str] = Field(
        default_factory=list,
        description=("Publisher node names (`ros2 topic info --verbose`); empty when unknown."),
    )
    subscriber_nodes: list[str] = Field(
        default_factory=list,
        description="Subscriber node names; empty when unknown.",
    )
    mode_effective: Literal["mock", "live"] = Field(description=_MODE_EFFECTIVE_DESC)
    note: str | None = Field(
        default=None,
        description=_NOTE_DESC,
    )


class MessageSample(BaseModel):
    """A single sampled message on a topic."""

    model_config = _CONFIG

    topic: str = Field()
    message_type: str = Field()
    timestamp_ns: int = Field(
        description=(
            "Publisher-clock ns: `header.stamp`, else the time in the body of `Clock`, "
            "`TFMessage` or `Log`, else 0 (see `stamp_source`); not the arrival time."
        )
    )
    stamp_source: Literal["header", "payload", "recorded", "dds_source", "none"] = Field(
        description=(
            "Origin of `timestamp_ns`: `header`, `payload`, `recorded` (bag record "
            "time), `dds_source` or `none` (0)."
        ),
    )
    recorded_ns: int | None = Field(
        default=None,
        description="Bag record time in ns (`peek_bag_samples` only).",
    )
    received_ns: int | None = Field(
        default=None,
        description=(
            "Wall-clock ns when the `ros2` CLI printed the message (live `sample_messages` only)."
        ),
    )
    payload: dict[str, object] = Field(
        default_factory=dict,
        description=(
            "Message fields as nested values; `nan`/`inf` are strings. Live "
            "`sample_messages` adds `_truncated_fields` (cut paths) and `_raw_text` "
            "(unparsable output)."
        ),
    )
    summary: MessageSummary | None = Field(
        default=None,
        description=(
            "Condensed reading of the whole message (see `summary_type`); `null` for "
            "other types and DDS peeks."
        ),
    )


class BagTopicStats(BaseModel):
    """Per-topic statistics inside a bag analysis result."""

    model_config = _CONFIG

    name: str = Field()
    message_type: str = Field()
    message_count: int = Field(
        ge=0, description="Number of messages recorded on this topic across the bag."
    )
    kind: Literal["user", "rosbag2_internal", "ros_builtin"] = Field(
        default="user",
        description=(
            "`user` (data, incl. `/tf` and `/clock`), `rosbag2_internal` or "
            "`ros_builtin` (e.g. `/rosout`)."
        ),
    )
    frequency_hz: float | None = Field(
        default=None,
        ge=0,
        description=(
            "Average Hz: (count - 1) over the topic's own time span (see "
            "`frequency_basis`); `null` under 2 messages, a zero span, or a latched "
            "start-up burst (all within 1 s)."
        ),
    )
    first_timestamp_ns: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Recorded ns of the first message; `null` for text-only summaries or no messages."
        ),
    )
    last_timestamp_ns: int | None = Field(
        default=None,
        ge=0,
        description="Recorded ns of the last message; same rules.",
    )
    frequency_basis: Literal["topic_span", "bag_duration"] | None = Field(
        default=None,
        description=(
            "`topic_span` (accurate) or `bag_duration` (count over the whole bag, "
            "understates late topics); `null` without a rate."
        ),
    )
    latched: bool | None = Field(
        default=None,
        description=(
            "True if recorded with transient_local durability, false if volatile, "
            "`null` without QoS information."
        ),
    )


class BagAnalysis(BaseModel):
    """Structured summary of a ROS2 bag.

    `bag_format` is populated only when the `rosbags`-backed reader runs; the
    `ros2 bag info` path leaves it `None`.
    """

    model_config = _CONFIG

    path: str = Field(description="Bag path as supplied.")
    storage_format: str | None = Field(
        default=None,
        description="`mcap`, `sqlite3`, or other storage identifier when known.",
    )
    duration_s: float = Field(
        ge=0,
        description="Bag duration in seconds.",
    )
    message_count: int = Field(
        ge=0, description="Total number of messages across all recorded topics."
    )
    topics: list[BagTopicStats] = Field(
        description="Per-topic statistics for every topic present in the bag."
    )
    anomalies: list[str] = Field(
        default_factory=list,
        description="Notes on gaps or clock jumps; mock mode only.",
    )
    mode_effective: Literal["mock", "live"] = Field(description=_MODE_EFFECTIVE_DESC)
    note: str | None = Field(
        default=None,
        description=(
            "Why the result is less detailed than usual (e.g. rates over the whole bag "
            "duration); else `None`."
        ),
    )
    bag_format: Literal["mcap", "db3", "bag", "unknown"] | None = Field(
        default=None,
        description=(
            "Detected container: `mcap`, `db3`, `bag` or `unknown`; `None` for `ros2 "
            "bag info` text."
        ),
    )


class SampleResult(BaseModel):
    """Samples returned by `sample_messages`, `peek_dds_samples` and `peek_bag_samples`."""

    model_config = _CONFIG

    topic: str = Field()
    count: int = Field(
        ge=0,
        description=(
            "Number of samples in `samples`: fewer than requested when the topic gave "
            "fewer, never more than 50 (a larger request is capped to 50)."
        ),
    )
    samples: list[MessageSample] = Field(description="The samples, in backend order.")
    rate: TopicRate | None = Field(
        default=None,
        description=(
            "How the topic delivered the messages, with a verdict; `null` for "
            "`peek_dds_samples`. Use `count` >= 10."
        ),
    )
    mode_effective: Literal["mock", "live"] = Field(description=_MODE_EFFECTIVE_DESC)
    note: str | None = Field(
        default=None,
        description=("Why `samples` is empty or limited, when not obvious; else `None`."),
    )


class HealthReport(BaseModel):
    """Result of `health_check`. Always succeeds, even when the host is unhealthy."""

    model_config = _CONFIG

    mode: str = Field(
        description=(
            "Mode actually serving: `mock` or `live` (`live` with `ros_backend` `none`:"
            " DDS tools only). May differ from `requested_mode` after a fallback."
        )
    )
    requested_mode: str = Field(description="Mode requested by configuration (may be `auto`).")
    ros2_available: bool = Field(description="Whether a `ros2` CLI is on PATH.")
    ros2_distro: str | None = Field(
        default=None,
        description=("`ROS_DISTRO` if set (disclosed by design under the local-trust model)."),
    )
    server_version: str = Field(description="TopicForge version.")
    contract_version: int = Field(
        default=2,
        ge=1,
        description=(
            "Output contract version; changes only when a field is renamed, removed or retyped."
        ),
    )
    max_sample_count: int = Field(
        ge=0,
        description=("Server cap on samples per call; larger requests are clamped."),
    )
    dds_backend: Literal[
        "mock",
        "cyclone",
        "fast",
        "none",
    ] = Field(
        default="none",
        description=(
            "DDS backend serving: `mock`, `cyclone`, `fast`, or `none` (DDS module inactive)."
        ),
    )
    dds_inactive_note: str | None = Field(
        default=None,
        description=(
            "Why `dds_backend` is `none` while ROS 2 serves (not selected, binding "
            "missing, or failed to start); else `null`."
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
            "Which DDS domain is observed: only the one joined at startup; others are invisible."
        ),
    )
    middleware_available: bool = Field(
        default=False,
        description=(
            "True when a DDS backend serves; else whether the configured binding is importable."
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
            "ns when the DDS observer joined; nothing earlier was watched. `None` without one."
        ),
    )
    tracker_running: bool | None = Field(
        default=None,
        description=(
            "Whether the discovery tracker thread is alive (Cyclone); `None` without a tracker."
        ),
    )
    tracker_passes: int | None = Field(
        default=None, ge=0, description="Completed discovery tracker passes since start."
    )
    tracker_errors: int | None = Field(
        default=None,
        ge=0,
        description="Tracker passes that raised; non-zero means gaps.",
    )
    tracker_last_pass_ns: int | None = Field(
        default=None,
        ge=0,
        description=("ns of the last tracker pass; far older than `now_ns` means stale lifecycle."),
    )
    tracker_cache_evictions: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Discovery entries dropped from a full tracker cache (4096 each); non-zero "
            "means the bus is bigger than listed."
        ),
    )
    ros_tools_available: bool = Field(
        default=False,
        description=(
            "True when the ROS 2 tools can run (`ros_backend` is not `none`); on a DDS-"
            "only setup use `list_endpoints`."
        ),
    )
    sim_clock_published: bool | None = Field(
        default=None,
        description=(
            "Whether `/clock` had a publisher in the last graph read (a hint that "
            "stamps may be sim time). `None` in mock or without a recent read; "
            "`health_check` never runs `ros2`."
        ),
    )
    payload_decoding: Literal["disabled", "enabled"] = Field(
        default="disabled",
        description="`disabled`: DDS user-topic payloads are not decoded.",
    )
    payload_decoding_note: str | None = Field(
        default=(
            "user-topic payload decoding is switched off until it is validated "
            "on a real bus; builtin discovery topics are still readable"
        ),
        description="Reason for `payload_decoding`.",
    )
    dds_security: Literal["not_supported"] = Field(
        default="not_supported",
        description=("DDS Security is not handled: protected endpoints and data are invisible."),
    )
    rmw_implementation: str | None = Field(
        default=None,
        description=(
            "ROS 2 middleware, e.g. `rmw_fastrtps_cpp`; `None` when `rmw_source` is `none`."
        ),
    )
    rmw_source: Literal["env", "ros2_cli", "distro_default", "none"] = Field(
        default="none",
        description=("`env`, `ros2_cli`, `distro_default` (not verified on the graph) or `none`."),
    )
    ros_backend: Literal["mock", "ros2_cli", "none"] = Field(
        default="none",
        description="ROS 2 backend: `ros2_cli`, `mock` or `none`.",
    )
    note: str | None = Field(
        default=None,
        description=_NOTE_DESC,
    )


class EndpointInfo(BaseModel):
    """One DDS endpoint (writer or reader) announced through discovery."""

    model_config = _CONFIG

    guid: str = Field()
    role: Literal["writer", "reader"] = Field(
        description="`writer` publishes the topic; `reader` subscribes to it."
    )
    participant_guid: str = Field()
    participant_name: str | None = Field(
        default=None,
    )
    participant_vendor: _DdsVendor = Field(
        default="unknown",
    )
    dds_topic: str = Field()
    ros_topic: str | None = Field(
        default=None,
        description=(
            "ROS 2 name of the topic, for example `/scan`. `None` when the endpoint is not "
            "a ROS 2 topic: see `ros_topic_note`."
        ),
    )
    ros_topic_note: str | None = Field(
        default=None, description="Why `ros_topic` is `None`. `None` when it is set."
    )
    type_name: str | None = Field(
        default=None,
    )
    type_id: str | None = Field(
        default=None,
        description="Compact XTypes type identifier (`COMPLETE:<hex>`), `None` when not announced.",
    )
    qos: QosProfile | None = Field(
        default=None,
        description=(
            "Announced QoS; `None` when reliability or durability could not be "
            "resolved. A duration of `None` is infinite or unset."
        ),
    )
    announced_ns: int | None = Field(
        default=None,
        description=(
            "Source timestamp of the announcement, on the announcer's clock; `None` if unavailable."
        ),
    )
    is_observer: bool = Field(
        description="True when the endpoint belongs to TopicForge's own observer participant."
    )
    qos_summary: str | None = Field(
        default=None,
        description="One-line QoS, set only with `fields` `compact` (where `qos` is `None`).",
    )
    gone_ns: int | None = Field(
        default=None,
        description=(
            "`None` for a live endpoint; for a departed one (`include_departed`) when "
            "its participant was lost (upper bound)."
        ),
    )


class DepartedEndpoint(BaseModel):
    """An endpoint whose participant left the bus (crash, clean exit or lease expiry)."""

    model_config = _CONFIG

    guid: str = Field()
    participant_guid: str = Field()
    participant_name: str | None = Field(
        default=None,
    )
    gone_ns: int | None = Field(
        default=None, description="When the participant was lost, ns since epoch (upper bound)."
    )


class TopicSummary(BaseModel):
    """Per-topic roll-up of the listed endpoints, for spotting orphans."""

    model_config = _CONFIG

    dds_topic: str = Field()
    ros_topic: str | None = Field(
        default=None,
        description="ROS 2 name of the topic, for example `/scan`. `None` for a DDS topic that is not a ROS 2 topic.",
    )
    type_names: list[str] = Field()
    writer_count: int = Field(
        ge=0,
    )
    reader_count: int = Field(
        ge=0,
    )
    partitions: list[str] = Field(
        description='Union of the endpoints\' partitions, sorted. `""` is the default partition.'
    )
    departed_writers: list[DepartedEndpoint] = Field(
        default_factory=list,
        description=("Writers whose participant left, newest first (last 200, 1 h)."),
    )
    departed_readers: list[DepartedEndpoint] = Field(
        default_factory=list,
        description="Readers whose participant left, newest first.",
    )
    orphan: Literal["no_reader", "no_writer"] | None = Field(
        default=None,
        description=("`no_reader` (writers only), `no_writer` (readers only), else `None`."),
    )


class EndpointListing(BaseModel):
    """Envelope returned by `list_endpoints`."""

    model_config = _CONFIG

    domain_id: int = Field(
        ge=0,
        le=232,
    )
    fields: Literal["full", "compact"] = Field(
        default="full",
        description="Detail level served: `compact` drops per-endpoint QoS detail, type id and notes.",
    )
    snapshot_ns: int = Field(description="Wall-clock time of the snapshot, ns since epoch.")
    observer_guid: str | None = Field(
        default=None, description="GUID of TopicForge's own participant, `None` in mock."
    )
    endpoints: list[EndpointInfo] = Field(
        description="Matching endpoints, capped (see `truncated`)."
    )
    by_topic: list[TopicSummary] = Field(description="Roll-up over every matching endpoint.")
    total: int = Field(ge=0, description="Endpoints in the discovery cache before any filter.")
    returned: int = Field(
        ge=0,
    )
    truncated: bool = Field(description="True when matching endpoints exceeded the cap.")
    departed_endpoint_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Departed endpoints matching the filters; listed in `endpoints` only with "
            "`include_departed`."
        ),
    )
    excluded_observer_endpoint_count: int = Field(
        default=0,
        ge=0,
        description=("TopicForge's own endpoints left out of `endpoints` (counted in `total`)."),
    )
    hidden_internal_endpoint_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Service/action endpoints (`rq/`, `rr/`, `rs/`, `rp/`, `ra/`, "
            "`ros_discovery_info`) left out while `include_internal` is false."
        ),
    )
    hints: list[str] = Field(
        default_factory=list,
        description=("Facts true of every endpoint, stated once (e.g. liveness is not observed)."),
    )
    note: str | None = Field(
        default=None,
        description=("Set when a `topic` filter matched nothing: the closest known topics."),
    )
    mode_effective: Literal["mock", "live"] = Field(description=_MODE_EFFECTIVE_DESC)
