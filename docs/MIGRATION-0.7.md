# Migrating from 0.6.x to 0.7.0

0.7.0 is `contract_version` 2. It is the one breaking release of the output contract: every
rename, split and removal happens here, so a client changes once. The rules behind the new
shapes are in [CONTRACT.md](CONTRACT.md); the served tools are in [TOOLS.md](TOOLS.md). The
tool list (twelve tools) and the read-only guarantee do not change.

## Check the version first

A client that must work with both releases reads `health_check` once:

```python
health = call("health_check")
contract = health.get("contract_version", 1)   # the field does not exist in 0.6.x
if contract != 2:
    ...  # speak the 0.6.x shapes
```

`health_check.contract_version == 2` is the only place the contract version is exposed. It is
an integer and is not repeated in other results. The server also sends short `instructions` in
the `initialize` result and two MCP prompts (`diagnose-dds-bus`, `inspect-ros2-robot`); both
are additive.

## What clients must change (checklist)

1. Read `contract_version` from `health_check` (above).
2. `list_topics`, `list_participants` and `participant_events` return an object, not a list:
   read `topics`, `participants` or `events`. Use `returned`, `total` and `truncated` instead of
   guessing a cap.
3. Stop reading `qos_reliability` and `qos_durability` on topics. `list_topics` has no QoS;
   `get_topic_info` returns `publisher_qos` and `subscription_qos`.
4. Rename the duration, frequency and count fields (table below), and the two tool inputs
   `topic_metrics.window_seconds` -> `window_s` and `participant_events.lookback_seconds` ->
   `lookback_s`.
5. Rename `_reason` fields on `health_check` to `_note`.
6. `list_endpoints`: read `dds_topic` and `ros_topic` instead of `topic`; read `total` instead of
   `total_discovered`; pass `include_internal: true` if you need the `rq/`, `rr/`, `rs/`, `rp/`,
   `ra/` and `ros_discovery_info` endpoints; read `hints` instead of the per-endpoint `activity`
   and `activity_note`.
7. Do not read `mode_effective` or `domain_id` from nested items: they are on the envelope.
8. `stamp_source` is never `null`: handle the five values `header`, `payload`, `recorded`,
   `dds_source`, `none`.
9. Do not read the removed fields (table below).
10. `sample_messages.timeout_s` accepts 1 to 40 (it was 1 to 45).
11. Configuration: `TOPICFORGE_DDS_BACKEND` accepts `mock`, `cyclone`, `fast` and `auto` only
    (`opendds`, `dust`, `rti`, `opensplice`, `coredx` and `intercom` are now rejected at startup).
    `health_check.dds_backend` no longer returns `opendds` or `dust`.
12. Python code that imported `RosAdapter` from `topicforge.adapters` uses `MiddlewareAdapter`.

## Tools that return a list now return one object

| Tool | 0.6.x | 0.7.0 |
| --- | --- | --- |
| `list_topics` | `[TopicInfo, ...]` | `{topics, returned, total, truncated, mode_effective, note}` |
| `list_participants` | `[ParticipantInfo, ...]` | `{participants, returned, total, truncated, domain_id, mode_effective, note}` |
| `participant_events` | `[ParticipantEvent, ...]` | `{events, returned, total, truncated, domain_id, mode_effective, note}` |

`participant_events` used to cut at 200 events without saying so; it now sets `truncated`.

## Renames

