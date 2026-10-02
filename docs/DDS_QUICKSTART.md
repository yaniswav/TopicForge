# DDS quickstart

A short tour of TopicForge's DDS observability tools. The server joins the bus as a read-only DDS-RTPS participant and reads the builtin discovery topics, which the OMG protocol standardizes for every conformant vendor (observed so far: Cyclone DDS and Dust DDS). The `MiddlewareAdapter` protocol has no write method, so the MCP client cannot publish back on any backend. This guide does not assume ROS2.

Validation status. The Cyclone adapter has run against a live bus, with a Python / Cyclone participant and Dust DDS participants in Rust, Python and C, on Windows and in CI (Ubuntu and Windows, `.github/workflows/demo.yml`); see [`scripts/integration/README.md`](../scripts/integration/README.md). The Fast DDS adapter has never run against a bus, and no RTI, OpenDDS, CoreDX or OpenSplice participant has been observed yet. For runnable live scenarios (who is on the bus, why two nodes cannot talk, a node that crashed, a late joiner that misses data), see [`examples/dds/README.md`](../examples/dds/README.md). Multi-vendor positioning: [`dds-interop-matrix.md`](dds-interop-matrix.md).

## 1. Mock mode

The mock fixtures expose every tool with no DDS SDK:

```bash
pip install topicforge
TOPICFORGE_MODE=mock python -m topicforge
```

- `list_participants(domain_id=0)` returns four participants: two CycloneDDS (`mock-robot`, `mock-laptop`), one Fast DDS (`mock-aerospace-node`) and one Dust DDS in Rust (`mock-rust-node`). The `vendor` field comes from the OMG vendor id: `cyclone`, `fast`, `rti`, `rti_micro`, `opensplice`, `opendds`, `coredx`, `intercom`, `dust`, `mock` or `unknown`.
- `detect_qos_mismatches(topic=None)` returns a `MismatchScan` with one report for `/dds/qos_mismatch`: a RELIABLE reader against a BEST_EFFORT writer.
- `list_endpoints()` returns the mock writers and readers with their QoS and a `by_topic` roll-up.
- `peek_dds_samples(topic="/dds/well_matched", count=3)` returns three deterministic samples. The mock only knows `/dds/well_matched`, `/dds/qos_mismatch`, `/dds/topicforge/example` and `/dds/topicforge/opaque`, and raises "Unknown DDS topic" for anything else.
- `topic_metrics(topic="/dds/heartbeat_10hz", window_seconds=60)` returns a pre-filled 10 Hz buffer (100 samples, no gaps, 50 ms latency). A live adapter behaves differently, see section 5 and [`examples/04-monitor-topic-frequency.md`](../examples/04-monitor-topic-frequency.md).

The mock illustrates payload shapes that no live adapter produces today, such as the `"full"` decode status on `/dds/topicforge/example`.

## 2. Live mode: choose a backend

Cyclone is the one with a PyPI install path:

```bash
pip install topicforge[dds]             # same as [dds-cyclone]
TOPICFORGE_DDS_BACKEND=cyclone python -m topicforge
```

`cyclonedds` publishes wheels for CPython 3.10 to 3.13 on Windows, Linux and macOS (as of 11.0.1). A background thread reads the builtin DCPS topics through `BuiltinDataReader`. On a real bus `list_participants` reports each participant's `name` (EntityName QoS) and `hostname` (the `__Hostname` discovery property) when the remote participant sets them, and the vendor from the first two bytes of the GUID prefix; implementations that do not follow that convention (Dust DDS, RTI by default) show vendor `unknown`.

Fast DDS has no PyPI install path. The `fastdds` binding is not published, so TopicForge declares no extra for it. Build eProsima's [Fast-DDS-python](https://github.com/eProsima/Fast-DDS-python) from source (it needs the Fast DDS C++ libraries and SWIG), install it into the same environment, then `TOPICFORGE_DDS_BACKEND=fast`. The adapter was written against the 2.6.x API and has never run against a bus.

Auto (`TOPICFORGE_DDS_BACKEND=auto`) probes importable bindings in the order `fast`, `cyclone`, `mock`. With only the PyPI extra installed it resolves to Cyclone. `opendds` and `dust` are permanent stubs that always report unavailable and are not in the chain.

