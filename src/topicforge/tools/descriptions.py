"""Tool and parameter description strings for the MCP tool handlers.

Kept apart from `handlers.py` so the registration code stays readable. The text is
what LLM clients read on `tools/list`: edit it with `tests/contract` snapshots in mind.
"""

from __future__ import annotations

TOPIC_PARAM_DESC = (
    "Fully qualified ROS 2 topic name starting with `/`, e.g. `/cmd_vel`. Segments are "
    "letters, digits and underscores (not starting with a digit); anything else is "
    "rejected before reaching the `ros2` CLI."
)

NODE_PARAM_DESC = (
    "Fully qualified node name as `list_nodes` gives it, e.g. `/robot1/lidar_driver` "
    "(a missing leading `/` is added)."
)

NODE_TIMEOUT_PARAM_DESC = (
    "Seconds to wait for the parameter read, 1..20, default 8. A node that does not "
    "answer is reported in `parameters_note`, not as an error."
)

PARAMS_FILE_PARAM_DESC = "Server path of a ROS 2 parameter `.yaml` (1 MiB max)."

URDF_PATH_PARAM_DESC = (
    "Server path of a `.urdf` (no xacro, 1 MiB max) used instead of /robot_description."
)

PARAM_DIFF_ONLY_PARAM_DESC = (
    "With `params_file`: return only `param_diff` and its note; interfaces and parameters "
    "are left out."
)

TF_FRAME_PARAM_DESC = (
    "Frame name, e.g. `base_link`. Give `source_frame` and `target_frame` together to get "
    "the chain between them in `path`."
)

DDS_TOPIC_PARAM_DESC = (
    "DDS topic name: bare (`scan`), ROS 2 mangled (`rt/scan`) or a builtin discovery "
    "topic (`DCPSParticipant`, `DCPSSubscription`, `DCPSPublication`). Letters, digits, "
    "`_`, `/` and `::` only."
)

DOMAIN_PARAM_DESC = (
    "Ignored: it does not switch domains and is only range-checked (0..232). The domain "
    "observed is the one joined at startup (`TOPICFORGE_DDS_DOMAIN_ID`), reported in the "
    "response `domain_id`; `note` flags a different non-zero value."
)

COUNT_PARAM_DESC = (
    "Messages to return, default 5, at most 50 (`health_check.max_sample_count`): more is "
    "capped to 50, negative raises an error. The result's `count` may be lower."
)

SAMPLE_COUNT_PARAM_DESC = (
    "Messages to wait for, default 5, at most 50 (more is capped and `note` says so); 0 "
    "returns nothing. Fewer arrive when the topic is slow or `timeout_s` runs out."
)

SAMPLE_TIMEOUT_PARAM_DESC = (
    "Seconds to wait, 1..40, default 10, covering topic lookup and CLI start-up. The call "
    "returns within `timeout_s` plus a few seconds, with what arrived and a `note`. Raise "
    "it for a topic slower than 1 Hz."
)

MAX_ARRAY_LENGTH_PARAM_DESC = (
    "Longest array, string or bytes value returned in full, 1..65536, default 128 (a "
    "541-beam `LaserScan` is cut to 128 ranges; its `summary` still covers all). Longer "
    "values are cut and listed in `_truncated_fields`. Null returns everything (large for "
    "images and clouds; a message over 1 MiB is dropped with a note)."
)

LOG_LEVEL_PARAM_DESC = (
    "`/rosout` (`rcl_interfaces/msg/Log`) only: keep messages at or above this level "
    "(debug 10, info 20, warn 30, error 40, fatal 50). Other topics: error."
)

LOG_NODE_PARAM_DESC = (
    "`/rosout` only: keep logs of this node and its child loggers, e.g. `/robot1/driver` "
    "(also `driver`, `robot1.driver`). Exact name, never a prefix. Other topics: error."
)

START_S_PARAM_DESC = (
    "Only messages recorded at or after this many seconds from the bag's first message "
    "(`analyze_bag.start_ns`), default from the start."
)

END_S_PARAM_DESC = (
    "Only messages recorded before this bag-relative second (exclusive), default to the end."
)

ARRAYS_SUMMARY_PARAM_DESC = (
    "When true, arrays become a type-and-length summary: use it to read the non-array "
    "fields of large messages. Default false."
)

