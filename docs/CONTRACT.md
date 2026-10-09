# TopicForge output contract

Status: draft for 0.7.0, pending owner validation. Nothing in this file changes the
behaviour of 0.6.4. The baseline it starts from is recorded in
[`tests/contract/`](../tests/contract) (one JSON file per tool: description, input
schema, output schema, annotations) and rendered in [TOOLS.md](TOOLS.md).

The contract is the shape of what a tool returns and the meaning of its words. It is
what an AI client, a script or a test can rely on between releases. 0.6.4 is
`contract_version` 1 (the field does not exist yet, so 1 is implicit). 0.7.0 is
`contract_version` 2: it is the one breaking release, and the rest of this file
describes it. Section 7 lists every rename and split, so the break happens once.

Sections:

1. Principles
2. Decisions
3. Reserved optional fields
4. Rate verdicts
5. Reserved multimodal exception
6. Change policy and tests
7. 0.7.0 migration list

## 1. Principles

### 1.1 A tool always returns one object

Never a bare list, never a scalar. A collection lives under a plural name, next to its
counts. Today `list_topics`, `list_participants` and `participant_events` return a bare
list, which the SDK wraps as `{"result": [...]}` in `structuredContent` and as one text
block per element in `content`. From 0.7.0:

```json
{
  "topics": [{"name": "/scan", "message_type": "sensor_msgs/msg/LaserScan"}],
  "returned": 1,
  "total": 1,
  "truncated": false,
  "mode_effective": "live",
  "note": null
}
```

`returned` is the number of elements in the list, `total` the number that exist,
`truncated` is true when `returned < total` because of a cap. A collection that has no
cap still carries `returned` and `total` (equal).

### 1.2 One envelope, one `note`

Every top-level result carries `mode_effective` (`"live"` or `"mock"`) and a single
`note`: one sentence, or `null`. There is no `notes` array and no second free-text
field at the top level. When several things need saying, the most important comes first
in `note` and the rest become structured fields. Lists of findings that are data
(`anomalies`, `hints`, `policies_unchecked`) stay lists and are not notes.

```json
{"count": 3, "samples": [], "mode_effective": "live",
 "note": "3 of 5 messages arrived before the 10 s deadline; the publisher is slow."}
```

`health_check` is the one result that has no `mode_effective`: it reports `mode` and
`requested_mode` instead.

### 1.3 No silent null

A `null` must always be explainable. Two mechanisms, applied by construction:

1. A field that a tool never fills does not exist in that tool's model. The list and
   detail views of a topic are two models (`TopicListItem`, `TopicInfo`), and three
   fields that no adapter ever filled are removed (section 7).
2. A field that can be `null` either has a fixed meaning written in its schema
   description ("`null` means no deadline is set"), or has a sibling that says why:
   `<field>_note` (a sentence), `<field>_source` (where the value came from) or
   `<field>_basis` (what it was computed from).

```json
{"subscription_qos": null,
 "subscription_qos_note": "The topic has no subscriber, so there is no QoS to report."}
```

```json
{"timestamp_ns": 0, "stamp_source": "none"}
```

A field is never `null` merely because the backend did not say. `stamp_source` is one
of four strings (decision 2.2), never `null`.

### 1.4 Locked suffix vocabulary

A name that carries a unit or a kind ends with exactly one of these suffixes:

| Suffix | Meaning | Example |
| --- | --- | --- |
| `_ns` | integer nanoseconds (instant or duration) | `timestamp_ns`, `deadline_ns` |
| `_s` | seconds, float | `timeout_s`, `window_s` |
| `_hz` | hertz, float | `frequency_hz` |
| `_count` | number of items in a set | `publisher_count` |
| `_total` | number of items before a cap | `reports_total` |
| `_source` | where a value came from (closed set of strings) | `stamp_source` |
| `_basis` | what a value was computed from (closed set of strings) | `frequency_basis` |
| `_note` | one sentence of explanation for a neighbouring field | `observed_domain_note` |

A new field uses one of these or no suffix. `_reason` is not in the vocabulary and is
removed (section 7). Adding a suffix is a contract change.

### 1.5 Enum casing follows the layer

The casing of a closed set of string values depends on where it comes from:

- A value that is a constant of the DDS specification is `UPPER_SNAKE`:
  `RELIABLE`, `BEST_EFFORT`, `TRANSIENT_LOCAL`, `KEEP_LAST`, `BY_SOURCE_TIMESTAMP`.
  This is the whole of `QosProfile` and everything derived from it.
- A value that is an argument or an output word of the ROS 2 CLI, or a vocabulary owned
  by TopicForge, is `lower_snake`: `reliable`, `best_effort`, `volatile`, `active`,
  `no_reader`, `topic_span`, `header`.

```json
{"qos": {"reliability": "RELIABLE", "durability": "TRANSIENT_LOCAL"}}
{"qos_reliability": "reliable", "qos_durability": "transient_local"}
```

0.6.4 already follows this rule, so it needs no rename; it is written down so a new
field cannot drift.

### 1.6 Errors are `isError` with the `AdapterError` text

A failure is never a successful result with an error body. The handler lets the
exception propagate, and the client receives an MCP result with `isError: true` whose
text is the `AdapterError` message (a user-safe sentence that says what to do).

```json
{"isError": true,
 "content": [{"type": "text",
              "text": "Topic '/nope' is not on the ROS 2 graph. Run list_topics to see what exists."}]}
```

On SDK 2.x, any exception other than `ToolError` is replaced by a generic message, so
`AdapterError` is translated to `ToolError` in `tools/` and a test pins the text.

### 1.7 Descriptions describe present behaviour

A tool or field description says what the tool does now: no version numbers, no
"Phase", no "new in", no "previously", no mention of a tool ceiling. It states the
caveats a caller needs (empty-result behaviour, mock versus live, caps) and the worst-case
duration, which stays under 45 s for every tool. A regex test fails on history words from
0.7.0 rc1.

The 45 s are wall time from the request to the response, lock wait included. The server
enforces it (`topicforge.tools.guard`, with `topicforge.budget` passing the deadline down):

- Calls run in two lanes with one lock each: ROS (`list_topics`, `get_topic_info`,
  `sample_messages`, `analyze_bag`) and DDS (`list_participants`, `detect_qos_mismatches`, `peek_dds_samples`,
  `participant_events`, `topic_metrics`, `list_endpoints`).
  `health_check` never takes a lock and never runs the `ros2` CLI; `peek_bag_samples` reads
  a file in pure Python and takes none either.
- A handler that waited for its lane has that much less time: the `ros2` timeouts and the
  `sample_messages` `timeout_s` are clamped to what is left of the 45 s.
- A call that cannot get its lane while at least 5 s remain fails at once with
  `busy: another <ros|dds> call is running, retry`.

### 1.8 Golden snapshots

The tools exactly as served by `list_tools` in mock mode (name, title, description,
input schema, output schema, annotations) are stored in `tests/contract/<tool>.json`.
`tests/test_contract_snapshots.py` fails with a readable diff on any difference, and
also fails when [TOOLS.md](TOOLS.md) is out of date. An intended change is recorded with:

```
python scripts/contract/snapshot_tools.py --update --docs
```

and the diff of the snapshots is reviewed like any other API change.

### 1.9 Additive only between breaks

Between two `contract_version` values only additions are made: a new optional field, a
new tool, a new value in an open set that the schema documents as extensible. A removal
or a rename goes first through "deprecated" (the description says so and names the
replacement) and is carried out in the next `contract_version`. Closed sets
(`stamp_source`, the rate verdicts) change only at a version break.

## 2. Decisions

### 2.1 Non-finite floats in raw payloads are strings

JSON has no `inf` or `nan`. In a raw message payload (`sample_messages`,
`peek_bag_samples`) a non-finite float is the string `"inf"`, `"-inf"` or `"nan"`, never
`null` (which would merge `inf` and `nan` and contradict 1.3). A consumer that wants
numbers converts those three strings itself.

```json
{"ranges": [1.25, "inf", "inf", 0.9], "intensities": ["nan", 0.0]}
```

A summary never mixes strings into numbers. It reports counts instead, with the
statistics computed over the finite values only:

```json
{"ranges": {"count": 541, "finite_count": 498, "inf_count": 41, "nan_count": 2,
            "min": 0.12, "max": 29.8, "mean": 4.7}}
```

