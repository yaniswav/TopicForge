# DDS quickstart: TopicForge 0.5.3

A 5-minute tour of TopicForge's multi-vendor DDS observability module. Both backends (Eclipse CycloneDDS and eProsima Fast DDS) join the bus as **read-only DDS-RTPS participants** and, by the OMG protocol guarantee, observe every conformant vendor on the wire. The `MiddlewareAdapter` protocol does not expose a write method, so the MCP client cannot publish back to the bus on any backend.

**Validation status.** The Cyclone adapter has been run once against a live bus on Windows 11, with a Python / Cyclone participant and Dust DDS participants in Rust, Python and C, through the demo in [`scripts/integration/`](../scripts/integration/README.md). The Fast DDS adapter has never been run against a bus, and no RTI, OpenDDS, CoreDX or OpenSplice participant has been observed yet. The unit tests cover the binding-free logic (QoS analysis, lifecycle and metrics buffers) and the mock backend. Treat the rest of this guide as a description of what the code is designed to do, and report what you see on a real domain.

See [`docs/dds-interop-matrix.md`](dds-interop-matrix.md) for the canonical multi-vendor positioning and the [OMG May 2025 interop reference](projet-file/references/omg-dds-interop-2025-05-08.xlsx).

This guide does **not** assume you have ROS2 installed.

---

## 1. Mock mode: 30-second demo

The deterministic mock fixtures expose the full tool surface without any DDS SDK or middleware. Useful for evaluating the tools before pulling in a real binding.

```bash
pip install topicforge
TOPICFORGE_MODE=mock python -m topicforge
```

In the spawned MCP client (Claude Desktop, Claude Code, Cursor, ...), the 5 DDS tools (`list_participants`, `detect_qos_mismatches`, `peek_dds_samples`, `participant_events`, `topic_metrics`) are available alongside the ROS2 graph and bag tools:

- `list_participants(domain_id=0)` -> returns 4 mock participants on domain 0: two CycloneDDS (`mock-robot`, `mock-laptop`), one Fast DDS (`mock-aerospace-node`) and one Dust DDS in Rust (`mock-rust-node`). The `vendor` field is derived from the OMG vendor id and takes one of `cyclone`, `fast`, `rti`, `rti_micro`, `opensplice`, `opendds`, `coredx`, `intercom`, `dust`, `mock`, `unknown`.
- `detect_qos_mismatches(topic=None)` -> returns 1 `MismatchReport` for `/dds/qos_mismatch` (deliberate Reliability incompatibility; RELIABLE reader vs BEST_EFFORT writer).
- `peek_dds_samples(topic="/dds/well_matched", count=3)` -> returns 3 deterministic samples; `peek_dds_samples(topic="/dds/qos_mismatch", count=1)` returns 1 sample with a `qos_note` field annotating the mismatch. The mock only knows its four fixture topics (`/dds/well_matched`, `/dds/qos_mismatch`, `/dds/ddsforge/example`, `/dds/ddsforge/opaque`); it does not serve the builtin `DCPSParticipant` topics and raises "Unknown DDS topic" for anything else.
- `topic_metrics(topic="/dds/heartbeat_10hz", window_seconds=60)` -> returns a pre-filled 10 Hz buffer (100 samples, no gaps, 50 ms latency). See [`examples/04-monitor-topic-frequency.md`](../examples/04-monitor-topic-frequency.md) for the exact output and for what the live adapters do differently.

Mock fixtures are stable across runs: you can write integration tests against them. They also illustrate payload shapes that no live adapter produces today (for example the `"full"` decode status on `/dds/ddsforge/example`, see section 5).

---

## 2. Live mode: choose your backend

Two OSS Python adapters exist. Only one of them has a PyPI install path.

### 2.a Eclipse CycloneDDS

```bash
pip install topicforge[dds-cyclone]     # or topicforge[dds], same thing
TOPICFORGE_DDS_BACKEND=cyclone python -m topicforge
```

`cyclonedds` publishes wheels for CPython 3.10 to 3.13 on Windows (x86-64), Linux (manylinux, x86-64) and macOS (x86-64, arm64), as of cyclonedds 11.0.1. The extra pulls `cyclonedds>=0.10`.

The CycloneDDS adapter uses `cyclonedds.builtin.BuiltinDataReader` for polling-style discovery on the DCPS builtin topics, via `read_iter`. Each read is wrapped in `itertools.islice`, because `read_iter` resets its timeout on every received sample and would otherwise never return on a topic publishing faster than the timeout. QoS policies are introspected via `Policy.*` class names.