PATH_PARAM_DESC = (
    "Bag path: a `.mcap`, `.db3` or ROS 1 `.bag` file or a `rosbag2_*` directory (with its "
    "`metadata.yaml`). Existence and format are checked live; mock accepts any well-formed path."
)

BASELINE_PARAM_DESC = (
    "`previous` (last snapshot), a `snap-N` id from `snapshot_ids`, or a bag path."
)

BAG_SNAPSHOT_PARAM_DESC = "Snapshot this bag instead of the live graph."

SUMMARY_RATE_NOTE = (
    " **Summary and rate**: `LaserScan`, `Odometry`, `Imu`, `Image`, `PointCloud2` and "
    "`GoalStatusArray` (goals per status) samples carry a `summary` of the whole message "
    "(other types: null); `rate` gives frequency, "
    "interval spread and a `verdict` measured on %s. Use `count` >= 10 (50 above 20 Hz); a "
    "multi-publisher topic such as `/tf` reads `erratic` without being broken."
)

ROS_LANE_NOTE = " **Duration**: ROS lock, one call at a time, ends within 45 s; `busy` if held."

DDS_LANE_NOTE = " **Duration**: DDS lock, one call at a time, ends within 45 s; `busy` if held."

NO_LOCK_NOTE = (
    " **Duration**: never waits for a lock and never runs the `ros2` CLI; it answers at once."
)

BAG_NOTE = (
    " **Duration**: takes no lock (in-process file read, so it runs while a ROS 2 or DDS "
    "call is busy); the time follows the bag's size and how deep the first samples are."
)

HEALTH_CHECK_DESC = (
    "Report TopicForge environment state as a `HealthReport`: `mode`, `ros_backend`, "
    "`dds_backend`, `ros_tools_available`, `ros2_distro`, `rmw_implementation`, "
    "`dds_domain_id` and `observed_domain_note` (only the domain joined at startup is "
    "observed), server version and sample cap; for a live DDS backend also "
    "`observer_started_ns` / `now_ns` (nothing earlier was watched) and the discovery "
    "tracker status `tracker_*` (errors, evictions or a stale pass mean gaps). `live` "
    "with `ros_backend` `none` is a DDS-only setup: use `list_endpoints`. With "
    "`dds_backend` `none`, `dds_inactive_note` says why. `payload_decoding` is "
    "`disabled` (DDS user-topic payloads are not decoded); `dds_security` is "
    "`not_supported`. **Always succeeds**: call it first when something looks wrong." + NO_LOCK_NOTE
)

LIST_TOPICS_DESC = (
    "ROS 2 graph only; on a DDS-only setup use `list_endpoints`. List the topics on "
    "the ROS 2 graph (or the mock graph) as a `TopicListing`: each has `name`, "
    "`message_type`, `publisher_count` and `subscriber_count`; `mode_effective` tells a "
    "real graph from fixtures. QoS is not listed: use `get_topic_info`. `counts_unknown: true` = counts 0/0 are unknown, not an orphan. **Empty "
    "`topics`** when the graph has none or live discovery times out. **Raises an MCP "
    "error** without a `ros2` CLI." + ROS_LANE_NOTE
)

GET_TOPIC_INFO_DESC = (
    "ROS 2 graph only; on a DDS-only setup use `list_endpoints`. Describe one ROS 2 "
    "`topic` (fully qualified, e.g. `/cmd_vel`) as a `TopicInfo`: counts and, in live "
    "mode, `publisher_qos` / `subscription_qos` (`reliability`, `durability` where "
    "`transient_local` marks a latched topic such as `/tf_static`, `endpoint_count`; a "
    "`null` side says why in its `_note`) and `publisher_nodes` / `subscriber_nodes`. "
    "**Raises an MCP error** for a malformed or unknown topic, or without a `ros2` CLI."
    + ROS_LANE_NOTE
)