Domain. `TOPICFORGE_DDS_DOMAIN_ID` (`0..232`, default `0`) is joined at startup; the `domain_id` tool parameter exists for protocol uniformity only. Changing domains needs a restart.

Commercial vendors. `rti`, `opensplice`, `coredx` and `intercom` are rejected at startup with a configuration error. You do not need them to observe an RTI bus; for what a native RTI adapter would add (secure domains with vendor credentials, shared-memory-only deployments) see [`pro.md`](pro.md).

## 3. The QoS mismatch scenario

The canonical "my subscriber does not receive" case. Against the mock:

```
> Detect QoS mismatches on the current bus.

[tool call: detect_qos_mismatches]
{
  "reports": [
    {
      "topic": "/dds/qos_mismatch",
      "reader_guid": "...", "writer_guid": "...",
      "reader_participant_name": "lidar_driver", "writer_participant_name": "nav_planner",
      "incompatible_policies": ["Reliability"],
      "severity": "incompatible",
      "details": [{"policy": "Reliability", "requested": "RELIABLE",
                   "offered": "BEST_EFFORT", "rule": "a RELIABLE reader needs a RELIABLE writer"}],
      "mode_effective": "mock"
    }
  ],
  "not_matched": [],
  "hints": ["Topic '/dds/topicforge/opaque' has writers but no reader: there is no pair to compare."],
  "pairs_checked": 3, "topics_scanned": 4,
  "policies_checked": ["Reliability", "Durability", "Deadline", "Liveliness", "..."],
  "policies_unchecked": ["Presentation: ...", "..."],
  "mode_effective": "mock"
}
```

The result is a `MismatchScan` envelope. Live adapters render GUIDs in dotted form (`xxxxxxxx.xxxxxxxx.xxxxxxxx.xxxxxxxx`). From this the agent can suggest a concrete fix: the writer is BEST_EFFORT but the reader requires RELIABLE, so relax the reader or upgrade the writer. The analysis is vendor-neutral pure code (`adapters/common/qos_analyzer.py`, `qos_scan.py`) over canonical `QosProfile` models, so it does not depend on which backend produced the discovery samples.

Order of checks per reader/writer pair: Partition first (wildcards `*` and `?` on one side match; wildcard against wildcard never does), then the type name, then the RxO policies. A pair separated by partition or type goes to `not_matched` and no QoS rule is run on it, so a partition split is never reported as a Reliability problem; an empty `reports` with a non-empty `not_matched` still means no data flows. RxO policies compared: Reliability, Durability, Deadline, Liveliness (kind and lease), LatencyBudget, Ownership (kind only), DestinationOrder, DataRepresentation. History (KEEP_ALL reader, KEEP_LAST writer) is reported as `risky`, not as an incompatibility. A policy a side did not announce is skipped and counted in a hint. `policies_unchecked` lists what stays out of scope (Presentation, XTypes assignability, runtime liveliness, anything not discoverable): discovery shows declared QoS, not runtime behavior, so an empty result is not proof the bus is healthy.

## 4. Composite adapter and backend selection

When `ros2` is on PATH and a DDS backend starts, TopicForge builds both a `Ros2CliAdapter` and the DDS adapter behind a `CompositeAdapter`: the five ROS2 graph and bag tools go to the CLI, the seven DDS tools to the DDS backend. An explicit DDS backend (`cyclone`, `fast`, `auto`) is honoured in every mode except `mock`, including on a host without `ros2`.

| `TOPICFORGE_MODE` | `TOPICFORGE_DDS_BACKEND` | `ros2` on PATH | Active adapter | ROS2 graph and bag tools | DDS tools |
| --- | --- | --- | --- | --- | --- |
| `mock` | any | any | `MockAdapter` | fixtures | fixtures |
| `live` / `auto` | `mock` (default) | yes | `Ros2CliAdapter` | CLI | raise "DDS module is not active" |
| `live` / `auto` | `cyclone` / `fast` / `auto` | yes | `CompositeAdapter` | CLI | real binding |
| `live` / `auto` | `cyclone` / `fast` / `auto` | no | the DDS adapter alone | raise "DDS observability only" | real binding |
| `live` / `auto` | `cyclone` / `fast`, binding missing or participant fails | any | `Ros2CliAdapter` if `ros2` is on PATH, else `MockAdapter` | CLI, or fixtures | raise, or fixtures |
| `live` / `auto` | `mock` | no | `MockAdapter` | fixtures | fixtures |