### 2.b eProsima Fast DDS (binding built from source)

There is no `[dds-fast]` extra. The `fastdds` Python binding is not on PyPI, so TopicForge cannot declare it as a dependency (0.5.3 removed the extra: a name nobody owns on PyPI is a dependency-confusion risk, and the install failed anyway). To use the Fast DDS adapter, build eProsima's Python binding from their sources ([eProsima/Fast-DDS-python](https://github.com/eProsima/Fast-DDS-python), which requires the Fast DDS C++ libraries and SWIG), install it into the same environment as TopicForge, then:

```bash
TOPICFORGE_DDS_BACKEND=fast python -m topicforge
```

The adapter was written against the `fastdds` 2.6.x Python API. It attaches a `DomainParticipantListener`-shaped object to a freshly created participant and accumulates discovery callbacks under an RLock. A bounded `discovery_wait_ms=1500` warm-up after participant creation gives the listener time to populate before the first tool call. If the binding is not importable, the server logs a warning that says so and falls back (see section 4).

### 2.c Auto resolution

```bash
TOPICFORGE_DDS_BACKEND=auto python -m topicforge
```

`auto` probes for importable bindings in this order: `fast`, then `cyclone`, then `mock`. With only `cyclonedds` installed, which is the case for anyone using the PyPI extra, it resolves to Cyclone. `opendds` and `dust` are not part of the chain: their adapters are permanent stubs that always report unavailable, and `pyopendds` (which does exist on PyPI) is never used by the OpenDDS adapter. Both stay selectable by name so that choosing one produces a clear warning instead of a silent fallback.

### Domain selection

```bash
TOPICFORGE_DDS_DOMAIN_ID=42 python -m topicforge
```

Accepts `0..232` (DDS spec range). Default is `0`: the same default used by most ROS2 setups. The domain is joined at startup; changing it needs a restart.

### Commercial vendors: RTI Connext and others

`TOPICFORGE_DDS_BACKEND` used to accept `rti`, `opensplice`, `coredx` and `intercom` for the retired Pro tier. Since 0.5.3 these values are rejected at startup with a configuration error (`topicforge: configuration error: ...`, exit code 2). You do not need them to observe an RTI bus: a Cyclone participant already discovers RTI, CoreDX and OpenSplice participants through standard RTPS discovery, and `rti` remains a valid `vendor` tag on the participants it reports. For what a native RTI adapter adds (secure domains with vendor credentials, shared-memory-only deployments) and how to arrange it, see [`docs/pro.md`](pro.md).

---

## 3. The QoS mismatch scenario

Both real backends and the mock fixtures encode the canonical "subscriber doesn't receive" debugging case. From an MCP client, against the mock:

```
> Detect QoS mismatches on the current bus.

[tool call: detect_qos_mismatches]
[result]
[
  {
    "topic": "/dds/qos_mismatch",
    "reader_guid": "010f1c2a-3b4c-5d6e-7f80-000000000001",
    "writer_guid": "010f1c2a-3b4c-5d6e-7f80-000000000002",
    "incompatible_policies": ["Reliability"],
    "severity": "incompatible",
    "mode_effective": "mock"
  }
]
```

The live adapters render GUIDs in dotted form (`xxxxxxxx.xxxxxxxx.xxxxxxxx.xxxxxxxx`) rather than the dashed form of the mock fixtures. An LLM reading this output has enough information to suggest a concrete fix ("the writer is BEST_EFFORT but the reader requires RELIABLE; either relax the reader or upgrade the writer"). That is the diagnostic loop the DDS module is designed to support, and it is independent of which backend produced the discovery samples, because the vendor-neutral pure analyzer at `src/topicforge/adapters/common/qos_analyzer.py` operates on canonical `QosProfile` Pydantic models.

The analyzer covers four policies: **Reliability**, **Durability**, **History** and **Deadline**. Liveliness, Ownership, Partition, TimeBasedFilter and LatencyBudget are not checked, and a mismatch on one of those will not be reported. Liveliness, Ownership and Partition are the ones that tend to matter in redundant production systems.

---

## 4. Composite adapter and backend selection

When the `ros2` CLI is on PATH and a DDS backend starts, TopicForge instantiates **both** a `Ros2CliAdapter` and the chosen DDS adapter behind a `CompositeAdapter` and routes per tool: the ROS2 graph and bag tools (`list_topics`, `get_topic_info`, `sample_messages`, `analyze_bag`, `peek_bag_samples`) hit the CLI half, the five DDS tools (`list_participants`, `detect_qos_mismatches`, `peek_dds_samples`, `participant_events`, `topic_metrics`) hit the DDS half. The adapter `name` collapses to `"ros2_cli+cyclone"` or `"ros2_cli+fast"`; the composite reports `live` whenever either half is live.