LIST_NODES_DESC = (
    "ROS 2 graph only; on a DDS-only setup use `list_participants`. List the nodes on "
    "the ROS 2 graph (or the mock graph) from one `ros2 node list` call as a "
    "`NodeListing`: `name`, `namespace`, `full_name`, `duplicate_count`, `lifecycle` "
    "(managed node; state not read, `lifecycle_note` if unknown), `by_namespace` counts. "
    "`duplicates` lists full names used by several nodes (a per-node tool then answers for "
    "one of "
    "them, unidentified). Nodes starting with an underscore are hidden and a node on "
    "another `ROS_DOMAIN_ID` is invisible. **Empty `nodes`** when the graph has none. "
    "Use `get_node_info` for one node's interfaces. **Raises an MCP error** without a "
    "`ros2` CLI. Cheap (two CLI calls)." + ROS_LANE_NOTE
)

GET_NODE_INFO_DESC = (
    "ROS 2 graph only; on a DDS-only setup use `list_participants`. Describe one ROS 2 "
    "node as a `NodeInfo`: the topics it publishes and subscribes to, the services and "
    "actions it serves or calls (each `{name, type}`), its parameters and "
    "`use_sim_time`. Interfaces come from the graph (`ros2 node info`), which never "
    "waits for the node. Parameters come from a read (`ros2 param dump`) bounded by "
    "`timeout_s`: **a node that does not answer in time is a finding, not an error**: "
    "`parameters` is `null`, `parameters_note` says its executor is probably blocked, "
    "and the interfaces are still returned. A value whose name contains password, "
    "secret, token, api_key or credential is replaced by `<masked>` and a long value "
    "is cut (`truncated`, `original_size`), both said in `parameters_note`. With "
    "`params_file`, `param_diff` lists declared values that differ on the node, are "
    "`declared_only` or `masked`. With several nodes of that name `duplicate_count` is "
    "above 1 and `note` warns. "
    "**Raises an MCP error** when the node is not on the graph (the message lists close "
    "matches), the name is malformed, or without a `ros2` CLI. **Read-only**: the only "
    "request TopicForge ever sends is a parameter read (list/get); it sends no other "
    "request to a node. **Duration**: `ros2` CLI behind the ROS lock, one call at a "
    "time; up to three CLI calls (node list and info, 8 s each at most; the parameter "
    "read, at most `timeout_s`, 20 s); ends within 45 s, less time for the read after "
    "a wait; `busy` error if the lock is held (retry)."
)

INSPECT_TF_DESC = (
    "ROS 2 graph only. Rebuild the TF tree from the transforms on `/tf_static` (latched) "
    "and `/tf` (listened to for `window_s`) and flag what breaks lookups. Returns a "
    "`TfReport`: `frames` (parent, `kind` static/dynamic/both, `age_s`, `stale`, "
    "`observed_frequency_hz` on wall time, `sim_frequency_hz` on stamps), `roots`, counts "
    "and `findings` `{kind, frames, text}`: `stale` (a frame trails the newest `/tf` "
    "stamp by more than `stale_after_s`), `two_parents`, `disconnected` (several roots), "
    "`static_and_dynamic`, `cycle`, `no_path`. Ages and rates use header stamps, so they "
    "hold on sim time (`real_time_factor` far from 1 means sim). A slow or intermittent "
    "publisher needs a longer `window_s` (`diagnose` uses 8 s). Give `source_frame` and "
    "`target_frame` for the `path` between them. It also reads `/robot_description` and "
    "flags URDF "
    "links with no frame (`declared_not_published`) and joints on the wrong stream "
    "(`fixed_joint_on_tf`, `movable_joint_on_tf_static`). **Empty `frames`** when nothing is "
    "published (read `note`). The listening is capped (3000 messages): `echo` can drop "
    "messages at high rates, so the tree is exact but frequencies are approximate "
    "(`truncated`). **Raises an MCP error** without a `ros2` CLI. **Mock mode**: a fixed "
    "5 s capture of the demo robot with one stale frame." + ROS_LANE_NOTE
)