A DDS problem never prevents startup: the server logs a warning naming the cause and keeps going. Only an invalid or removed configuration value stops it. `health_check` reports what was actually built: `mode` (can differ from `requested_mode`), `ros_backend` (`ros2_cli`, `mock`, `none`) and `dds_backend` (`cyclone`, `fast`, `mock`, `none`).

## 5. Scope of the discovery tools

`list_endpoints` returns every announced writer and reader as a typed record: role, topic, type, owning participant (guid, name, vendor), structured QoS and announcement time. Its `by_topic` roll-up flags orphans (`no_reader`, `no_writer`) and lists endpoints of participants that left under `departed_writers` / `departed_readers`. TopicForge's own endpoints are excluded unless `include_observer` is set. Cyclone and the mock serve it; the Fast DDS backend raises "not supported yet". Use it first to find out who talks on which topic, and `detect_qos_mismatches` to learn why they do not match.

`peek_dds_samples` is structured on the three builtin discovery topics, with both backends. Builtin names carry no leading `/`:

```
peek_dds_samples(topic="DCPSParticipant", count=5)
peek_dds_samples(topic="DCPSSubscription", count=10)
peek_dds_samples(topic="DCPSPublication", count=10)
```

Each sample is the cached current discovery state, not a stream. The payload carries `vendor`, `guid`, `topic_name` and, for endpoints, `role`, `participant_guid`, `participant_name`, `type_id`, `qos`, `announced_ns` and `is_observer`. The sample `timestamp_ns` is the announcement time.

User topics are not decoded. For a user topic the tool returns count 0 and a `note`:

```json
{
  "topic": "/my/topic",
  "count": 0,
  "samples": [],
  "mode_effective": "live",
  "note": "payload decoding is disabled for DDS user topics, so no samples are returned; this does not mean the topic is silent. Use list_endpoints for the topic's presence, writers, readers and QoS"
}
```

An empty result says nothing about traffic. The earlier decode path never worked on either backend and is disabled until it can be validated against a real bus. The `"full"` and `"partial"` decode statuses stay in the schema and the mock emits examples of them, but no live adapter produces them.

`topic_metrics` only has data for the builtin topics. For a user topic it returns `status="unsupported_user_topic"`, and its null fields are not a measurement. For a builtin topic, `frequency_hz_observed` is how often you called `peek_dds_samples` (one call yields `null`), `sequence_numbers_available` is `false` and the latency percentiles are `null`, because builtin samples carry no publish timestamp. `frequency_hz_declared` is `1 / deadline` for the shortest Deadline a writer announced, when there is one. Use it to watch discovery-layer churn, not to check a publish rate.

Lifecycle. On Cyclone a background thread (0.5 s period) reads the three builtin discovery topics and feeds the caches behind every discovery tool, so `participant_events` and `list_participants` do not depend on when you call them. A participant that cycles faster than the discovery history depth between two passes can still be missed. Event times come from DDS (`announced_ns`, `lost_ns`), with `time_source` saying which clock. A `lost` time is an upper bound of the death: a crash is only noticed when the lease expires, and a crash cannot be told from a clean leave. A writer that is alive but silent is not observable without reading its data. Fast DDS captures arrival and removal through listener callbacks.

## 6. Open work

Wider real-bus validation (Fast DDS, RTI, OpenDDS, CoreDX, OpenSplice; re-enabling user-topic decoding depends on it), an opt-in data probe to catch a hung writer (planned for 0.5.6), and DDS Security, which is not handled at all: a participant without credentials sees an empty secure bus. The roadmap is in [`product-plan.md`](product-plan.md). Errors and fixes: [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md). Report what you see on a real domain at https://github.com/yaniswav/TopicForge/issues.