A DDS backend named explicitly (`cyclone`, `fast`, or `auto`) is honoured in every mode except `TOPICFORGE_MODE=mock`, including `auto` mode on a host without `ros2`. The effective result:

| `TOPICFORGE_MODE` | `TOPICFORGE_DDS_BACKEND` | `ros2` on PATH | Active adapter | ROS2 graph and bag tools | DDS tools |
| ----------------- | ------------------------ | -------------- | -------------- | ------------------------ | --------- |
| `mock` | (any) | (any) | `MockAdapter` | fixtures | fixtures |
| `live` / `auto` | `mock` (default) | yes | `Ros2CliAdapter` | work (CLI) | raise "DDS module is not active" |
| `live` / `auto` | `cyclone` / `fast` / `auto` | yes | `CompositeAdapter` | work (CLI) | work (real binding) |
| `live` / `auto` | `cyclone` / `fast` / `auto` | no | the DDS adapter alone | raise "DDS observability only" | work (real binding) |
| `live` / `auto` | `cyclone` / `fast` | (any), binding missing or participant fails to start | `Ros2CliAdapter` if `ros2` is on PATH, else `MockAdapter` | CLI, or fixtures | raise, or fixtures |
| `live` / `auto` | `mock` | no | `MockAdapter` | fixtures | fixtures |
| (any) | `rti`, `opensplice`, `coredx`, `intercom` | (any) | none: startup is refused | n/a | n/a |

In the binding-missing row the server logs a warning naming the cause and keeps going; it never fails to start because of a DDS problem. Only a configuration error (an invalid or removed value) stops it.

`health_check` reports what was actually built, not what was requested: `mode` is the mode of the serving adapter (it can differ from `requested_mode`, for example `live` requested without `ros2` ends on `mock`), `ros_backend` is `ros2_cli`, `mock` or `none`, and `dds_backend` is `cyclone`, `fast`, `mock`, or `none` when only the ROS2 CLI serves. `middleware_available` tells you whether the configured backend's binding is importable when no DDS backend is serving, so a missing binding stays visible.

---

## 5. Scope of `peek_dds_samples` and `topic_metrics`

`peek_dds_samples` is full-fidelity on the **three** builtin DCPS topics with both backends:

```
peek_dds_samples(topic="DCPSParticipant", count=5)
peek_dds_samples(topic="DCPSSubscription", count=10)
peek_dds_samples(topic="DCPSPublication", count=10)
```

Builtin names carry no leading `/`. Each sample payload carries `vendor`, `guid`, `topic_name` and a `_raw_text` rendering of the binding's discovery object.

**User topics are not decoded.** Since 0.5.3, on both Cyclone and Fast, `peek_dds_samples` on a user topic does two things: it checks that some reader or writer on the bus announces the topic (otherwise it raises an error), and it returns one placeholder sample. The placeholder looks like this:

```json
{
  "topic": "/my/topic",
  "count": 1,
  "samples": [
    {
      "topic": "/my/topic",
      "message_type": "dds/unknown",
      "timestamp_ns": 0,
      "payload": {
        "_decode_status": "raw",
        "_decode_note": "dynamic XTypes decode is disabled in this release pending real-bus validation; topic presence is reported, payload is not decoded",
        "_raw_bytes_hex": ""
      }
    }
  ],
  "mode_effective": "live"
}
```

Read it as "this topic exists on the bus". It is not a received message, no payload bytes are captured (`_raw_bytes_hex` is empty), and it is never counted by `topic_metrics`. On Fast DDS the `_decode_note` text differs (it says dynamic XTypes decode is not implemented for Fast DDS); the shape is the same.

The reason is that the previous decode path never worked. On Cyclone it called `get_types_for_typeid` with the wrong arity and did not unpack the tuple it returned, and both errors were swallowed at DEBUG level; on Fast the decoder returned `None` unconditionally. Shipping a repair that has never run against a bus would have been the less honest option. The `"full"` and `"partial"` values of `_decode_status` remain in the schema, and the mock backend emits a `"full"` example (`/dds/ddsforge/example`) and a `"raw"` example with hex bytes (`/dds/ddsforge/opaque`) so clients can be tested against the wire shape, but no live adapter produces either today. Re-enabling decode needs a real bus to validate against.

