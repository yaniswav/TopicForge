# DDS quickstart — TopicForge v0.2.0+

A 5-minute tour of TopicForge's DDS observability module. Same safety-first read-only contract as the ROS2 tools — the `MiddlewareAdapter` protocol does not expose a write method, so the MCP client can observe the bus but never publish to it.

This guide does **not** assume you have ROS2 installed.

---

## 1. Mock mode — 30-second demo

The deterministic mock fixtures expose the full DDS tool surface without any DDS SDK or middleware. Useful for evaluating the tools before pulling in a real broker.

```bash
pip install topicforge
TOPICFORGE_MODE=mock python -m topicforge
```

In the spawned MCP client (Claude Desktop, Claude Code, Cursor, ...), the 3 DDS tools are now available alongside the 5 ROS2 tools:

- `list_participants(domain_id=0)` → returns 2 mock participants on domain 0 (vendor `cyclone`, hostnames `mock-robot` and `mock-laptop`).
- `detect_qos_mismatches(topic=None)` → returns 1 `MismatchReport` for `/dds/qos_mismatch` (deliberate Reliability incompatibility — RELIABLE reader vs BEST_EFFORT writer).
- `peek_dds_samples(topic="/dds/well_matched", count=3)` → returns 3 deterministic samples ; `peek_dds_samples(topic="/dds/qos_mismatch", count=1)` returns 1 sample with a `qos_note` field annotating the mismatch.

Mock fixtures are stable across runs — you can write integration tests against them.

---

## 2. Live mode with CycloneDDS

Install the DDS extras and select the Cyclone backend:

```bash
pip install topicforge[dds]
TOPICFORGE_MODE=live TOPICFORGE_DDS_BACKEND=cyclone python -m topicforge
```

`TOPICFORGE_DDS_BACKEND` accepts `mock` (default), `cyclone`, `rti` (Pro tier, not shipped in v0.2.0), or `auto` (resolves to `cyclone` when `cyclonedds` is importable, else `mock`).

`TOPICFORGE_DDS_DOMAIN_ID` selects the DDS domain (`0..232`, default `0`).

### v0.2.0 stub limitation

The v0.2.0 `CycloneDdsAdapter` ships as a **protocol-compliant stub**: the lazy import, `is_available()`, and factory routing all work, but the 3 DDS tools raise an `AdapterError` with a v0.2.x roadmap pointer when invoked. The real CycloneDDS discovery (builtin topics, QoS pair extraction, typed reader for samples) lands in a v0.2.x patch.

In the meantime, use `TOPICFORGE_DDS_BACKEND=mock` for end-to-end tool testing.

---

## 3. The QoS mismatch scenario

Mock fixtures encode the canonical "subscriber doesn't receive" debugging case. From an MCP client:

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

An LLM reading this output has enough information to suggest a concrete fix ("the writer is BEST_EFFORT but the reader requires RELIABLE — either relax the reader or upgrade the writer"). That is the diagnostic loop the DDS module is designed to support.

The pure analyzer behind `detect_qos_mismatches` lives in `src/topicforge/adapters/common/qos_analyzer.py` and covers the four MVP policies that explain the bulk of real-world mismatch cases: **Reliability**, **Durability**, **History**, **Deadline**.

---

## 4. Single-adapter limitation (v0.2.0)

TopicForge v0.2.0 selects **one adapter at a time** based on `TOPICFORGE_MODE` + `TOPICFORGE_DDS_BACKEND`:

| `TOPICFORGE_MODE` | `TOPICFORGE_DDS_BACKEND` | Active adapter | ROS2 tools | DDS tools |
| ----------------- | ------------------------ | -------------- | ---------- | --------- |
| `mock`            | (any)                    | `MockAdapter`  | work (fixtures) | work (fixtures) |
| `live` / `auto`   | `mock` (default)         | `Ros2CliAdapter` | work | raise with remediation pointer |
| `live` / `auto`   | `cyclone`                | `CycloneDdsAdapter` (stub) | raise with remediation pointer | raise with v0.2.x roadmap pointer |
| `live` / `auto`   | `rti`                    | falls back to `Ros2CliAdapter` (Pro not shipped in v0.2.0) | work | raise |

A composite adapter that delegates per-tool category (ROS2 graph vs DDS layer) is on the v0.2.x roadmap — see `docs/projet-file/mcp-02-spec.md §7`. For now, restart the server with a different `TOPICFORGE_DDS_BACKEND` to switch sides.

The error message from the unselected side is explicit and points at the remediation path — no silent failures.

---

## 5. What's next

- **v0.2.x** — Replace the `CycloneDdsAdapter` stub with real CycloneDDS discovery (`BuiltinTopicDcpsParticipant` for participants, `BuiltinTopicDcpsSubscription`/`Publication` for QoS pair extraction, typed reader on builtin topics first then arbitrary user topics).
- **v0.2.x** — Composite adapter routing ROS2 tools to `Ros2CliAdapter` and DDS tools to `CycloneDdsAdapter` so both surfaces are usable simultaneously.
- **v0.3.0+** — `RtiConnextAdapter` in the Pro tier (BYO RTI Connext license, gated by `TOPICFORGE_LICENSE_KEY`).
- **v0.3.0+** — Extended QoS mismatch coverage (Liveliness, Ownership, Partition, TimeBasedFilter, LatencyBudget).

Full strategic roadmap lives in `docs/product-plan.md §5` (Phase 1 remaining) and `docs/projet-file/mcp-02-spec.md §7` (DDS module phasing).

---

## 6. Troubleshooting

- **`pip install topicforge[dds]` fails on Windows / macOS Python 3.13+** — `cyclonedds` wheels are typically published for Python 3.8 to 3.12. Pin Python 3.11 or 3.12 for the install host.
- **`pip install topicforge[dds]` fails with `CYCLONEDDS_HOME`** — pip is trying to build `cyclonedds` from source because no wheel matches your platform/Python combination. Either switch to a supported Python (3.11/3.12) or install the native CycloneDDS C library first (see Eclipse CycloneDDS releases).
- **DDS tool returns "v0.2.0 stub" error** — expected with `TOPICFORGE_DDS_BACKEND=cyclone` in v0.2.0. Use `TOPICFORGE_DDS_BACKEND=mock` for end-to-end testing until the v0.2.x patch lands.
- **DDS tool returns "DDS module is not active" error** — your `TOPICFORGE_DDS_BACKEND` resolves to neither `cyclone` nor `mock` (likely you're in `live` mode with the default backend). Set `TOPICFORGE_DDS_BACKEND=cyclone` or `=mock` explicitly.

Report issues at https://github.com/yaniswav/TopicForge/issues.
