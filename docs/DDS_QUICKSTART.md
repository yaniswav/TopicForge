# DDS quickstart — TopicForge v0.4.0+

A 5-minute tour of TopicForge's multi-vendor DDS observability module. Both backends — Eclipse CycloneDDS and eProsima Fast DDS — join the bus as **read-only DDS-RTPS participants** and observe every conformant vendor on the wire via the OMG protocol guarantee. The `MiddlewareAdapter` protocol does not expose a write method, so the MCP client cannot publish back to the bus on any backend.

See [`docs/dds-interop-matrix.md`](dds-interop-matrix.md) for the canonical multi-vendor positioning and the [OMG May 2025 interop reference](projet-file/references/omg-dds-interop-2025-05-08.xlsx).

This guide does **not** assume you have ROS2 installed.

---

## 1. Mock mode — 30-second demo

The deterministic mock fixtures expose the full DDS tool surface without any DDS SDK or middleware. Useful for evaluating the tools before pulling in a real broker.

```bash
pip install topicforge
TOPICFORGE_MODE=mock python -m topicforge
```

In the spawned MCP client (Claude Desktop, Claude Code, Cursor, ...), the 3 DDS tools are now available alongside the 5 ROS2 tools:

- `list_participants(domain_id=0)` → returns 3 mock participants on domain 0 — two CycloneDDS (`mock-robot`, `mock-laptop`) and one Fast DDS (`mock-aerospace-node`). The multi-vendor mock exercises the canonical vendor enum (`cyclone`, `fast`, `rti`, `mock`, `unknown`).
- `detect_qos_mismatches(topic=None)` → returns 1 `MismatchReport` for `/dds/qos_mismatch` (deliberate Reliability incompatibility — RELIABLE reader vs BEST_EFFORT writer).
- `peek_dds_samples(topic="/dds/well_matched", count=3)` → returns 3 deterministic samples ; `peek_dds_samples(topic="/dds/qos_mismatch", count=1)` returns 1 sample with a `qos_note` field annotating the mismatch.

Mock fixtures are stable across runs — you can write integration tests against them.

---

## 2. Live mode — choose your backend

v0.3.0 ships two OSS Python adapters. Pick one (or install both) :

### 2.a Eclipse CycloneDDS

```bash
pip install topicforge[dds-cyclone]
TOPICFORGE_MODE=live TOPICFORGE_DDS_BACKEND=cyclone python -m topicforge
```

The CycloneDDS adapter uses `cyclonedds.builtin.BuiltinDataReader` for polling-style discovery on the DCPS builtin topics. QoS policies are introspected via `Policy.*` class names. Bounded `take_iter` timeouts keep tool calls under 2 seconds.

### 2.b eProsima Fast DDS

```bash
pip install topicforge[dds-fast]
TOPICFORGE_MODE=live TOPICFORGE_DDS_BACKEND=fast python -m topicforge
```

The Fast DDS adapter attaches a `DomainParticipantListener`-shaped object to a freshly created participant and accumulates discovery callbacks under an RLock. A bounded `discovery_wait_ms=1500` warm-up after participant creation gives the listener time to populate before the first tool call.

### 2.c Both backends + auto resolution

```bash
pip install topicforge[dds]                                # both Cyclone and Fast
TOPICFORGE_MODE=live TOPICFORGE_DDS_BACKEND=auto python -m topicforge
```

`auto` resolves to the first available OSS backend in this order: `fast` > `cyclone` > `mock`. The order reflects the OMG May 2025 interop matrix where Fast DDS is validated against all five other vendors on 47/47 pairs. v0.2.0 users with only `cyclonedds` installed remain unchanged — Fast is unimportable on their host so the chain falls through to Cyclone.

### Domain selection

```bash
TOPICFORGE_DDS_DOMAIN_ID=42 python -m topicforge
```

Accepts `0..232` (DDS spec range). Default is `0` — the same default used by most ROS2 setups.

### Pro tier — RTI Connext

`TOPICFORGE_DDS_BACKEND=rti` is reserved for the v0.4.0+ Pro tier (BYO RTI Connext license). Selecting it in the OSS core falls back to the ROS2 CLI adapter with a logged warning.