| Where | 0.6.x | 0.7.0 |
| --- | --- | --- |
| `BagAnalysis` | `duration_seconds` | `duration_s` |
| `TopicMetrics` | `window_seconds` | `window_s` |
| `TopicMetrics` | `window_seconds_actual` | `window_actual_s` |
| `TopicMetrics` | `frequency_hz_observed` | `observed_frequency_hz` |
| `TopicMetrics` | `frequency_hz_declared` | `declared_frequency_hz` |
| `EndpointListing` | `departed_endpoints` | `departed_endpoint_count` |
| `EndpointListing` | `excluded_observer_endpoints` | `excluded_observer_endpoint_count` |
| `EndpointListing` | `total_discovered` | `total` |
| `EndpointInfo`, `TopicSummary` | `topic` | `dds_topic` and `ros_topic` |
| `HealthReport` | `dds_inactive_reason` | `dds_inactive_note` |
| `HealthReport` | `payload_decoding_reason` | `payload_decoding_note` |
| input `topic_metrics` | `window_seconds` | `window_s` |
| input `participant_events` | `lookback_seconds` | `lookback_s` |

## Removed

| Field | Why |
| --- | --- |
| `TopicInfo.reader_count`, `writer_count`, `qos_profile` | no adapter ever filled them |
| `TopicInfo.qos_reliability`, `qos_durability` | replaced by `publisher_qos` and `subscription_qos` |
| `BagAnalysis.samples_decoded_count`, `participants_recorded`, `recording_duration_ns` | never filled |
| `EndpointInfo.activity`, `activity_note` | always `null` / the same sentence on every endpoint; now one root-level `hints` entry |
| `EndpointInfo.domain_id`, `mode_effective` | on the listing envelope |
| `mode_effective` on `ParticipantInfo`, `ParticipantEvent`, `MismatchReport` | on the envelope |
| `stamp_source: null` | replaced by the value `none` |

## Added (no action needed)

`health_check`: `contract_version`, `rmw_implementation`, `rmw_source`, `note`. `note` on
`get_topic_info`, `detect_qos_mismatches` and `topic_metrics`. `get_topic_info`:
`publisher_nodes`, `subscriber_nodes`. `analyze_bag` topics: `kind`. `list_endpoints`:
`include_internal`, `hidden_internal_endpoint_count`, `hints`. `ParticipantInfo.node_names` and
`node_names_source`. MCP prompts and server instructions.

## Before and after

Examples come from the mock backend (a fictional robot); live values differ.

### `list_topics`

0.6.x:

```json
[{"name": "/cmd_vel", "message_type": "geometry_msgs/msg/Twist", "publisher_count": 1,
  "subscriber_count": 1, "qos_reliability": "reliable", "qos_durability": null,
  "reader_count": null, "writer_count": null, "qos_profile": null, "mode_effective": "mock"}]
```

0.7.0:

```json
{"topics": [{"name": "/cmd_vel", "message_type": "geometry_msgs/msg/Twist",
             "publisher_count": 1, "subscriber_count": 1}],
 "returned": 5, "total": 5, "truncated": false, "mode_effective": "mock", "note": null}
```

### `get_topic_info`

0.6.x:

```json
{"name": "/scan", "message_type": "sensor_msgs/msg/LaserScan", "publisher_count": 1,
 "subscriber_count": 1, "qos_reliability": "best_effort", "qos_durability": null,
 "reader_count": null, "writer_count": null, "qos_profile": null, "mode_effective": "mock"}
```

0.7.0:

```json
{"name": "/scan", "message_type": "sensor_msgs/msg/LaserScan", "publisher_count": 1,
 "subscriber_count": 1,
 "publisher_qos": {"reliability": "best_effort", "durability": "volatile", "endpoint_count": 1},
 "publisher_qos_note": null,
 "subscription_qos": {"reliability": "best_effort", "durability": "volatile", "endpoint_count": 1},
 "subscription_qos_note": null,
 "publisher_nodes": ["/lidar_driver"], "subscriber_nodes": ["/nav_planner"],
 "mode_effective": "mock", "note": null}
```

A side with no endpoint is `null` and its `_note` says why.

### `health_check`

0.6.x (excerpt):

```json
{"mode": "mock", "server_version": "0.6.4", "dds_backend": "mock",
 "dds_inactive_reason": null,
 "payload_decoding": "disabled", "payload_decoding_reason": "user-topic payload decoding is switched off ..."}
```

