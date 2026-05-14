# DDS quickstart — TopicForge v0.3.0+

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

The analyzer covers the four MVP policies — **Reliability**, **Durability**, **History**, **Deadline** — that explain the bulk of real-world mismatch cases. Liveliness, Ownership, Partition, TimeBasedFilter, and LatencyBudget are v0.3.x patches.

---

## 4. Single-adapter limitation (v0.3.0)

TopicForge v0.3.0 still selects **one adapter at a time** based on `TOPICFORGE_MODE` + `TOPICFORGE_DDS_BACKEND` :

| `TOPICFORGE_MODE` | `TOPICFORGE_DDS_BACKEND` | Active adapter             | ROS2 tools                | DDS tools                  |
| ----------------- | ------------------------ | -------------------------- | ------------------------- | -------------------------- |
| `mock`            | (any)                    | `MockAdapter`              | work (fixtures)           | work (fixtures)            |
| `live` / `auto`   | `mock` (default)         | `Ros2CliAdapter`           | work                      | raise with remediation     |
| `live` / `auto`   | `cyclone`                | `CycloneDdsAdapter`        | raise (DDS-only adapter)  | work (real CycloneDDS)     |
| `live` / `auto`   | `fast`                   | `FastDdsAdapter`           | raise (DDS-only adapter)  | work (real Fast DDS)       |
| `live` / `auto`   | `rti`                    | falls back to ROS2 CLI     | work (CLI)                | raise (v0.4.0+ Pro tier)   |

A composite adapter that delegates per-tool category (ROS2 graph vs DDS layer) is on the v0.3.x roadmap. For now, restart the server with a different `TOPICFORGE_DDS_BACKEND` to switch sides.

Error messages on the unselected side are explicit and point at the remediation path — no silent failures.

---

## 5. v0.3.0 scope of `peek_dds_samples`

`peek_dds_samples` is full-fidelity on the 4 builtin DCPS topics with both backends :

```
peek_dds_samples(topic="DCPSParticipant", count=5)
peek_dds_samples(topic="DCPSSubscription", count=10)
peek_dds_samples(topic="DCPSPublication", count=10)
```

Arbitrary user topics raise an `AdapterError` pointing at the v0.3.x roadmap — XTypes/IDL discovery (`cyclonedds.dynamic.get_types_for_typeid` on Cyclone, XTypes remote-type lookup on Fast DDS) is the missing piece for arbitrary user-topic peek.

The other two DDS tools — `list_participants` and `detect_qos_mismatches` — work end-to-end on any user-topic deployment ; they don't depend on payload deserialization.

---

## 6. What's next

- **v0.3.x patch** — XTypes/IDL discovery to extend `peek_dds_samples` to arbitrary user topics on both backends.
- **v0.3.x patch** — Extended QoS coverage : Liveliness, Ownership, Partition, TimeBasedFilter, LatencyBudget.
- **v0.3.x patch** — Composite adapter routing ROS2 graph tools to `Ros2CliAdapter` and DDS tools to the selected DDS adapter, so both surfaces are usable simultaneously.
- **v0.4.0+** — `RtiConnextAdapter` in the Pro tier (BYO RTI Connext license, gated by `TOPICFORGE_LICENSE_KEY`).

Full strategic roadmap lives in [`docs/product-plan.md`](product-plan.md) and the DDS module spec at [`docs/projet-file/mcp-02-spec.md`](projet-file/mcp-02-spec.md).

---

## 7. Troubleshooting

- **`pip install topicforge[dds-cyclone]` fails on Windows / macOS Python 3.13+** — `cyclonedds` wheels are typically published for Python 3.8 to 3.12. Pin Python 3.11 or 3.12 for the install host.
- **`pip install topicforge[dds-cyclone]` fails with `CYCLONEDDS_HOME`** — pip is trying to build `cyclonedds` from source because no wheel matches your platform/Python combination. Either switch to a supported Python (3.11/3.12) or install the native CycloneDDS C library first (see Eclipse CycloneDDS releases).
- **`pip install topicforge[dds-fast]` fails** — eProsima Fast DDS Python bindings (`fastdds>=2.6.1,<3`) currently ship wheels for Linux first. Windows wheels lag ; consult fast-dds.docs.eprosima.com for the current matrix.
- **DDS tool returns "v0.3.x roadmap" error** — you called `peek_dds_samples` on an arbitrary user topic. The 4 builtin DCPS topics work today ; arbitrary user-topic peek is a v0.3.x patch (XTypes/IDL discovery).
- **DDS tool returns "DDS module is not active" error** — your `TOPICFORGE_DDS_BACKEND` is `mock` while `TOPICFORGE_MODE` is `live` (the ROS2 CLI adapter is selected). Set `TOPICFORGE_DDS_BACKEND=cyclone` or `=fast` explicitly to enable the DDS adapters.
- **`auto` selects the wrong backend** — `auto` prefers Fast > Cyclone > Mock. If you want Cyclone explicitly, set `TOPICFORGE_DDS_BACKEND=cyclone` rather than relying on `auto`.

Report issues at https://github.com/yaniswav/TopicForge/issues.
