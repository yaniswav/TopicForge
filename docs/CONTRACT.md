# TopicForge output contract

Status: contract 2, shipped in 0.7.0. The baseline it started from (0.6.4) is recorded in
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
3. Optional fields
4. Rate verdicts
5. Reserved multimodal exception
6. Change policy and tests
7. 0.7.0 migration list
8. Node tools
9. 0.7.x additions

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

Every result is exactly one `structuredContent` object plus one `TextContent` block that
carries the same JSON. `tests/test_contract_shape.py` (section 6) asserts
`len(result.content) == 1` for every tool in mock mode.

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

The envelope appears once, at the top level. Nested items do not repeat it: `mode_effective`
is removed from `ParticipantInfo`, `ParticipantEvent`, `EndpointInfo` and `MismatchReport`,
`domain_id` is removed from `EndpointInfo`, and a listing envelope carries `domain_id` once.

```json
{"participants": [{"guid": "01f1a2b3c4d5e6f7a8b9c0d1", "name": "/"}],
 "returned": 1, "total": 1, "truncated": false,
 "domain_id": 0, "mode_effective": "live", "note": null}
```

`ParticipantInfo` and `ParticipantEvent` keep their own `domain_id`: the 0.7.0 contract removed `domain_id`
from `EndpointInfo` only, and the lifecycle buffers filter on it.

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
of five strings (decision 2.2), never `null`.

### 1.4 Locked suffix vocabulary

A name that carries a unit or a kind ends with exactly one of these suffixes:

| Suffix | Meaning | Example |
| --- | --- | --- |
| `_ns` | integer nanoseconds (instant or duration) | `timestamp_ns`, `deadline_ns` |
| `_s` | seconds, float | `timeout_s`, `window_s` |
| `_hz` | hertz, float | `observed_frequency_hz` |
| `_cv` | coefficient of variation: dimensionless, standard deviation divided by mean, >= 0 | `interval_cv` |
| `_count` | number of items in a set | `publisher_count` |
| `_total` | number of items before a cap | `reports_total` |
| `_source` | where a value came from (closed set of strings) | `stamp_source` |
| `_basis` | what a value was computed from (closed set of strings) | `frequency_basis` |
| `_note` | one sentence of explanation for a neighbouring field | `observed_domain_note` |

A new field uses one of these or no suffix. A physical quantity (distance, angle, speed, acceleration) carries no suffix: REP 103 fixes its unit (meters, radians, meters per second, meters per second squared), the description states it, and the name mirrors the ROS field where one exists. The time suffixes exist because nanosecond integers and float seconds are both in use; a meter or a radian has no second representation to tell apart. `_reason` is not in the vocabulary and is
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
duration, which stays under 45 s for every tool. The 45 s are wall time, from the request
to the response, lock wait included. A regex test fails on history words from 0.7.0 rc1.

Requirement: `health_check` never takes the lock, and a handler that waited for the
lock shortens its own deadline by the time it waited.

The 45 s are wall time from the request to the response, lock wait included. The server
enforces it (`topicforge.tools.guard`, with `topicforge.budget` passing the deadline down):