SAMPLE_MESSAGES_DESC = (
    "ROS 2 graph only; on a DDS-only setup use `list_endpoints` or `peek_dds_samples` "
    "on `DCPSPublication` / `DCPSSubscription`. Collect up to `count` live messages "
    "from a ROS 2 `topic`, waiting at most `timeout_s` seconds, as a `SampleResult`. "
    "**Live mode** streams `ros2 topic echo` with the publishers' QoS (latched topics "
    "work) and returns what arrived by the deadline: `count` is the actual number and "
    "`note` says `N of M messages` and why it is short (no publisher, silent, slow or "
    "too large to print in time; a latched topic usually holds one message, so use "
    "`count` 1). Each sample has `payload` (nested named values), `timestamp_ns` (the "
    "publisher's clock, sim time on a simulation, 0 for a headerless message such as "
    "`std_msgs/String`; see `stamp_source`) and `received_ns` (wall clock when the CLI "
    "printed it). **Arrays**: arrays, strings and bytes are cut at 128 elements by "
    "default (a 541-beam `LaserScan` loses beams 128 and up); cut fields are listed "
    "in `payload._truncated_fields` and `note`. Raise `max_array_length` (up to "
    "65536, or null) or set `arrays_summary_only`. A message over the 1 MiB cap "
    "(`TOPICFORGE_MAX_SAMPLE_BYTES`) is dropped with a `note`. **Mock mode** returns "
    "demo-robot samples instantly. **Raises an MCP error** without a `ros2` CLI, for "
    "an unknown topic, or when the CLI fails. Never publishes. **Logs**: "
    "`min_log_level` / `node` (`/rosout` only) stream until `timeout_s` (at most "
    "2000 messages) and return the NEWEST `count` matches in stamp order; "
    "`peek_bag_samples` returns the FIRST matches."
    + (SUMMARY_RATE_NOTE % "`received_ns` (when the CLI printed each message)")
    + (
        " **Duration**: `ros2` CLI behind the ROS lock, one call at a time; returns within "
        "`timeout_s` (at most 40) plus about 3 s to stop the CLI and decode; ends within "
        "45 s, less time to collect after a wait; `busy` error if the lock is held "
        "(retry)."
    )
)

ANALYZE_BAG_DESC = (
    "Summarize a ROS 2 bag at `path` as a `BagAnalysis`: storage format, duration, "
    "message count, per-topic stats and `mode_effective`. Per topic `frequency_hz` is "
    "`(n - 1) / (last - first message time)` (`frequency_basis` `topic_span`), with "
    "`first_timestamp_ns`, `last_timestamp_ns` and `latched`; a latched topic whose "
    "messages all fall within 1 s (e.g. `/tf_static`) has a null `frequency_hz`. When "
    "the bag cannot be read locally or the bounded scan (8 s) is cut, the rate falls back "
    "to count / bag duration (`bag_duration`) and `note` says why. `anomalies` lists observed "
    "clock steps, silences of steady topics and TF edges, and backward header stamps "
    "(topic, time offset, size, no cause); empty means none found by these checks, not "
    "clean, and `note` says if the scan was cut or skipped. **Live mode** "
    "runs `ros2 bag info`, or `rosbags` without `ros2` or when it fails (`reader` says "
    "which); **mock mode** returns fixtures for any bag-like path. **Raises an MCP "
    "error** for a missing path, a directory without `metadata.yaml` or an unreadable bag."
    + ROS_LANE_NOTE
)

GRAPH_DIFF_DESC = (
    'ROS 2 graph only. Answer "what changed since it last worked": snapshot the graph '
    "(or `bag_path`) and compare it with `baseline`, as a `GraphDiff`. `changes` are "
    "node, topic, type, qos, rate and count differences; `added` = present now, absent "
    "from the baseline. `not_compared` names what a pair cannot support: nodes and "
    "counts need live against live, rates bag against bag, writer QoS a DDS backend. "
    "`/rosout` and `/parameter_events` count noise is only tallied in "
    "`builtin_changes_count`. The first call stores a snapshot and says so in `note`. "
    "Snapshots stay in memory (16, lost on restart). **Raises an MCP error** for an "
    "unknown id, a bad bag path or without a `ros2` CLI. **Mock mode**: the first call "
    "reports 3 changes, the next 0." + ROS_LANE_NOTE
)