Where `sample_messages` converts non-finite floats today, it already does this. Bag
decoding is brought in line in 0.7.0.

### 2.2 `stamp_source` is frozen to four values

`stamp_source` is exactly one of:

| Value | Meaning |
| --- | --- |
| `header` | `timestamp_ns` is the message's top-level `header.stamp` |
| `payload` | no header, but the time is in the body of `Clock`, `TFMessage` or `Log` |
| `recorded` | no time in the message; `timestamp_ns` is the bag record time |
| `none` | no time anywhere; `timestamp_ns` is 0 |

It is never `null`. The `null` that the 0.6.4 schema allows is removed in 0.7.0.

## 3. Reserved optional fields

These shapes are fixed now so that they can be added later without a break. Until a
tool fills them, they do not appear in its output (1.3, mechanism 1). Each is optional
and additive.

### 3.1 `suggested_fixes`

Not a tool: an optional list on a diagnostic finding (first on a QoS mismatch report).
TopicForge diagnoses, proposes a fix as text, a human applies it, TopicForge verifies.

```json
{"suggested_fixes": [
  {"kind": "qos_override",
   "target": "reader /nav2/controller on /scan",
   "text": "qos_overrides./scan.subscription.reliability: best_effort",
   "tradeoff": "The reader stops requesting retransmission, so it can miss samples.",
   "verify_with": "detect_qos_mismatches"}
]}
```

| Field | Content |
| --- | --- |
| `kind` | one of `cli`, `qos_override`, `dds_profile`, `launch_param` (closed set) |
| `target` | which node or which side to change, writer or reader |
| `text` | the exact command, YAML or XML to apply |
| `tradeoff` | what is lost by applying it |
| `verify_with` | the TopicForge tool to run again to confirm the repair |

Rules:

- The text is never executed by TopicForge. The tool description says: "suggestions are
  text for a human; TopicForge never applies them". `readOnlyHint` stays true.
- No destructive verb: nothing that kills a process, shuts down a lifecycle node,
  deletes or unsets a parameter, removes a file. A test fails any suggestion whose
  `text` contains a forbidden verb; the list lives in that test.
- When two sides can fix the problem (the writer or the reader), both are offered as
  separate entries, each with its own `tradeoff`.

### 3.2 Participant to node link

On `ParticipantInfo`: `node_names` (list of ROS 2 node names that share the participant,
read from `ros_discovery_info`) and `node_names_source`.

```json
{"guid": "01f1a2b3c4d5e6f7a8b9c0d1", "name": "/",
 "node_names": ["/lidar_driver", "/lidar_filter"],
 "node_names_source": "ros_discovery_info"}
```

`node_names_source` is `ros_discovery_info` (read from that topic), `participant_name`
(the name announced by DDS carried a node name) or `none` (no link could be made, and
`node_names` is then empty rather than absent). It exists because Fast DDS participants
all announce the name `/`.

### 3.3 QoS per side

On `TopicInfo` (the detail view): `publisher_qos` and `subscription_qos`, replacing the
publisher-only `qos_reliability` and `qos_durability`. Each describes one side and may be
`null` with its `_note` (1.3).

```json
{"publisher_qos": {"reliability": "reliable", "durability": "volatile", "endpoint_count": 1},
 "subscription_qos": {"reliability": "best_effort", "durability": "volatile", "endpoint_count": 2},
 "subscription_qos_note": null}
```

`reliability` and `durability` use the ROS casing (1.5) and the value `mixed` when the
endpoints of that side disagree. A topic with subscribers only now reports its
subscription side instead of `null`.

### 3.4 RMW implementation

On `health_check`: `rmw_implementation` and `rmw_source`.

```json
{"rmw_implementation": "rmw_fastrtps_cpp", "rmw_source": "env"}
```

`rmw_source` is `env` (the `RMW_IMPLEMENTATION` variable is set), `default` (not set; the
distro default is reported and was not verified on the running graph) or `none` (no ROS 2
available). When `rmw_source` is `none`, `rmw_implementation` is `null`.

### 3.5 Bag topic kind

On the per-topic entries of `analyze_bag`: `kind`, one of `user`, `rosbag2_internal`
(topics rosbag2 itself writes, such as `/events/write_split`) or `ros_builtin` (ROS 2
infrastructure topics such as `/parameter_events` and `/rosout`). `/tf`, `/tf_static` and
`/clock` are data and count as `user`.