- Calls run in two lanes with one lock each: ROS (`list_topics`, `get_topic_info`,
  `list_nodes`, `get_node_info`, `inspect_tf`, `sample_messages`, `analyze_bag`) and DDS (`list_participants`, `detect_qos_mismatches`, `peek_dds_samples`,
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

### 2.2 `stamp_source` is frozen to five values

`stamp_source` is exactly one of:

| Value | Meaning |
| --- | --- |
| `header` | `timestamp_ns` is the message's top-level `header.stamp` |
| `payload` | no header, but the time is in the body of `Clock`, `TFMessage` or `Log` |
| `recorded` | no time in the message; `timestamp_ns` is the bag record time |
| `dds_source` | `timestamp_ns` is the DDS source timestamp of the sample |
| `none` | no time anywhere; `timestamp_ns` is 0 |

It is never `null`. The `null` that the 0.6.4 schema allows is removed in 0.7.0. Which
values a tool can return:

| Tool | Possible values |
| --- | --- |
| `sample_messages` | `header`, `payload`, `none` |
| `peek_bag_samples` | `header`, `payload`, `recorded` |
| `peek_dds_samples` | `dds_source`, `none` |

```json
{"timestamp_ns": 1760000000123456789, "stamp_source": "dds_source"}
```

## 3. Optional fields

Some shapes were fixed before any tool filled them, so that they could be added without a
break. Each is optional and additive; a tool that does not fill one omits its content (1.3,
mechanism 1).

### 3.1 `suggested_fixes`

Populated by `detect_qos_mismatches` since 0.7.1, on `reports[]` (RxO mismatches) and on
`not_matched[]` (`partition` splits only). Not a tool: an optional list on a diagnostic
finding. TopicForge diagnoses, proposes a fix as text, a human applies it, TopicForge
verifies. Other tools may fill it later with the same shape.

```json
{"suggested_fixes": [
  {"kind": "qos_override",
   "target": "reader /nav2/controller on /scan",
   "text": "/nav2/controller:
  ros__parameters:
    qos_overrides./scan.subscription.reliability: best_effort",
   "tradeoff": "The reader stops requesting retransmission, so it can miss samples. Only works if the node enabled QoS overrides (rclcpp/rclpy QosOverridingOptions); pass the file when the node starts: --ros-args --params-file <file>.",
   "verify_with": "detect_qos_mismatches(topic='rt/scan')"}
]}
```

| Field | Content |
| --- | --- |
| `kind` | one of `cli`, `qos_override`, `dds_profile`, `launch_param` (closed set). 0.7.1 emits `qos_override` and `dds_profile` |
| `target` | which node or which side to change, writer or reader |
| `text` | the exact command, YAML or XML to apply |
| `tradeoff` | what is lost by applying it |
| `verify_with` | the TopicForge tool and arguments to run again to confirm the repair |

Behaviour:

- A finding gets one fix per side that can change, most often two (the safer side first,
  explained in `tradeoff`). A finding with no supported fix (DestinationOrder,
  DataRepresentation, History) has an empty list and a `hints` entry.
- `qos_override` is used for a ROS 2 topic (`rt/` prefix) when only Reliability and
  Durability differ; everything else is a `dds_profile`: a Fast DDS XML profile followed by
  the cyclonedds-python equivalent.
- Findings with the same topic and compared values share one set of fixes, carried by the
  first report; the others have an empty list and a `hints` entry says so. A fix lists at most
  three nodes.

Rules:

- The text is never executed by TopicForge. The tool description says: "suggestions are
  text for a human; TopicForge never applies them". `readOnlyHint` stays true.
- No destructive verb: nothing that kills a process, shuts down a lifecycle node,
  deletes or unsets a parameter, removes a file. A test fails any suggestion whose
  `text`, `target` or `tradeoff` contains a forbidden word (kill, pkill, delete, rm,
  shutdown, deactivate, cleanup, set, publish, call, launch, restart, reset); the list
  lives in that test.
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

`node_names_source` is `ros_discovery_info` (read from that topic), `ros2_cli` (derived
from the `ros2` CLI) or `none` (no link could be made, and `node_names` is then empty
rather than absent). There is no `participant_name` value: since Foxy the participant
carries the name of the enclave, not of a node. The field exists because Fast DDS
participants all announce the name `/`.

Filled since 0.7.2 on the Cyclone backend (`node_names_source` `ros_discovery_info`) and
by the mock. A fourth reader on `ros_discovery_info` (type
`rmw_dds_common::msg::dds_::ParticipantEntitiesInfo_`, RELIABLE, TRANSIENT_LOCAL, KEEP_ALL)
feeds a cache keyed by the 12-byte GUID prefix; `adapters/common/ros_discovery.py` decodes
the XCDR1 payload by hand and accepts the gid width (24 or 16) that consumes the buffer
exactly. The decoder was checked on captures from real rmw_fastrtps nodes (Humble: 24,
Jazzy: 16, both in WSL), and the Docker ROS 2 bench names every node on Humble and Jazzy
with both rmw_fastrtps and rmw_cyclonedds; Connext writers are untested. A
participant that has left keeps the last non-empty list it announced. Fast DDS and RTI
backends leave it `none`.

Risk: `ros_discovery_info` is a user topic whose `Gid.data` is 24 bytes on Humble
and 16 bytes from Iron. Fallback: `ros2_cli`, through the GID prefixes printed by
`ros2 topic info -v /parameter_events` and `/rosout`.

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
endpoints of that side disagree. A side is `null` when it has no endpoint ("The topic has no
publisher...") or when one of the two policies is reported by none of its endpoints (the CLI
printed `UNKNOWN` or `SYSTEM_DEFAULT`), never half filled. A topic with subscribers only now reports its
subscription side instead of `null`.

`TopicInfo` also gains `publisher_nodes` and `subscriber_nodes`: lists of fully qualified
node names, read from the `Node name:` and `Node namespace:` lines of
`ros2 topic info -v`. An empty list, never `null`, when none is known.

```json
{"publisher_nodes": ["/lidar_driver"], "subscriber_nodes": ["/nav2/controller", "/slam"]}
```

### 3.4 RMW implementation

On `health_check`: `rmw_implementation` and `rmw_source`.

```json
{"rmw_implementation": "rmw_fastrtps_cpp", "rmw_source": "env"}
```

`rmw_source` is `env` (the `RMW_IMPLEMENTATION` variable is set), `ros2_cli` (reserved:
read from the `ros2` CLI), `distro_default` (not set; the distro default is reported and
was not verified on the running graph) or `none` (no ROS 2 available). The server implements
`env`, `distro_default` and `none`; `ros2_cli` is reserved. When `rmw_source` is `none`,
`rmw_implementation` is `null`.

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
the time the messages were received (`received_ns`), so it measures the delivery the
caller actually gets; `stamp_source` says which clock the message stamps use.

```json
{"rate": {"basis": "received_ns", "message_count": 50, "startup_burst_count": 0, "window_s": 5.0,
          "mean_interval_s": 0.1003, "interval_median_s": 0.1, "max_gap_s": 0.13,
          "interval_cv": 0.04, "observed_frequency_hz": 9.97, "sim_frequency_hz": 9.95,
          "trailing_gap_s": null, "verdict": "stable",
          "verdict_note": "Steady delivery at 10.0 Hz (interval variation 0.04)."}}
```

The block is `SampleResult.rate` (`sample_messages` and `peek_bag_samples`). Names as built in
`message_count` (suffix `_count`) replaces `samples_count`, `observed_frequency_hz`
replaces `rate_hz`, `sim_frequency_hz` is the same computation on the messages' own stamps
(`null` without stamps), and `basis` is `received_ns` for a live call or `recorded_ns` for a
bag, where there is no end of window and so `trailing_gap_s` is always `null`. Messages
dropped for size still count. `interval_cv` uses the population standard deviation.

On `received_ns` the measuring subscriber is a fresh `ros2 topic echo`, whose reader keeps 5 messages (2000 on a `rcl_interfaces/msg/Log` topic, section 9.12) (the `sensor_data` depth; the adapter overrides reliability and durability only) between the match and its first callback, then prints them in one go. On a fast topic that reads as a run of near-zero intervals at the start and inflates the frequency: the Docker bench read `/clock` (50 Hz) at 91.3 Hz over 12 messages, 11 intervals in 0.1205 s instead of 0.220 s, exactly five periods short. The block sets aside the leading run of intervals under a quarter of the median interval, never more than half of them, reports it as `startup_burst_count`, and computes the intervals, `observed_frequency_hz`, `interval_cv` and the verdict on the rest; `message_count` still counts them and their stamps still feed `sim_frequency_hz`. `recorded_ns` is never trimmed: what the recorder saw is the data. Kept messages spanning less than about a second stay coarse on both bases (one late message moves the frequency by several percent and the cv over 11 intervals by 0.2); the tool description asks for `count` 50 on a topic above 20 Hz. To check a configured publish rate, `sim_frequency_hz` is the number when the messages are stamped; `observed_frequency_hz` is what reached the subscriber.

`interval_cv` is the coefficient of variation of the intervals between consecutive
messages (standard deviation divided by mean). `trailing_gap_s` is the interval between
the last message and the end of the window; it is `null` when collection stopped on
`count` rather than on the deadline. The verdict is one of six values, evaluated in this
order, first match wins:

| Order | Verdict | Condition |
| --- | --- | --- |
| 1 | `silent` | no message in the window |
| 2 | `insufficient` | fewer than 5 samples |
| 3 | `intermittent` | at least one interval longer than 3 times the median interval; the trailing interval counts when collection stopped on the deadline and not on `count`; measured on `received_ns` |
| 4 | `stable` | `interval_cv < 0.2` |
| 5 | `jittery` | `0.2 <= interval_cv < 0.5` |
| 6 | `erratic` | `interval_cv >= 0.5` |

The tool description recommends `count >= 10` and warns that topics with several
publishers, such as `/tf`, come out `erratic` without being broken.

A verdict is a description of what was observed in the window, not a diagnosis: a
`silent` topic may be latched.

Thresholds confirmed 2026-10-09 under delegation and kept at 0.2 and 0.5, the same on both bases. Measured on the OmniSim fixture bag (`recorded_ns`, sliding windows of 10 to 50 messages): `/odom` cv 0.03-0.11 and `/imu/data` 0.03-0.17, `/scan` 0.16-0.40 and `/gps/local` 0.19-0.41, whose intervals alternate between about 0.16 and 0.25 s at 5 Hz. The same recorder and path give 0.05 on `/odom`, so the spread is the emitter's, not the transport's or the basis's; `jittery` describes it correctly, and the note gives the longest gap as a multiple of the median. 0.3 was rejected: with `count` 10 or 20, 7 to 12 percent of the windows on those two topics stay above 0.3, so the verdict would flap between calls, and a periodic loss of one message in 8 (cv 0.29) or 10 (0.27) would read `stable`; periodic loss never exceeds cv 0.35. The threshold moves to 0.3 only if the Docker bench, a fixed-rate publisher read through `ros2 topic echo`, lands at or above 0.2 on `received_ns` in two consecutive runs on a `count` 50 call (kept messages spanning at least 0.9 s), start-up burst set aside, while the cv of the same messages' stamps stays below 0.2: that would mean the measuring path, not the publisher, is that noisy. The 2026-10-09 readings on `/clock` over 12 messages do not qualify: cv 0.31 was one tick 20 ms late on a loaded runner (the stamps agree at 45.8 Hz), and 91.3 Hz was the drain described above.

### Message summaries

`MessageSample.summary` (null for any other type, for DDS peeks, and for a scan whose arrays
were replaced by `arrays_summary_only`) is one of five objects told apart by `summary_type`:
`laser_scan`, `odometry`, `imu`, `image`, `point_cloud2`. It is always computed on the whole
message, even when `payload` is cut at `max_array_length`. There is no `summarize` input: a
summary is small and always on.

Resolutions recorded here:

- The unit suffixes `_m`, `_rad`, `_mps` are not in the locked vocabulary (1.4), so summary
  fields carry no unit suffix. Units follow REP 103 (meters, radians, meters per second,
  radians per second) and each description says so; names mirror the ROS fields
  (`angle_min`, `range_max`) or are plain (`range`, `bearing`, `linear_speed`, `yaw`).
  Decided 2026-10-09 under delegation: no unit suffix on physical quantities, now a rule of 1.4. `_rad_s` would end in `_s` and read as seconds, `_mps` and `_mps2` are spellings nobody else uses, and `angle_min_rad` would stop mirroring the ROS field.
- Counts use `_count` (`beam_count`, `point_count`), not `n_*`.
- `inf_count` is `+inf` only; `-inf` is `neg_inf_count`; `nan_count` is `nan`. A finite range
  outside `range_min`..`range_max` is counted in `out_of_range_count` and is not a valid return.
- Sectors are defined on the bearing in the sensor frame: front within +-45 degrees, left
  above 45 up to 135, right from -135 up to -45, rear beyond +-135; an edge beam belongs to the
  narrower sector, with a tolerance of 0.006 degrees for float32 angles. A sector with no beam
  has `closest` null and a `note`.

## 5. Reserved multimodal exception

Section 1.1 says a tool returns one object. A future contract version may add a
multimodal exception: a tool that returns that object and, next to it, one MCP image
content block carrying the picture. The object is
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

Tests (all three exist):

| File | Asserts |
| --- | --- |
| `tests/test_contract_shape.py` | `len(result.content) == 1` for every tool in mock mode (1.1) |
| `tests/test_field_names.py` | no output or input field name ends with `_seconds`, `_sec`, `_secs`, `_ms`, `_us`, `_nanos`, `_nanoseconds`, `_hertz`, `_reason`, `_num` or `_nb` (1.4) |
| `tests/test_server_info_version.py` | `serverInfo.version` of the initialize handshake equals `health_check.server_version` |

## 7. 0.7.0 migration list

This is the complete list of renames, splits and removals that make 0.7.0 a break. It was
produced by reading `tools/handlers.py`, `models/schemas.py` and the adapters of 0.6.4.
After rc1, no rename is added. Section 7.7 records the decision taken for each item the
audit found, and section 7.8 collects what the contract added.

### 7.1 Tools that return a bare list today

Exactly three of the twelve tools of 0.6.4 (the others already return an object):

| Tool | Today | From 0.7.0 |
| --- | --- | --- |
| `list_topics` | `list[TopicInfo]` | `TopicListing {topics: list[TopicListItem], returned, total, truncated, mode_effective, note}` |
| `list_participants` | `list[ParticipantInfo]` | `ParticipantListing {participants: list[ParticipantInfo], returned, total, truncated, mode_effective, note}` |
| `participant_events` | `list[ParticipantEvent]` | `ParticipantEventListing {events: list[ParticipantEvent], returned, total, truncated, mode_effective, note}`; the silent 200-event cap becomes `truncated` and `total` |

The participant and event listings also carry `domain_id` once, in the envelope (1.2).

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

Three fields of `BagAnalysis` are removed for the same reason, because they were never
filled: `samples_decoded_count`, `participants_recorded` and `recording_duration_ns`.

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
- `mode_effective` and `domain_id` are removed from `EndpointInfo` (1.2); the listing
  envelope carries `domain_id` once.
- A root-level `hints` list (strings, the same idea as `MismatchScan.hints`) replaces the
  sentence repeated on every endpoint: `EndpointInfo.activity` (always `null`) and
  `EndpointInfo.activity_note` (identical on all entries) are removed, and the sentence
  moves once to `hints`.
- The listing gains `returned`, `total` and `truncated` names consistent with 1.1: the
  existing `total_discovered` becomes `total`. It keeps its meaning (every endpoint in the
  discovery cache before any filter), so `returned < total` is explained by the filters and
  the observer exclusion as well as by the cap; `truncated` is only the cap. Two counts say
  what a default call left out: `excluded_observer_endpoint_count` and the new
  `hidden_internal_endpoint_count` (the `include_internal` filter).
- `ros_topic` is `null` with a one-sentence `ros_topic_note` on the endpoint when the DDS
  name is not a ROS 2 topic (no `rt/` prefix: `rq/`, `rr/`, `rs/`, `rp/`, `ra/`,
  `ros_discovery_info` and DDS-native names). `TopicSummary` carries `dds_topic` and
  `ros_topic` only.

### 7.6 Envelope and `health_check`

- `contract_version` (integer) is added to `health_check`.
- A single `note` is added to the results that have none: `get_topic_info`,
  `detect_qos_mismatches`, `topic_metrics`, `health_check`. Existing fields are not
  overloaded: this is additive.
- `MessageSample.stamp_source` becomes non-nullable, with five values (2.2). It is set
  explicitly in `adapters/dds_cyclone/adapter.py` (lines 165 and 442),
  `adapters/dds_fast/adapter.py` (line 314), `adapters/common/dds_helpers.py`
  (line 135) and the DDS fixtures of the mock adapter.

### 7.7 Decided by audit

These are consequences of the principles that the brief did not list. Each line carries
the decision taken for contract 2 (2026-10-09).

| Item | Observation | Decision |
| --- | --- | --- |
| `BagAnalysis.duration_seconds`, `TopicMetrics.window_seconds`, `TopicMetrics.window_seconds_actual` | seconds without the `_s` suffix | renamed: `duration_s`, `window_s`, `window_actual_s` |
| tool inputs `participant_events.lookback_seconds`, `topic_metrics.window_seconds` | same, on the input side | renamed: `lookback_s`, `window_s` |
| `TopicMetrics.frequency_hz_observed`, `frequency_hz_declared` | `_hz` in the middle of the name | renamed: `observed_frequency_hz`, `declared_frequency_hz` |
| `EndpointListing.departed_endpoints`, `EndpointListing.excluded_observer_endpoints` | counts without `_count` | renamed: `departed_endpoint_count`, `excluded_observer_endpoint_count` |
| `MismatchScan.pairs_checked`, `topics_scanned`, `TopicMetrics.samples_observed`, `EndpointListing.returned` | counts without `_count`, but read as past participles | kept |
| `NotMatchedPair.reason` | an enum named `reason`, not a free sentence | kept (a closed set, not a `_reason` suffix) |
| `interval_cv` (section 4) | a ratio, no suffix in the vocabulary | `_cv` added to the locked vocabulary (1.4) |
| `sample_messages.timeout_s` maximum | allows 45 s and returns within about `timeout_s` + 2 s, so the worst case is 47 s, above the 45 s of 1.7 | maximum lowered from 45 to 40 (the default stays 10) |
| `HealthReport.mode` / `requested_mode` | `health_check` has no `mode_effective` | kept, documented in 1.2 |

### 7.8 Added by the contract

Everything below goes into the migration list and into `docs/MIGRATION.md`.

- Renames of 7.7, in output models and in the two tool inputs.
- `BagAnalysis` loses `samples_decoded_count`, `participants_recorded` and
  `recording_duration_ns` (7.3).
- Nested items no longer repeat envelope fields (1.2, 7.5): `mode_effective` leaves
  `ParticipantInfo`, `ParticipantEvent`, `EndpointInfo` and `MismatchReport`;
  `domain_id` leaves `EndpointInfo`.
- `stamp_source` has five values and is set explicitly in the DDS paths and in the mock
  fixtures (7.6).
- `sample_messages.timeout_s` maximum is 40.
- `rmw_source` values are `env`, `ros2_cli`, `distro_default`, `none`; `node_names_source`
  values are `ros_discovery_info`, `ros2_cli`, `none` (3.2, 3.4).
- Verdict `variable` is replaced by `jittery`; the `rate` block gains `trailing_gap_s`
  (section 4).
- `TopicInfo` gains `publisher_nodes` and `subscriber_nodes` (3.3).
- `list_nodes` (tool 13) is limited to one `ros2 node list` call: names, namespaces and
  duplicates. Per-node interfaces stay in `get_node_info` (tool 14). The only
  request TopicForge ever sends is a parameter read (list/get). Both tools ship in 0.7.0,
  in the ROS lane, and are additive: see section 8.
- External callers to update with the renames: `examples/dds/harness.py`,
  `scripts/integration/interop_check.py` (and the kit's `compare.py`).

## 8. Node tools

Tools 13 and 14, shipped in 0.7.0. Both are additive, run in the ROS lane and are open-world.

`list_nodes` returns `NodeListing {nodes, returned, total, truncated, duplicates,
lifecycle_count, lifecycle_note, by_namespace, mode_effective, note}` from `ros2 node list`
and, since 0.7.3, one `ros2 service list -t` (section 9.10). A node is `{name, namespace,
full_name, duplicate_count, lifecycle}`; `nodes` holds one entry per distinct full name and `duplicates`
the full names with `duplicate_count` above 1. Nodes whose name starts with an underscore
are hidden by the CLI and are not listed.

`get_node_info(node, timeout_s)` returns `NodeInfo {full_name, publishers, subscribers,
service_servers, service_clients, action_servers, action_clients, parameters, parameters_note,
use_sim_time, use_sim_time_note, lifecycle, duplicate_count, mode_effective, note}`. An interface is
`{name, type}`. It makes up to three CLI calls: `ros2 node list` (the node must exist,
duplicates are counted), `ros2 node info` (a graph query that never waits for the node) and
`ros2 param dump` bounded by `timeout_s` (1..20, default 8) and by what is left of the 45 s.

Rules:

- `parameters` is `null` only with a `parameters_note` (1.3): the node did not answer in time
  (a diagnosis, its executor is probably blocked, not an error), it offers no parameter
  service, or the dump was unreadable or over 1 MiB. The interfaces are returned anyway.
- `use_sim_time` is a boolean read from the parameters, else `null` with `use_sim_time_note`.
- A parameter is `{name, value, masked, truncated, original_size}`; names are dotted and
  sorted. A name containing password, secret, token, api_key or credential (any case) has its
  value replaced by `<masked>`. A string over 2048 characters or a list over 128 elements is
  cut and flagged `truncated`; `parameters_note` says how many were masked or cut.
- Non-finite floats are the strings `inf`, `-inf` and `nan` (2.1).
- The command is `ros2 param dump <node>` with no output option: it prints YAML on stdout and
  nothing is written to disk. The only request TopicForge ever sends is a parameter read
  (list/get).
- A name used by several nodes is flagged: `duplicate_count` above 1 and a `note`, because the
  CLI answers for one of them without saying which.
- An unknown node is an `isError` result naming close matches (1.6).

## 9. 0.7.x additions

Everything here is additive (1.9): a new optional field or input with a backward-compatible
default, or a description that says the same thing in fewer words. No property was renamed,
removed or retyped; `scripts/contract/snapshot_tools.py` regenerates the snapshots and the diff
shows only added properties.

### 9.1 Listing size

`tools/list` carries the description, input schema and output schema of every tool, and some
clients truncate it. In 0.7.0 it was about 178 KB compact (14 tools); 0.7.2 served about 119 KB
(118,884 bytes). 0.7.3 serves about 95 KB (94,957 bytes, section 9.9) against a 100,000 test
ceiling, which leaves about 5 KB before `graph_diff`.
How:

- Pydantic's generated `title` is dropped from every property and nested model, and the
  docstring of a nested model is dropped (the model name stays). The output type name stays as
  the `title` of each `outputSchema`. Done by `tools/schema_compact.py`.
- Field descriptions are one sentence, and self-explanatory ones (`reader_guid`, `returned`)
  have none. The longer explanations are in this section and in section 4; the rules a client
  needs stay in the tool description.
- `tests/test_friction_fixes.py` fails if the listing grows back over 100 KB.

Rule for new fields: one sentence, no history, no restating the name.

### 9.1b `is_topicforge_cli` and `restarts_by_node` (0.7.2)

Both additive. `ParticipantInfo.is_topicforge_cli` (default `false`) marks the `ros2` CLI
process that a TopicForge call (`list_nodes`, `get_node_info`, `sample_messages`, ...)
spawned: it joins the domain as a participant and stays 0.3-2 s after exiting, so a bus
of 7 looked like 8. A participant is flagged, never removed, when one of these matches a
pid TopicForge started in the last 30 s: its `/_ros2cli_<pid>` node name; the pid in a
Fast DDS GUID prefix (bytes 4-5, low 16 bits); a Cyclone `__Pid` property. The listing
`note` says so, and counts participants that merely appeared within 5 s of a CLI call.
Only the composite (ROS CLI plus DDS) adapter flags; the pid check is unavailable where the
launcher is not the process that joins DDS (Windows `ros2.exe`), the note still applies.
`ParticipantListing.restarts_by_node` is `restarts_by_name` keyed by node full name, over
the participants that announced node names, the CLI ghosts left out.

### 9.2 `restarts_by_name`

A restarted ROS 2 node is a new DDS participant with a new GUID and the same name, so
`seen_count` (observations of one GUID) stays at 1 and an agent had to count GUIDs by hand.
`list_participants` and `participant_events` now carry:

- `restarts_by_name: {name: {lives, departures}}`. `lives` is the number of distinct GUIDs
  that carried the name (restarts = `lives` - 1); `departures` is the number of times a
  participant with that name was lost.
- Only names with `lives` > 1 or `departures` > 0 are listed, so a healthy bus adds nothing. A
  name that is absent ran once and stayed.
- `list_participants` counts the participants it lists (`status` `left` is a departure), bounded
  by the lifecycle buffer. `participant_events` counts the events in the `lookback_s` window
  only: a participant discovered before the window and still active does not appear, and one
  that left inside it counts one life and one departure.
- Participants and events without a `name` are excluded and `restarts_note` says how many.
  Dust DDS announces no name, and every Fast DDS participant of a ROS 2 setup is named `/`, so
  those cannot be separated by name.

### 9.3 `domain_id` is ignored, and says so

`domain_id` on `list_participants`, `participant_events`, `topic_metrics` and `list_endpoints`
never selected a domain: the adapter reports the one it joined at startup
(`TOPICFORGE_DDS_DOMAIN_ID`, `health_check.dds_domain_id`). The parameter stays (removing it
is a break). Its description now starts with "Ignored" and the response `domain_id` is the
domain actually observed.

When a non-zero `domain_id` differs from the joined domain, the response `note` says
`domain_id N was ignored: this server observes domain M ...`. Chosen over raising an
`AdapterError` because a note is additive and cannot break a client that always passed the
wrong value, while an error would; the envelope already carries the real domain. The default
`0` cannot be told from "not given", so an explicit `0` is never flagged, and an omitted
argument never produces a note when the joined domain is not 0. The mock adapter does not
report a joined domain and gives no note.

### 9.4 `list_endpoints` `fields`

New optional input `fields`: `"full"` (default, unchanged output) or `"compact"`. Compact
keeps every endpoint and the `by_topic` roll-up, sets `qos` to `null` and fills the new
`qos_summary` with a one-line reading (`RELIABLE VOLATILE KEEP_LAST(10) deadline=100ms
partitions=a`, defaults left out), and drops `type_id`, `announced_ns` and `ros_topic_note`.
The response echoes the level in `fields`. Use `full` on one topic or participant to read a
complete QoS. In compact mode `qos_summary` is `qos unresolved` when the QoS could not be
resolved.

### 9.5 `MismatchScan.summary`

One line stating the counts: `0 incompatible, 0 risky QoS pair(s); 3 pairs will never match
(3 by partition): no data flows between them, see `not_matched`; 5 pairs will match given the
announced QoS.` An empty `reports` next to a non-empty `not_matched` can no longer be read as
an all-clear from the summary alone. The counts are over the whole scan, before the 200-entry
cap. Built in `adapters/common/qos_scan.py`, so every backend and the mock carry it.

### 9.6 `ParticipantEvent.age_s`

Seconds between `timestamp_ns` and the moment the response was built (never negative, three
decimals), next to the `*_ns` fields that are hard to read. `null` in mock mode, whose timeline
is a fixed 2024 anchor. Only the events got it: the other `*_ns` stamps of the listing
(`first_seen_ns`, `last_seen_ns`) would add a field to every participant for little reading
value; compare against `health_check.now_ns` when needed.

### 9.7 Explanations moved out of the schema

- `TopicRate.startup_burst_count`: on a live call the fresh `ros2 topic echo` prints the
  messages its reader queued before the first callback (up to 5) in one go. A leading run of
  intervals under a quarter of the median interval is that drain; those messages are set aside
  before the intervals, `observed_frequency_hz` and the verdict, are still counted in
  `message_count`, and still feed `sim_frequency_hz`. Always 0 on `recorded_ns`.
- `TopicRate.observed_frequency_hz` is coarse when the messages span under about a second (one
  late message moves it by several percent): compare `sim_frequency_hz` when present and ask
  for `count` 50 on a topic above 20 Hz. `sim_frequency_hz` is `null` when the messages carry no
  stamp (`stamp_source` `none` or `recorded`) or the stamps do not advance.
- `TopicRate.verdict`: a `silent` topic may be latched; a topic with several publishers such as
  `/tf` reads `erratic` without being broken; a simulated or bridged sensor whose intervals
  alternate reads `jittery` while healthy: compare `max_gap_s` with `interval_median_s` before
  suspecting a lost message.
- `MessageSample.timestamp_ns` on simulated time is sim time since the simulation started (a
  few seconds, not a date) and 0 is a valid value. `peek_bag_samples` gives a message without a
  header the bag record time (`stamp_source` `recorded`), also in `recorded_ns`. Mock samples
  with a header carry synthetic increasing values. `stamp_source`: `sample_messages` gives
  `header`, `payload` or `none`; `peek_bag_samples` `header`, `payload` or `recorded`;
  `peek_dds_samples` `dds_source` or `none`.
- `MessageSample.payload`: `_truncated_fields` lists the dotted paths of arrays, strings or bytes
  cut at `max_array_length` (a string of exactly that length plus `...` that was not cut is
  listed too); `_raw_text` holds the unparsed output only when a message could not be parsed;
  with `arrays_summary_only` an array is a string such as `<sequence type: float, length: 541>`.
  A message over the size cap may be dropped with a `note`.
- `MessageSample.summary` is computed on the whole message even when `payload` is cut, and is
  `null` for a scan whose arrays were replaced by `arrays_summary_only`.
- `ParticipantInfo.lost_ns` and `ParticipantEvent.time_source` for `lost`: an upper bound of when
  the participant died. After a clean shutdown it is the exact leave time; after a crash it is
  when the lease expired, and the two cannot be told apart. The process died at or before it,
  and at most one lease earlier (10 s Cyclone default, 20 s Fast DDS, 100 s RTI).
- `ParticipantInfo.vendor` is read from the vendor prefix of the participant GUID. Dust DDS and
  RTI Connext do not put their vendor id there, and the Cyclone Python binding does not expose
  the RTPS header vendor id, so those are `unknown` (the participant is still reported). See
  `docs/dds-interop-matrix.md`.
- `QosProfile.partitions`: no Partition policy is the default partition, reported as `[""]`,
  never `[]`; `null` only when the backend cannot read partitions at all. `history` is reported
  only for TopicForge's own endpoints and for Cyclone peers that set it (see `history_note`);
  a reader `deadline_ns` tighter than the writer's is incompatible.
- `HealthReport.ros2_distro` discloses `ROS_DISTRO` by design under the local-trust threat model
  (README "Security model"); a hosted multi-tenant endpoint would scrub it.

### 9.8 `inspect_tf` (tool 15)

Tool 15, additive, ROS lane, open-world, `ros2` CLI only (a DDS-only setup raises the usual
error). It returns `TfReport`. Inputs: `window_s` (1..20, default 5), `stale_after_s` (default
2), `source_frame` and `target_frame` (both or neither).

- The tree comes from the transforms: `/tf_static` is read once (transient_local, one latched
  message per publisher; it stops as soon as they are in, and waits at most the longer of
  `window_s` and 6 s), concurrently with `/tf`, which is streamed uncut for `window_s`, at most
  3000 messages. A topic that is absent or has no publisher is a `note`; when both streams fail for any
  other reason the call raises an error instead of returning an empty tree.
- Time is the header stamp. `age_s` is the distance between a frame's newest stamp and the
  newest `/tf` stamp (`reference_ns`), so it is correct on sim time. `stale` is `age_s` above
  `stale_after_s`; static frames have no age. `observed_frequency_hz` uses the arrival time,
  `sim_frequency_hz` the stamps, `real_time_factor` is their ratio (about 1 on a real robot).
- `findings.kind` is an open set (1.9): `stale`, `two_parents`, `disconnected` (more than one
  root), `static_and_dynamic` (a frame on both topics), `cycle`, `no_path` (unknown or
  unconnected frames). A sound tree has none.
- Ages are measured against the newest `/tf` stamp, so one publisher with a future stamp or a
  zero stamp skews the staleness of every other frame: a frame can read as stale because
  another one runs ahead. Check `stamp_span_s` and the stamps when many frames go stale at once.
- `echo` can drop messages at high rates: the structure and the findings hold, the exact
  message counts and frequencies are approximate (`truncated` says when messages were lost).
- The mock `/tf_static` topic carries the fixed frames of the demo robot; mock `inspect_tf`
  is a fixed 5 s capture and ignores `window_s`.

### 9.9 `summary` is advertised as an open object (0.7.3)

Additive, a loosening of the advertised schema only. `MessageSample.summary` used to list the
five summary models (`LaserScanSummary`, `OdometrySummary`, `ImuSummary`, `ImageSummary`,
`PointCloud2Summary`, with `ScanReturn` and the other nested ones) in the `$defs` of every
tool that returns a sample: about 26 KB of `tools/list`. It is now advertised as
`object | null`, where the object requires `summary_type` (`laser_scan`, `odometry`, `imu`,
`image`, `point_cloud2`) and allows any other property. `default` keywords were also dropped
from every `outputSchema` (never from an `inputSchema`): a result always carries the field.

- The shapes did not change. They are the ones of section 4 and the "Message summaries"
  appendix of [TOOLS.md](TOOLS.md), generated from the models. Read `summary_type`, then the
  fields of that shape.
- The server still builds and validates the five models strictly (`extra="forbid"`); only
  what it advertises is looser. A client that validates `structuredContent` against the
  `outputSchema` can only see fewer failures, never new ones, so no client breaks. A client
  that generated typed code from the five models now gets an untyped object for `summary`.
- `tests/test_summary_schema_compat.py` pins this: no summary model in any `$defs`, a valid
  `LaserScanSummary` passes the served schema, a wrong `summary_type` fails it, and mock
  `sample_messages` and `peek_bag_samples` results validate against it.

### 9.10 `list_nodes` lifecycle and `by_namespace` (0.7.3)

Additive: new fields with defaults, no input change. `list_nodes` now costs two CLI calls
(`ros2 node list`, then `ros2 service list -t`).

- `NodeListItem.lifecycle` (bool, default `false`) is `true` when the graph has the service
  `<full_name>/get_state` of type `lifecycle_msgs/srv/GetState`, the rule of
  `ros2 lifecycle nodes`. `NodeInfo.lifecycle` applies the same rule to the node's own
  `service_servers`. The lifecycle state itself is never read: that would be a service call,
  and the tool surface stays graph-only.
- `NodeListing.lifecycle_count` (int, default 0) counts the flagged nodes.
- `NodeListing.lifecycle_note` (str or `null`) is set when the service list failed or came
  back empty although nodes exist. Then every `lifecycle` is `false` only because it is
  unknown, never silently (1.3); the listing itself still succeeds.
- `NodeListing.by_namespace` (object, default `{}`) maps each namespace (`/` is the root) to
  the number of distinct full names in it, sorted by namespace.
- Mock: `/camera_driver` is the lifecycle node (five lifecycle services and the topic
  `/camera_driver/transition_event`, which adds one topic to the mock graph).
- Bench: `/robot1/bench_lifecycle` (rclpy `LifecycleNode`, left unconfigured) and its
  `transition_event` topic; the comparator checks `lifecycle` and `by_namespace` on both
  distros.

### 9.11 `TopicRate` clock fields (0.7.3)

Additive; every field has a default, so older payloads still validate. They say what kind of
clock the messages' own stamps are on, which `sim_frequency_hz` alone cannot.

- `clock_basis`: `wall` (stamps are dates, at least 1e18 ns), `sim` (a smaller non-zero stamp,
  time since a simulation or robot start), `none` (no stamp: timestamp 0, or `stamp_source`
  `none` / `recorded`) or `mixed` (more than one of those kinds among the messages, for example
  two publishers on one topic, or one stamped and one unstamped message). Default `none`.
- `stamp_age_median_s`, `stamp_age_p95_s`: `(basis - stamp) / 1e9` per message, where the basis
  is `received_ns` (live) or `recorded_ns` (bag). Given only when `clock_basis` is `wall`,
  else `null`. Signed: negative means the publisher clock is ahead of the observer's. The p95
  is nearest-rank, and the maximum under 20 messages. An age of a few tens of milliseconds is
  normal transport delay; seconds point to a skewed or wrong publisher clock.
- `stamp_regression_count`: messages, in basis order, whose non-zero stamp is strictly lower
  than the previous non-zero stamp. A topic with several publishers interleaves and reads high;
  a simulation restart counts once. Default 0.
- Computed by the pure `services/summaries/clock.py` from `(basis_ns, stamp_ns, stamp_source)`
  of the messages that were decoded; messages dropped for size or decode errors are not seen.

### 9.12 `/rosout` filter and bag ranges (0.7.3)

Additive; every new input is optional and every new field has a default.

**`sample_messages`: `min_log_level`, `node`.** Both apply to `rcl_interfaces/msg/Log` topics
(`/rosout`) only; on any other topic the call raises ("filter `rcl_interfaces/msg/Log`
topics"). An unknown level or an empty node is an error too.

- Levels are the ROS 2 constants, `debug` 10, `info` 20, `warn` 30, `error` 40, `fatal` 50, and
  the test is `level >= threshold`.
- `node` takes `/ns/name`, `name` or `ns.name`. It is normalized (leading `/` removed, `/` to
  `.`) and compared with the log message's `name` field, which is the node's full name with the
  namespace separated by dots (`/robot1/driver` logs as `robot1.driver`). A log matches when
  `name == n` or `name` starts with `n + "."` (a child logger such as `driver.camera`). It
  never matches a free prefix (`drive` does not match `driver`) and it is case-sensitive.
  `node` matches the log `name` field, not the node's real identity: a node that logs under a
  custom logger name will not match its own `node` value.
- Live reader. With a filter, a `Log` topic is read with `--qos-history keep_last
  --qos-depth 2000`, because the `/rosout` writer is `RELIABLE`, `TRANSIENT_LOCAL`, `KEEP_LAST`
  1000 with a 10 s lifespan, and the CLI default depth of 5 would drop most of that buffer.
  Without a filter nothing changes from 0.7.2: the CLI default reader, the first `count`
  messages, `rate` as before.
- With a filter the call streams until `timeout_s` (at most 2000 messages), filters AFTER
  decoding, and returns the NEWEST `count` matches, oldest first (stamp order, arrival order
  for equal stamps). `note` says how many matched, for example `6 of 12 log messages read
  matched min_log_level=error, node=/bench_robot; the newest 3 are returned in stamp order.`,
  explains an empty result, and, when the 2000-message cap was hit, says the result is the
  newest of the first 2000 read (newer logs may be unread: use a narrower `node` or a higher
  `min_log_level`). `rate` is `null`
  (a replayed buffer has no meaningful rate).
- Mock: `/rosout` is a mock topic (10 fixture lines from six nodes) with the same rules.

**`peek_bag_samples`: `start_s`, `end_s`, `min_log_level`, `node`.**

- `start_s` and `end_s` are bag-relative seconds, zero at the bag's first recorded message
  (`BagAnalysis.start_ns`, the rosbag2 `starting_time`), applied to `recorded_ns` as the
  half-open interval `[start, end)` through the `rosbags` reader. Both default to the whole bag.
  `end_s` must exceed `start_s`; both must be finite and not negative. A range past the end of
  the bag returns no sample and a `note` ("start_s ... is past the end of the bag").
- The same log filter, applied after decoding, with the same rules for level and node.
  A bag read returns the FIRST `count` matches in range, in bag order. This is the opposite of
  the live rule (newest): the live stream has no beginning to start from, the bag does. Both
  tool descriptions say so. A filtered or ranged bag read can decode far more messages than it
  returns, so it stops at 20 s or when the call budget runs out, checked every 64 messages, and
  the `note` says `scan stopped after X s / N messages, K matches so far: narrow start_s/end_s
  or the filter`.
- `BagAnalysis.start_ns: int | None` (default `null`): record time of the first message, read
  from `metadata.yaml` (`starting_time`) or the `rosbags` reader; `null` only when unreadable.
  A bag that starts at 0 (sim time) reports `0`.

**Adapter protocol.** `MiddlewareAdapter.sample_messages` gains keyword arguments
`min_log_level` and `node`; `peek_bag_samples` gains `start_s`, `end_s`, `min_log_level` and
`node` (all default `None`). CHANGELOG, "Changed".

**Code.** The pure rules are in `adapters/common/log_filter.py` (levels, node match, notes) and
`adapters/common/bag_range.py` (range validation, `[start, end)` in ns), shared by the live
adapter, the bag reader and the mock.

**Bench.** `bench_robot` logs WARN every second and ERROR every two; `/robot1/bench_lifecycle`
logs INFO every five seconds; the bag records `/rosout`. Rows (comparator section `logs`):
`error` + `/bench_robot` returns at least 3 messages, all level 40 and name `bench_robot`;
`warn` alone returns levels 30 and above only; `/bench_blocked` returns none and a note;
`/robot1/bench_lifecycle` matches `robot1.bench_lifecycle`; the bag `/rosout` at `error` has
at least 2; the bag `/clock` with `start_s` 2, `end_s` 4 and `count` 50 returns 50 samples
within the bounds computed from `metadata.yaml`; `analyze_bag.start_ns` equals the recorded
`starting_time`. The live rows use `timeout_s` 6, short enough that live lines alone give one or
two ERRORs, so at least 3 means the depth-2000 reader received the transient_local buffer on
that RMW. Each row prints its exact message count and a histogram of levels.

### 9.13 `tools/list` size: two compaction levers (0.8.0)

Additive and wire-compatible: no field is added, removed or retyped, and no payload changes.
`tools/list` went from 94,957 bytes (0.7.3, fifteen tools) to 94,393 bytes with `graph_diff`
(sixteen tools), under the 100,000-byte test ceiling, which does not move.

- **Lane notes.** The `**Duration**` sentence every locked tool carries is one sentence
  now: ``**Duration**: `ros2` CLI behind the ROS lock, one call at a time; ends within 45 s;
  `busy` error if the lock is held (retry).`` (DDS tools name the DDS binding and lock). The
  facts are the ones of section 1.7, and `tests/test_tool_descriptions.py` still requires
  `45 s`, `` `busy` ``, the lock name and, for `sample_messages`, `` `timeout_s` `` and
  `at most 40`.
- **Nullable fold, output schemas only.** `anyOf: [X, {"type": "null"}]` is served as
  `type: [X, "null"]` when `X` is a plain typed schema (a string, number, array or object with
  its own keywords). A `$ref` or a nested combinator is left as it was, and an `enum` of `X`
  gains `null` so the folded schema still accepts one. Input schemas are never folded: clients
  already read their `anyOf` form. The two forms accept exactly the same values, so a client
  that validates `structuredContent` against the `outputSchema` sees no difference; one that
  generates types from the schema reads `string | null` for what used to be an `anyOf`.
  `MessageSample.summary` (9.9) becomes `type: ["object", "null"]` with its `required` and
  `properties` beside it, which is the same set of accepted values.
- An output schema description that only says "Envelope returned by `<tool>`." is dropped, and
  a few `HealthReport` field descriptions are shorter (same facts).
- `tests/test_output_schema_validation.py` calls every tool in mock mode and validates the
  structured result against the schema `list_tools` serves, with `jsonschema`. It is the net
  under any further compaction.
- Reserve, if a later tool needs bytes: serve a subset of tools (`ros`, `dds`, `all`). It is not
  used in 0.8.0.

### 9.14 `graph_diff` (tool 16)

The sixteenth tool served (the slot numbered 16 in the plan stays reserved for `get_image`, which
needs rclpy). Additive, ROS lane, open-world, read-only. It answers "what changed since it last
worked". Inputs: `baseline` (string, default `previous`) and `bag_path` (string or `null`). It
returns `GraphDiff`.

**Calls.** Each call takes one snapshot of the current side, stores it, and compares it with the
baseline. The current side is the live graph, or the bag at `bag_path`. `baseline` is one string:

- `previous`: the last snapshot taken before this call. With none yet the call stores a snapshot
  and returns `baseline` and `baseline_id` `null`, no `changes`, and a `note` ("No baseline yet:
  snapshot snap-1 stored ...").
- `snap-N`: a snapshot id from `snapshot_ids`. An id that is not held raises an error that lists
  the held ids ("unknown snapshot id 'snap-9'; held snapshots: snap-1, snap-2"), and nothing is
  stored.
- anything else: a bag path (same validation as `analyze_bag`), snapshotted for this call and
  not kept in the ring. Its `id` in `baseline_id` is the path.

The baseline is resolved before the new snapshot is taken, so a bad value stores nothing.

**The ring.** Sixteen snapshots in memory, in the server process. Ids are `snap-N`, N counting up
from 1 for the life of the process and never reused; the oldest snapshot is dropped past sixteen.
A restart empties it. Nothing is written to disk.

**What a snapshot holds.**

| Side | Holds | Source |
| --- | --- | --- |
| live | nodes (full names), topics with type, publisher and subscriber counts, writer QoS | `list_nodes` and `list_topics` (the `ros2` CLI), `list_endpoints` read from the discovery cache |
| bag | topics with type, rate (`frequency_hz`), latching | `analyze_bag`; `rosbag2_internal` topics are left out |

Writer QoS is the set of `reliability/durability` pairs of the topic's writers (lowercase,
sorted, comma-separated), never the depth. It is present only when a DDS backend is active, read
without a binding call and without the DDS lock; otherwise `not_compared.qos` says so. A bag
holds latching only (`transient_local` or `volatile`).

**Comparisons.** Only what both sides can support is compared; the rest is named in
`not_compared` (key, then the reason), never silently skipped.

| `kind` | Compared when | `change`, `field` |
| --- | --- | --- |
| `node` | live against live | `added`, `removed` |
| `topic` | always | `added`, `removed` (`after` or `before` holds the message type) |
| `type` | always | `changed`, `message_type` |
| `qos` | both sides hold the same kind of QoS: live writer QoS (`writer_qos`) or bag latching (`latched`) | `changed` |
| `rate` | bag against bag | `changed`, `frequency_hz`; past 20 percent relative (exactly 20 percent is not a change) |
| `count` | live against live | `changed`, `publisher_count` or `subscriber_count` (strings in `before` and `after`) |

`added` means present in the current snapshot and absent from the baseline. When one side is a
bag, `note` says what that means: a bag holds only the recorded topics, so with a bag baseline
`added` is present live and absent from the bag. `changes` is sorted by kind (the order of the
table), then name. `added_count`, `removed_count` and `changed_count` count `changes`.

**Noise.** Endpoint counts on `/rosout` and `/parameter_events` move with every node and every
CLI call: a change in one of their counters is tallied in `builtin_changes_count` and never
appears in `changes`. A `subscriber_count` change in a snapshot taken less than 5 s after a
TopicForge `ros2` CLI call (from the runner's ring of spawned pids) carries "A TopicForge `ros2`
CLI reader may linger 2 s" in its `text`. A healthy graph diffed against itself gives zero
changes (bench row `31_graph_diff_steady`).

**Mock.** The ring starts with `snap-0`, built on the first call from the mock fixtures minus
three things: the node `/imu_driver`, the topic `/imu/data`, and the `/scan` writer QoS (it had
`reliable/volatile`, the fixture says `best_effort/volatile`). The first mock call therefore
reports exactly three changes (a node, a topic and a QoS), the second none. `MOCK_BAG_ANALYSIS`
(any well-formed bag path in mock mode) is accepted as a baseline.

**Fields.** `GraphSnapshotRef` has `id`, `source` (`live` or `bag`), `taken_ns`, `path` (`null`
for live), `node_count` (`null` for a bag), `topic_count`, `has_qos`, `has_rates`. `GraphChange`
has `kind`, `change`, `name`, `field` (`null` when added or removed), `before`, `after` (`null`
when not applicable) and `text`. `GraphDiff` has `snapshot_id`, `baseline_id`, `current`,
`baseline`, `changes`, the three counts, `not_compared`, `builtin_changes_count`, `snapshot_ids`
(oldest first), `mode_effective` and `note`.

**Read-only.** It sends nothing `list_nodes` and `list_topics` do not already send; the
discovery cache is read, not the binding; a bag is read through the existing path validation.

**Bench.** `calls_graph_diff.json` (run after `calls.json`): `30_graph_diff_first` (no baseline),
settle 3 s, `31_graph_diff_steady` (zero changes: the healthy line), `graph_change.py` started by
`drive.py` (`{"spawn": ...}`, stopped when the session ends), settle 5 s, `32_graph_diff_v2`
(exactly three changes: node `/bench_v2` added, topic `/scan_v2` added, `/cmd_vel_out`
`publisher_count` 1 to 2; any other change is a false alarm) and `33_graph_diff_bag` (`baseline`
is the recorded bag: `removed_count` 0, no `type` or `qos` change, `not_compared` names `nodes`
and `rates`). Comparator section `graph_diff`. Written for the four Docker cells; not run in the
session that added it.

### 9.16 DDS visibility: SROS2 environment, `endpoint_count`, `node_name` (0.8.0)

Additive: new optional fields, no input change. About 1,100 bytes of `tools/list`. Nothing here
says a bus is secured or unsecured: it reports environment variables and what discovery shows.

**`health_check`.** Four fields, all `null` unless the `ros2` CLI serves (`ros_backend` is
`ros2_cli`; the same rule as `rmw_implementation`, so the mock and a DDS-only setup say nothing
about an environment they do not run in), read from the server's own environment by the pure
`resolve_security` in `services/health.py`:

- `ros_security_enabled` (bool): `ROS_SECURITY_ENABLE` is exactly `true`, as rcl reads it.
- `ros_security_strategy` (`Enforce`, `Permissive` or `null`): `ROS_SECURITY_STRATEGY` when it
  holds one of the two documented values, `null` when unset or anything else.
- `ros_security_keystore_set` (bool): `ROS_SECURITY_KEYSTORE`, or the older
  `ROS_SECURITY_ROOT_DIRECTORY`, is non-empty. The path itself is never reported.
- `ros_security_note` (string or `null`): set only when `ros_security_enabled` is true:
  "TopicForge joins the domain without an identity: participants of secured nodes stay visible
  through SPDP, their endpoints and data do not". A server started without these variables
  while the robot has them reports `false`: the variables describe this process, not the graph.

`dds_security` stays `not_supported`.

**`ParticipantInfo.endpoint_count`** (int or `null`): writers plus readers the discovery cache
holds for the participant, ROS 2 plumbing included (a ROS 2 node always brings a dozen: its
parameter services, `/rosout`, `ros_discovery_info`). `null` when the backend keeps no endpoint
cache (mock excepted: it counts its fixtures; Fast DDS has never run). When an active participant
that is not the observer and not a TopicForge `ros2` CLI process has had `endpoint_count` 0 for
more than 10 s since first seen, `ParticipantListing.note` adds "N participant(s) announce no
endpoint TopicForge can see: an endpoint-less participant, or DDS Security hides them (TopicForge
has no identity on this domain)." Two causes, no verdict: the data cannot tell them apart.

**`EndpointInfo.node_name`** (string or `null`): the ROS 2 node that owns the endpoint, resolved
from the `ros_discovery_info` cache. Each message lists, per node, the gids of its readers and
writers; a gid is 24 bytes on Humble and 16 on Iron and later, and its bytes 0 to 15 are the RTPS
GUID of the endpoint (12-byte participant prefix, 4-byte entity id), which is the `guid` of the
endpoint. `null` for an endpoint no node claims (a plain DDS application, a participant whose
message was not seen, the TopicForge observer). It is the only link between an endpoint and a
node when every participant is called `/` (Fast DDS). The map outlives a participant that
announced an empty node list on shutdown, so departed endpoints keep their node.
`TopicSummary.writer_nodes` and `reader_nodes` list the distinct `node_name` of the listed writers
and readers, sorted (empty when none is known).

**Mock.** Participants count their fixture endpoints (`lidar_driver` 2, `nav_planner` 3,
`camera_driver` 1, the Dust participant 1) and the endpoints of the three that host a node carry
its name; the Dust endpoint has none.

**Bench.** `visibility` section of the bench truth (`05s_list_endpoints_scan` is a
`list_endpoints(topic="/scan")` call in `calls.json`): the `/scan` writer's `node_name` and
`writer_nodes` are `/bench_robot`, the participant that hosts `/bench_robot` has an
`endpoint_count` of at least 12, `ros_security_enabled` is `false` and `ros_security_note` is
`null`. Written for the four Docker cells; not run in the session that added it. Not covered: a
bus with SROS2 enabled (a SROS2 cell is planned for 0.8.1) and RTI.

### 9.17 The `diagnose-nav2` prompt and hidden topics (0.8.0)

A third MCP prompt, `diagnose-nav2` (optional `symptom` and `namespace`, both only focus the
text), with a plugin skill of the same name under `plugin/skills/` in the same wording. It adds
nothing to `tools/list` (prompts are listed apart) and no tool. The server `instructions` name
it next to the other two. It is a call order, not a diagnosis engine: the model reads the tool
outputs and says what they show.

**Order.** `health_check` (a `mock` server is the demo robot, which has no Nav2: say so),
`list_nodes` (the Nav2 servers are managed nodes, `lifecycle` true; the state is never read),
`get_node_info` of the `lifecycle_manager_*` (`parameters`: `node_names`, `autostart`,
`bond_timeout`), `sample_messages("/bond", count 50)` (a `bond/msg/Status` per bond end, `id`
the node name: a name of `node_names` that never appears is not bonded), `get_node_info` of
`bt_navigator` then `sample_messages("<action>/_action/status", count 1)` (latched
`action_msgs/msg/GoalStatusArray`, `status_list[].status` 1 accepted to 6 aborted),
`inspect_tf(map, base_link)`, `/rosout` filtered with `min_log_level` and `node`, and the
controller's velocity output while a goal executes. Count 50 is the tool's cap: with several
bonds at several messages per second each, 20 messages could miss a node.

**Hidden topics are readable.** `list_topics` does not list the `_action/status` and
`_action/feedback` topics of an action (the `ros2` CLI hides them), but `sample_messages` reads
any topic by name, and a latched one gives its last message. The prompt says so, because an
agent that trusts `list_topics` alone concludes there is no such topic. A server that has not
handled a goal since it started may hold no status message: the prompt tells the model to say
that, not to call it a failure.

**Read-only.** The text names no write verb (`tests/test_prompts.py` fails if a prompt contains
`service call`, `lifecycle set`, `action send_goal` or `topic pub`), and it ends with what
TopicForge cannot see: lifecycle state, costmaps, the behavior tree and its recoveries, whether
the robot moves.

**Bench.** The prompt is verified on its primitives, not on Nav2. `calls_nav_primitives.json`
(between `calls.json` and `calls_graph_diff.json`): `40_action_status` reads
`/bench_fib/_action/status` (an action server nothing calls; the bench publishes one empty
latched status by hand, since an idle server publishes none) and must return one sample with
`status_list` `[]`; `/bench_fib` must be in `action_servers` of `/bench_robot`; the status topic
must be absent from `list_topics`; `41_bond` reads `/bond` (`bond/msg/Status`, id `bench_robot`,
1 Hz) and every sample must carry the id. Comparator section `nav_primitives`. Written for the
four Docker cells; not run in the session that added it, and Nav2 itself was never run.