DIAGNOSE_DESC = (
    'Answer "is anything wrong": sweep the ROS 2 graph and DDS bus in one call and rank '
    "what is found as a `Diagnosis`. Checks, in order: `environment`, `nodes`, "
    "`topics`, `dds` (QoS pairs, restarts), `tf` (8 s window), `logs` (`/rosout` "
    "errors), `responsiveness` (parameter reads, `node` first, `params_file` diff); "
    "`scope` runs one group. `findings` are `critical`, `warning` or `info`, with "
    "`evidence` and `suggested_fixes` (QoS changes, else read-only commands); `verdict` "
    "is one line. A check that errors or runs out of "
    "`budget_s` is in `checks_skipped`, never a tool error, and the verdict then says "
    "it is not verified. A clean result does not prove data flows. **Mock mode**: "
    "fixtures. **Duration**: holds the ROS lock up to `budget_s` and ends within 45 s; "
    "other ROS tools get `busy` meanwhile, `health_check` stays free."
)

LIST_PARTICIPANTS_DESC = (
    "List DDS participants observed on the bus as a `ParticipantListing`: `guid`, "
    "`vendor` (read from the GUID prefix, `unknown` for vendors that do not put theirs "
    "there, e.g. Dust DDS and RTI Connext; see `vendor_source`), optional `name` (the "
    "announced EntityName, e.g. `lidar_driver`; a participant with none is normal, not "
    "a fault: identify it by its topics), `hostname`, `domain_id` and `is_observer` "
    "(TopicForge's own read-only participant, listed like any other). Lifecycle: "
    "`status` (`active` / `left`), `first_seen_ns` / `last_seen_ns` (TopicForge's "
    "clock), `announced_ns` (DDS source timestamp) and, once left, `lost_ns` + "
    "`lost_time_source`: an upper bound of the death (exact after a clean shutdown; "
    "after a crash the lease expiry, so up to one lease earlier: 10 s Cyclone, 20 s "
    "Fast DDS, 100 s RTI). **Restarts**: a restarted node is a new GUID with the same "
    "name; `restarts_by_name` gives `lives` (GUIDs) and `departures` per name, so do "
    "not count GUIDs by hand (`seen_count` is not a restart count). Cyclone tracks "
    "discovery in the background; right after start the call waits up to 3 s for it. "
    "**Only the domain joined at startup is observed** (`health_check` "
    "`dds_domain_id`): a participant on another domain is INVISIBLE here, and "
    "`domain_id` does not switch domains (restart with `TOPICFORGE_DDS_DOMAIN_ID`). "
    "Works at the raw DDS layer, so non-ROS participants appear too. **Read-only by "
    "architecture**: it cannot publish, modify QoS or alter the bus. **Raises an MCP "
    "error** when no DDS module is active (install `topicforge[dds]`, set "
    "`TOPICFORGE_DDS_BACKEND=cyclone`); the mock backend returns fixtures." + DDS_LANE_NOTE
)

DETECT_QOS_MISMATCHES_DESC = (
    "Explain why DDS readers and writers on a topic do not talk, and who will. Pairs "
    "every reader with every writer per topic and returns a `MismatchScan`: `summary` "
    "(one line, read it first), `matched` (pairs DDS will connect given the announced "
    "QoS; data flow is not observed), `reports` (incompatible or risky QoS pairs, with "
    "participant names, requested vs offered values and the failed rule in `details`), "
    "`not_matched` (pairs DDS never matches: different partitions or type names; the "
    "QoS rules are NOT evaluated for them, and `latent_incompatible_policies` lists "
    "what would also break once fixed), `hints` (probable typos in orphan topic names, "
    "type id notes), plus `pairs_checked`, `topics_scanned`, `policies_checked` and "
    "`policies_unchecked`. Checked: Partition (`*` and `?` wildcards), type name, "
    "Reliability, Durability, Deadline, Liveliness, LatencyBudget, Ownership, "
    "DestinationOrder, DataRepresentation, History (risky only, where announced). "
    "**An empty `reports` with a non-empty `not_matched` still means no data flows**, "
    "and an all-empty result does not prove the bus healthy: discovery shows the QoS "
    "DECLARED, not runtime behavior (a 'deadline missed' log with compatible QoS means "
    "the writer's real period exceeds the deadline, which TopicForge cannot see). "
    "Pass `topic` to scope the scan. The lists are capped at 200 each (`truncated`; "
    "`*_total` keep the counts). A `late_joiner` match is a VOLATILE writer whose "
    "reader joined later on the same host: normal. Reports and partition splits carry "
    "`suggested_fixes`: text for a human, never applied by TopicForge; `verify_with` "
    "names the call that confirms the repair. **Read-only by architecture**. "
    "**Raises an MCP error** when no DDS module is active; mock returns fixtures." + DDS_LANE_NOTE
)