```json
{"name": "/events/write_split", "kind": "rosbag2_internal", "message_count": 1}
```

## 4. Rate verdicts

A topic rate is reported as a `rate` block with a `verdict`. The block is computed from
message timestamps (`stamp_source` says which clock), so it is right on simulated time.

```json
{"rate": {"rate_hz": 9.97, "interval_median_s": 0.1, "interval_cv": 0.04,
          "max_gap_s": 0.13, "samples_count": 50, "window_s": 5.0,
          "stamp_source": "header", "verdict": "stable"}}
```

`interval_cv` is the coefficient of variation of the intervals between consecutive
messages (standard deviation divided by mean). The verdict is one of a closed set,
evaluated in this order, first match wins:

| Order | Verdict | Condition |
| --- | --- | --- |
| 1 | `silent` | no message in the window |
| 2 | `insufficient` | fewer than 5 samples |
| 3 | `intermittent` | at least one gap longer than 3 times the median interval |
| 4 | `erratic` | `interval_cv >= 0.5` |
| 5 | `stable` | `interval_cv < 0.2` (and, by order, no such gap) |
| 6 | `variable` | otherwise (`0.2 <= interval_cv < 0.5`, no such gap) |

`variable` is proposed here because the thresholds above leave `0.2 <= CV < 0.5` without
a verdict; it is pending owner validation. A verdict is a description of what was
observed in the window, not a diagnosis: a `silent` topic may be latched.

## 5. Reserved multimodal exception

Section 1.1 says a tool returns one object. A future `get_image` (0.8) is the single
planned exception: it returns that object (topic, size, encoding, `stamp_source`,
`note`) and, next to it, one MCP image content block carrying the picture. The object is
still the contract; the image block is an attachment that a text-only client may ignore.
The image is size-bounded (under 300 KB) and only a compressed image type is accepted. No
other tool returns binary content.

## 6. Change policy and tests

| What changes | How |
| --- | --- |
| A description, an input schema, an output schema or an annotation | the snapshot test fails; regenerate and review the diff |
| A new optional field, a new tool | allowed between breaks; update snapshots and TOOLS.md in the same change |
| A removal, a rename, a new required field | only at a `contract_version` bump, after one release marked deprecated |
| A new suffix, a new closed-set value | contract change; written here first |

`health_check.contract_version` (integer, 2 for 0.7) is the only place a client reads the
version of this contract. It is not repeated in other results.

## 7. 0.7.0 migration list

This is the complete list of renames, splits and removals that make 0.7.0 a break. It was
produced by reading `tools/handlers.py`, `models/schemas.py` and the adapters of 0.6.4.
After rc1, no rename is added. Anything in "Found by audit, not decided" waits for the
owner.

### 7.1 Tools that return a bare list today

Exactly three of the twelve tools (the others already return an object):

| Tool | Today | From 0.7.0 |
| --- | --- | --- |
| `list_topics` | `list[TopicInfo]` | `TopicListing {topics: list[TopicListItem], returned, total, truncated, mode_effective, note}` |
| `list_participants` | `list[ParticipantInfo]` | `ParticipantListing {participants: list[ParticipantInfo], returned, total, truncated, mode_effective, note}` |
| `participant_events` | `list[ParticipantEvent]` | `ParticipantEventListing {events: list[ParticipantEvent], returned, total, truncated, mode_effective, note}`; the silent 200-event cap becomes `truncated` and `total` |

### 7.2 `TopicInfo` splits in two

`TopicInfo` serves both `list_topics` and `get_topic_info`, and in `list_topics` several of
its fields are always `null`. It becomes:

- `TopicListItem` (in `list_topics`): `name`, `message_type`, `publisher_count`,
  `subscriber_count`. No QoS field.
- `TopicInfo` (in `get_topic_info`): the same four fields plus `publisher_qos`,
  `subscription_qos` and their `_note` fields (3.3), and `mode_effective`.

`qos_reliability` and `qos_durability` leave `TopicInfo` (replaced by 3.3). `mode_effective`
moves to the envelope of the listing.

### 7.3 Dead fields removed from `TopicInfo`