---

## 3. The QoS mismatch scenario

Both real backends and the mock fixtures encode the canonical "subscriber doesn't receive" debugging case. From an MCP client:

```
> Detect QoS mismatches on the current bus.

[tool call: detect_qos_mismatches]
[result]
[
  {
    "topic": "/dds/qos_mismatch",
    "reader_guid": "010f1c2a.3b4c5d6e.7f800000.00000001",
    "writer_guid": "010f1c2a.3b4c5d6e.7f800000.00000002",
    "incompatible_policies": ["Reliability"],
    "severity": "incompatible",
    "mode_effective": "mock"
  }
]
```

An LLM reading this output has enough information to suggest a concrete fix ("the writer is BEST_EFFORT but the reader requires RELIABLE — either relax the reader or upgrade the writer"). That is the diagnostic loop the DDS module is designed to support — and it works identically regardless of which backend produced the discovery samples, because the vendor-neutral pure analyzer at `src/topicforge/adapters/common/qos_analyzer.py` operates on canonical `QosProfile` Pydantic models.

The analyzer covers the four MVP policies — **Reliability**, **Durability**, **History**, **Deadline** — that explain the bulk of real-world mismatch cases. Liveliness, Ownership, Partition, TimeBasedFilter, and LatencyBudget are v0.5.x patches.

---

## 4. Composite adapter (v0.4.0 Phase 1+)

v0.4.0 Phase 1 lifted the v0.3.0 single-adapter limitation. When `TOPICFORGE_MODE=live` is paired with a DDS backend, TopicForge instantiates **both** a `Ros2CliAdapter` and the chosen DDS adapter behind a `CompositeAdapter` and routes per-tool category — the 5 ROS2 graph tools hit the CLI, the 6 DDS / observability tools (`list_participants`, `detect_qos_mismatches`, `peek_dds_samples`, `participant_events`, `topic_metrics`, `peek_bag_samples`) hit the DDS half. The `name` collapses to `"ros2_cli+cyclone"` or `"ros2_cli+fast"` ; `effective_mode` reports `"live"` whenever either half is live.

| `TOPICFORGE_MODE` | `TOPICFORGE_DDS_BACKEND` | Active adapter                | ROS2 tools                       | DDS / observability tools           |
| ----------------- | ------------------------ | ----------------------------- | -------------------------------- | ----------------------------------- |
| `mock`            | (any)                    | `MockAdapter`                 | work (fixtures)                  | work (fixtures)                     |
| `live` / `auto`   | `mock` (default)         | `Ros2CliAdapter`              | work                             | raise with remediation              |
| `live` / `auto`   | `cyclone`                | `CompositeAdapter(ros2_cli + cyclone)` | work (CLI)              | work (real CycloneDDS)              |
| `live` / `auto`   | `fast`                   | `CompositeAdapter(ros2_cli + fast)` | work (CLI)                 | work (real Fast DDS)                |
| `live` / `auto`   | `rti`                    | falls back to `Ros2CliAdapter` | work (CLI)                      | raise (v0.4.0+ Pro tier — BYO license) |

**Graceful degradation paths preserved.** DDS binding missing → ROS2-CLI-only (the v0.3.0 behavior). ROS2 CLI missing on PATH → DDS-only adapter with a clear `DDS_ONLY_ERROR_MSG` on the 5 ROS2 methods. Neither available → MockAdapter (auto mode only).

`HealthReport` reports both halves via the `ros_backend` and `dds_backend` fields, so a downstream client can introspect which half of a composite is live without guessing.

---

## 5. v0.4.0 scope of `peek_dds_samples`

`peek_dds_samples` is full-fidelity on the 4 builtin DCPS topics with both backends :

```
peek_dds_samples(topic="DCPSParticipant", count=5)
peek_dds_samples(topic="DCPSSubscription", count=10)
peek_dds_samples(topic="DCPSPublication", count=10)
```

**Arbitrary user topics (v0.4.0 Phase 1.5+).** The v0.3.0 `AdapterError` is retired. The tool now returns best-effort decoded samples annotated with a `_decode_status` field :