PEEK_DDS_SAMPLES_DESC = (
    "Peek recent samples on a raw DDS topic as a `SampleResult`, like `sample_messages` "
    "but read from the DDS layer (works without ROS 2). `count` defaults to 5, clamped "
    "to 50. (a) The builtin discovery topics (`DCPSParticipant`, `DCPSSubscription`, "
    "`DCPSPublication`) return structured discovery records: on Cyclone the CURRENT "
    "state (one per live participant or endpoint), not recent events (use "
    "`participant_events`); `DCPSPublication` / `DCPSSubscription` are the raw data "
    "behind `list_endpoints`. The topic may be `/scan`, `scan` or `rt/scan`. (b) User "
    "topics: decoding is off by default (`health_check` `dds_decode`): `count` 0, empty "
    "`samples`, a `note`; that does NOT mean the topic is silent. Use `list_endpoints` "
    "for its presence, writers, readers and QoS. When on, ROS 2 types are read (bounded, "
    "`note` if refused). A user topic not announced on the bus raises an error. Right after start the call waits up to 3 s "
    "for discovery. **Read-only by architecture**: it cannot publish. **Raises an MCP "
    "error** when no DDS module is active or the topic is not announced." + DDS_LANE_NOTE
)

PARTICIPANT_EVENTS_DESC = (
    "Return DDS participant lifecycle events (`discovered` / `lost`) from a recent "
    "window, e.g. 'who left in the last 5 minutes?', as a `ParticipantEventListing`, "
    "newest first: `guid`, `event_type`, `vendor`, `timestamp_ns`, `time_source`, "
    "`observed_ns`, `age_s` (seconds before the response; live only), optional `name` "
    "(announced name), `hostname` and `domain_id`. `time_source` is "
    "`dds_source_timestamp` (timestamp of the announcement or dispose) or "
    "`observed_local` (when TopicForge noticed, weakest). **Crash caveat**: a `lost` "
    "timestamp is an upper bound of the death: exact after a clean shutdown, the "
    "lease expiry after a crash, so the process died up to one lease earlier (10 s "
    "Cyclone, 20 s Fast DDS, 100 s RTI); the two cases cannot be told apart. "
    "**Restarts**: a restarted node is a new participant (one `lost` and one "
    "`discovered`, different `guid`, same `name`); `restarts_by_name` gives `lives` "
    "and `departures` per name over the window, so do not count GUIDs by hand. Capped "
    "at 200 events (`truncated`, `note`; reduce `lookback_s`). TopicForge only knows "
    "what happened since it started watching (`health_check.observer_started_ns`). "
    "**Backends**: Cyclone polls discovery in the background every 0.5 s, so a participant cycle faster than the reader's history "
    "depth between two passes can be missed; mock returns a fixture timeline. Right "
    "after start the call waits up to 3 s for discovery. **Read-only by "
    "architecture**. **Raises an MCP error** when no DDS module is active." + DDS_LANE_NOTE
)

TOPIC_METRICS_DESC = (
    "Return temporal metrics (frequency, sequence gaps, latency percentiles) for a DDS "
    "topic over a recent window as a `TopicMetrics`. **Read `status` first**: "
    "`unsupported_user_topic` means the payload is not decoded, so no metric exists: "
    "every null or 0 is not a measurement; `no_samples_yet` is nothing buffered or read. "
    "**Limits**: `frequency_basis` `source_timestamps` (decoding on) is a real publish rate "
    "from a bounded read of up to 10 s; otherwise the buffer fills only when "
    "`peek_dds_samples` runs, so `observed_frequency_hz` is how often it was called. "
    "`declared_frequency_hz` is `1 / deadline` of the shortest writer Deadline "
    "announced in discovery (declared, not measured; null when none). Also "
    "`sequence_gaps_count`, `latency_ns_p50/p95/p99` and availability flags. "
    "**Read-only by architecture**. **Raises an MCP error** when no DDS module is "
    "active or `window_s` is outside 1..3600. Right after start the call waits up to "
    "3 s for discovery." + DDS_LANE_NOTE
)