`reader_count`, `writer_count` and `qos_profile` are removed. Checked by grep over `src/`:
no adapter (`ros2_live`, `ros2_mock`, `dds_cyclone`, `dds_fast`, `composite`) and no
service ever assigns them, so they have always been `null`. Not to be confused with
`TopicSummary.writer_count` and `TopicSummary.reader_count` in `list_endpoints`, which are
filled and stay.

### 7.4 `_reason` becomes `_note`

Every occurrence in the output schemas, found by grep of `src/` for `_reason`:

| Model | 0.6.4 field | 0.7.0 field |
| --- | --- | --- |
| `HealthReport` | `dds_inactive_reason` | `dds_inactive_note` |
| `HealthReport` | `payload_decoding_reason` | `payload_decoding_note` |

(The Python-side `dds_inactive_reason` attribute on the adapters, and the
`_dds_inactive_reason` factory helper, are internal and follow the rename as they are
touched.) No payload key is named `_reason`; the underscore-prefixed annotations in raw
payloads are `_decode_status`, `_decode_note`, `_truncated_fields`, `_raw_text`,
`_raw_bytes_hex` and `_raw_bytes_truncated`, and they stay.

### 7.5 `list_endpoints`

- `include_internal` is a new input, default `false`: the `rq/`, `rr/`, `rs/`, `rp/` and
  `ra/` ROS 2 service and action endpoints are hidden unless it
  is true. Today they make up most of the 155 endpoints of a small robot.
- `EndpointInfo.topic` is replaced by `dds_topic` (the raw DDS name, `rt/scan`) and
  `ros_topic` (the ROS name, `/scan`, `null` with a note when the endpoint is not a ROS
  topic). `TopicSummary.topic` follows the same split.
- A root-level `hints` list (strings, the same idea as `MismatchScan.hints`) replaces the
  sentence repeated on every endpoint: `EndpointInfo.activity` (always `null`) and
  `EndpointInfo.activity_note` (identical on all entries) are removed, and the sentence
  moves once to `hints`.
- The listing gains `returned`, `total` and `truncated` names consistent with 1.1: the
  existing `total_discovered` becomes `total`.

### 7.6 Envelope and `health_check`

- `contract_version` (integer) is added to `health_check`.
- A single `note` is added to the results that have none: `get_topic_info`,
  `detect_qos_mismatches`, `topic_metrics`, `health_check`. Existing fields are not
  overloaded: this is additive.
- `MessageSample.stamp_source` becomes non-nullable (2.2).

### 7.7 Found by audit, not decided (owner)

These are consequences of the principles that the brief did not list. Each is a rename or
a removal and so must be decided before rc1, or stay as it is.

| Item | Observation | Proposal |
| --- | --- | --- |
| `BagAnalysis.duration_seconds`, `TopicMetrics.window_seconds`, `TopicMetrics.window_seconds_actual` | seconds without the `_s` suffix | `duration_s`, `window_s`, `window_actual_s` |
| tool inputs `participant_events.lookback_seconds`, `topic_metrics.window_seconds` | same, on the input side | `lookback_s`, `window_s` |
| `TopicMetrics.frequency_hz_observed`, `frequency_hz_declared` | `_hz` in the middle of the name | keep (a suffix pair is clearer), or `observed_frequency_hz` / `declared_frequency_hz` |
| `EndpointListing.departed_endpoints`, `EndpointListing.excluded_observer_endpoints` | counts without `_count` | `departed_endpoint_count`, `excluded_observer_endpoint_count` |
| `MismatchScan.pairs_checked`, `topics_scanned`, `TopicMetrics.samples_observed`, `EndpointListing.returned` | counts without `_count`, but read as past participles | keep |
| `NotMatchedPair.reason` | an enum named `reason`, not a free sentence | keep (it is a closed set, not a `_reason` suffix) |
| `interval_cv` (section 4) | a ratio, no suffix in the vocabulary | allow `_cv` or call it `interval_variation` |
| `sample_messages.timeout_s` maximum | allows 45 s and returns within about `timeout_s` + 2 s, so the worst case is 47 s, above the 45 s of 1.7 | lower the maximum to 40 (the default stays 10) |
| `HealthReport.mode` / `requested_mode` | `health_check` has no `mode_effective` | keep, documented in 1.2 |