**`topic_metrics` only has data for the builtin topics.** The metrics buffer is filled as `peek_dds_samples` surfaces samples, neither binding offers an at-receive callback, and only the builtin topics surface any. For a user topic the tool returns `samples_observed=0`, which is a valid and honest answer, not an error. For a builtin topic:

- `frequency_hz_observed` reflects how often you called `peek_dds_samples` (samples from one call share a single capture instant, so a single call yields `null`). It is the cadence of your own tool calls, not the publication rate of anything on the bus.
- `sequence_numbers_available` is `false` and `sequence_gaps_count` stays `0`, because builtin samples carry no application sequence number.
- `latency_ns_p50/p95/p99` are `null` and `latency_available` is `false`, because builtin samples carry no publish timestamp.
- `frequency_hz_declared` is always `null`. Nothing populates it.

Use it to watch discovery-layer churn. It cannot tell you whether an application topic is meeting its publish rate.

The other DDS tools (`list_participants`, `detect_qos_mismatches`, `participant_events`) do not depend on payload deserialization. `participant_events` on Cyclone is polling-driven: its lifecycle log only updates when `list_participants` runs, so a participant that joined and left between two calls is invisible. Fast DDS captures both arrival and removal through listener callbacks.

---

## 6. What's next

Open items, in the order they matter. None is scheduled; see [`docs/product-plan.md`](product-plan.md).

- **Wider real-bus validation.** The Cyclone adapter has had one run against Cyclone and Dust DDS participants ([`scripts/integration/README.md`](../scripts/integration/README.md)). The Fast DDS adapter and the RTI, OpenDDS, CoreDX and OpenSplice participants are still unobserved, and re-enabling user-topic payload decoding is blocked on that.
- **Extended QoS coverage**: Liveliness, Ownership, Partition, TimeBasedFilter, LatencyBudget.
- **DDS Security**: not handled at all. A participant without credentials sees an empty secure bus, which excludes authenticated domains.

Full strategic roadmap lives in [`docs/product-plan.md`](product-plan.md) and the DDS module spec at [`docs/projet-file/mcp-02-spec.md`](projet-file/mcp-02-spec.md).

---

## 7. Troubleshooting

See [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) for the polished error messages. DDS-specific notes:

- **`pip install topicforge[dds-cyclone]` fails on Python 3.14 or newer**: `cyclonedds` 11.0.1 publishes wheels for CPython 3.10 to 3.13 only. Use one of those for the install host.
- **`pip install topicforge[dds-cyclone]` fails with `CYCLONEDDS_HOME`**: pip is trying to build `cyclonedds` from source because no wheel matches your platform and Python combination. Either switch to a supported Python or install the native CycloneDDS C library first (see Eclipse CycloneDDS releases).
- **`pip install topicforge[dds-fast]` is not found or warns about a missing extra**: the extra was removed in 0.5.3. Build the Fast DDS Python binding from eProsima's sources instead (section 2.b).
- **The server logs "`fastdds` Python binding is not installed" and the DDS tools raise**: `TOPICFORGE_DDS_BACKEND=fast` needs that source-built binding importable in the same environment as TopicForge. Without it the server falls back, as described in section 4.
- **DDS tool returns a sample with `_decode_status="raw"` and an empty `_raw_bytes_hex`**: that is the user-topic placeholder from section 5. It confirms the topic is on the bus; it does not contain data.
- **DDS tool raises "DDS module is not active"**: `TOPICFORGE_DDS_BACKEND` is `mock` (the default) while a live adapter serves. Set `TOPICFORGE_DDS_BACKEND=cyclone` (or `fast`) to enable the DDS half.
- **DDS tool raises "DDS observability only" with a long remediation message**: the inverse case. A DDS-only adapter is active (the `ros2` CLI is missing on PATH) and you called a ROS2 graph or bag tool. Install ROS2 and source the workspace so `ros2` is on PATH; the `CompositeAdapter` then serves both halves on the next run.
- **`auto` selects the wrong backend**: `auto` prefers `fast` over `cyclone` when both bindings are importable. If you want Cyclone explicitly, set `TOPICFORGE_DDS_BACKEND=cyclone` rather than relying on `auto`.
- **Startup fails with `configuration error ... removed in 0.5.3`**: your environment still sets `TOPICFORGE_DDS_BACKEND` to `rti`, `opensplice`, `coredx` or `intercom`. Set it to `cyclone` or `auto`; see section 2.

Report issues at https://github.com/yaniswav/TopicForge/issues.