0.7.0 (excerpt):

```json
{"mode": "mock", "server_version": "0.7.0", "contract_version": 2, "dds_backend": "mock",
 "dds_inactive_note": null,
 "payload_decoding": "disabled", "payload_decoding_note": "user-topic payload decoding is switched off ...",
 "rmw_implementation": null, "rmw_source": "none", "note": null}
```

`rmw_source` is `env`, `distro_default` or `none`. It comes from `RMW_IMPLEMENTATION` or the
default of `ROS_DISTRO`, never from the running graph.

### `list_endpoints`

0.6.x:

```json
{"domain_id": 0, "endpoints": [{"topic": "rt/scan", "role": "writer", "domain_id": 0,
                                "mode_effective": "mock", "activity": null,
                                "activity_note": "Liveness is not observed ..."}],
 "by_topic": [{"topic": "rt/scan", "writer_count": 1, "reader_count": 1}],
 "total_discovered": 7, "returned": 7, "truncated": false,
 "departed_endpoints": 0, "excluded_observer_endpoints": 0, "mode_effective": "mock"}
```

0.7.0:

```json
{"domain_id": 0,
 "endpoints": [{"dds_topic": "rt/scan", "ros_topic": "/scan", "role": "writer"}],
 "by_topic": [{"dds_topic": "rt/scan", "ros_topic": "/scan", "writer_count": 1, "reader_count": 1}],
 "total": 7, "returned": 7, "truncated": false,
 "departed_endpoint_count": 0, "excluded_observer_endpoint_count": 0,
 "hidden_internal_endpoint_count": 0,
 "hints": ["Liveness is not observed: TopicForge holds no reader on user topics, so it cannot tell a silent or hung writer from a healthy one."],
 "mode_effective": "mock", "note": null}
```

`ros_topic` is `null` with a `ros_topic_note` when the DDS topic is not a ROS 2 topic.
`total` still counts every endpoint in the discovery cache, so `returned < total` can be
explained by the filters, the observer exclusion, `hidden_internal_endpoint_count` or the cap
(`truncated` is only the cap).

### `analyze_bag`

0.6.x (excerpt):

```json
{"path": "/tmp/demo.mcap", "duration_seconds": 42.5, "message_count": 1287,
 "topics": [{"name": "/cmd_vel", "message_count": 425, "frequency_hz": 10.0}],
 "samples_decoded_count": 0, "recording_duration_ns": 42500000000, "participants_recorded": []}
```

0.7.0 (excerpt):

```json
{"path": "/tmp/demo.mcap", "duration_s": 42.5, "message_count": 1288,
 "topics": [{"name": "/cmd_vel", "message_count": 425, "kind": "user", "frequency_hz": 10.0}]}
```

`kind` is `user`, `rosbag2_internal` or `ros_builtin`.

### `list_participants` (and `participant_events`)

0.6.x returned `[{"guid": ..., "name": ..., "domain_id": 0, "mode_effective": "mock", ...}]`.
0.7.0:

```json
{"participants": [{"guid": "010f1c2a-3b4c-5d6e-7f80-000000000001", "name": "lidar_driver",
                   "vendor": "cyclone", "domain_id": 0, "node_names": [],
                   "node_names_source": "none"}],
 "returned": 4, "total": 4, "truncated": false, "domain_id": 0,
 "mode_effective": "mock", "note": null}
```

## Behaviour changes that are not renames

- Calls are serialized per backend (a ROS lane and a DDS lane) and have a 45 s wall budget, lock
  wait included. A call that cannot get its lane fails at once with `busy: another <ros|dds>
  call is running, retry`. `health_check` and `peek_bag_samples` take no lock.
- `health_check.sim_clock_published` reports what the last graph read saw; it no longer runs the
  `ros2` CLI.
- The server requires the MCP Python SDK 2.x (`mcp>=2.3,<3`).
- Optional local HTTP transport: `topicforge --transport streamable-http --port N`.