- `"full"` — every IDL field decoded (currently the Cyclone XTypes path, structurally in place ; real-bus validation pending user feedback)
- `"partial"` — some fields decoded, others opaque (mixed-success path)
- `"raw"` — the binding could not resolve the dynamic XTypes ; the serialized payload is preserved as hex in `_raw_bytes_hex` (capped at 4096 hex chars ; `_raw_bytes_truncated=True` flags clipping)

The diagnostic key `_decode_note` carries a short explanation when the status is non-`full`. The wire shape is identical across Cyclone and Fast DDS — the analyzer doesn't need to know which backend produced the sample.

Fast DDS 2.6.x exposes only a partial dynamic XTypes Python surface today, so the `"raw"` fallback is the common path on Fast DDS user topics — the structural plumbing is identical to Cyclone, the upstream binding completion is the gating factor.

The other DDS tools — `list_participants`, `detect_qos_mismatches`, `participant_events`, `topic_metrics` — work end-to-end on any user-topic deployment ; they don't depend on payload deserialization.

---

## 6. What's next

- **v0.5.x patch** — Fast DDS XTypes binding completion to lift `"raw"` → `"full"` for arbitrary user-topic peek on Fast.
- **v0.5.x patch** — Extended QoS coverage : Liveliness, Ownership, Partition, TimeBasedFilter, LatencyBudget.
- **v0.5.x patch** — Real-bus validation of the Cyclone XTypes pipeline (the v0.4.0 Phase 1.5 structural pipeline awaits user feedback on real domains).
- **v0.4.0+ Pro tier** — Real `RtiConnextAdapter` (BYO RTI Connext license, gated by `TOPICFORGE_LICENSE_KEY`). Scaffolded but not yet shipped.

Full strategic roadmap lives in [`docs/product-plan.md`](product-plan.md) and the DDS module spec at [`docs/projet-file/mcp-02-spec.md`](projet-file/mcp-02-spec.md).

---

## 7. Troubleshooting

- **`pip install topicforge[dds-cyclone]` fails on Windows / macOS Python 3.13+** — `cyclonedds` wheels are typically published for Python 3.8 to 3.12. Pin Python 3.11 or 3.12 for the install host.
- **`pip install topicforge[dds-cyclone]` fails with `CYCLONEDDS_HOME`** — pip is trying to build `cyclonedds` from source because no wheel matches your platform/Python combination. Either switch to a supported Python (3.11/3.12) or install the native CycloneDDS C library first (see Eclipse CycloneDDS releases).
- **`pip install topicforge[dds-fast]` fails** — eProsima Fast DDS Python bindings (`fastdds>=2.6.1,<3`) currently ship wheels for Linux first. Windows wheels lag ; consult fast-dds.docs.eprosima.com for the current matrix.
- **DDS tool returns samples with `_decode_status="raw"`** — the binding could not resolve the dynamic XTypes for this user topic. Inspect `_decode_note` for the cause and `_raw_bytes_hex` for the serialized payload. On Fast DDS this is the common path until 2.6.x dynamic XTypes binding completion. On Cyclone, ensure the publisher uses XTypes-discoverable types and re-run.
- **DDS tool returns "DDS module is not active" error** — your `TOPICFORGE_DDS_BACKEND` is `mock` while `TOPICFORGE_MODE` is `live` (the ROS2 CLI half of the composite is the only one selected). Set `TOPICFORGE_DDS_BACKEND=cyclone` or `=fast` explicitly to enable the DDS half — the `CompositeAdapter` will then serve both surfaces.
- **DDS tool returns "DDS observability only" with a long remediation message** — the inverse case: a DDS-only adapter is active (the ROS2 CLI is missing on PATH) and you called a ROS2 graph tool. Install ROS2 and source the workspace so `ros2` is on PATH ; the `CompositeAdapter` will pick up both halves on the next run.
- **`auto` selects the wrong backend** — `auto` prefers Fast > Cyclone > Mock. If you want Cyclone explicitly, set `TOPICFORGE_DDS_BACKEND=cyclone` rather than relying on `auto`.

Report issues at https://github.com/yaniswav/TopicForge/issues.