PEEK_BAG_SAMPLES_DESC = (
    "Peek up to `count` samples from a recorded bag file as a `SampleResult`: offline, "
    "for post-mortem (`peek_dds_samples` and `sample_messages` are live). Formats, by "
    "extension: MCAP (`.mcap`), rosbag2 SQLite (`.db3`), ROS 1 (`.bag`). Returns the "
    "**first** `count` messages of the topic in recording order, not the last "
    "(`sample_messages` keeps the newest log matches). `start_s` / `end_s` limit it to "
    "a bag-relative time range, empty with a `note` past the end; on `/rosout`, "
    "`min_log_level` and `node` keep the first `count` matching logs in range. "
    "**Clocks**: `timestamp_ns` is `header.stamp` when the message has a top-level "
    "header (`stamp_source` `header`), else the time in the body of `Clock`, "
    "`TFMessage` or `Log` (`payload`), else the bag record time (`recorded`); "
    "`recorded_ns` is always the record time. Each `payload` carries `_decode_status` "
    "(`full` / `partial` / `raw`). `count` defaults to 5, clamped to 50. **Uses the "
    "`rosbags` library** and needs the ROS 2 side of the runtime (an error on a "
    "DDS-only setup). Mock returns fixtures on canned paths. A bag without embedded "
    "definitions (Humble `.db3`) is decoded with those of its recorded distro, or "
    "Humble; `note` says which. Arrays over 4096 elements are cut and `note` lists "
    "them. **Read-only by architecture**. **Raises an MCP error** when the path does "
    "not exist, the topic is not in the bag, or `rosbags` is missing."
    + (SUMMARY_RATE_NOTE % "`recorded_ns` (the bag record time of the returned messages)")
    + BAG_NOTE
)

LIST_ENDPOINTS_DESC = (
    "List every DDS endpoint (writer and reader) announced on the bus as an "
    "`EndpointListing`; one `EndpointInfo` each with `role`, `dds_topic` (raw, e.g. "
    "`rt/scan`), `ros_topic` (`/scan`; `null` with `ros_topic_note` for a non-ROS "
    "topic), `type_name`, `type_id`, the owning `participant_guid` with "
    "`participant_name`, and a structured `qos`. Use it instead of parsing "
    "`peek_dds_samples` and joining GUID prefixes. **Large buses**: pass `fields` "
    "`compact` to replace each `qos` by a one-line `qos_summary` and drop `type_id`, "
    "`announced_ns` and `ros_topic_note`; the default `full` is verbose, so scope with "
    "`topic` or `participant_guid` to read one topic's full QoS. **Orphans**: "
    "`by_topic` rolls endpoints up per topic with `writer_count`, `reader_count`, "
    "`orphan` (`no_reader` = a writer nobody subscribes to, `no_writer` = a reader "
    "nobody publishes to) and the union of partitions. In `qos`, a duration or policy "
    "of `None` is infinite/unset or not announced. Among EXCLUSIVE writers the live "
    "one with the highest `ownership_strength` delivers; which one owns an instance "
    "is runtime state TopicForge cannot observe. **Departed endpoints**: a leaving "
    "participant's endpoints are remembered (last 200, 1 h) and shown in `by_topic` as "
    "`departed_writers` / `departed_readers` (listed in `endpoints` only with "
    "`include_departed`), so a topic that lost its only writer is explained in one "
    "call. **Topic filter**: `rt/scan` and `scan` match each other; a filter matching "
    "nothing returns a `note` with the closest topics. **This lists discovery facts, "
    "not data flow**: it cannot tell a silent or hung writer from a healthy one; pair "
    "it with `detect_qos_mismatches`. The observer's own endpoints are excluded unless "
    "`include_observer`; ROS 2 service and action endpoints (`rq/`, `rr/`, `rs/`, "
    "`rp/`, `ra/`, `ros_discovery_info`) unless `include_internal`. Capped at 500 "
    "endpoints (`truncated`, `total`); `by_topic` covers all matches. "
    "**Raises an MCP error** when no DDS module is active; mock returns a fixture." + DDS_LANE_NOTE
)
