# Migrating from TopicForge v0.3.0 to v0.4.0

Reading time : 8 minutes. v0.4.0 is the **observability + bag-analysis maturation** release. The tool surface grows from 8 to 11 ; the v0.3.0 single-adapter limitation is lifted by `CompositeAdapter` ; the DDS auto-detect chain widens from 3 to 8 vendor candidates ; `peek_dds_samples` no longer raises on arbitrary user topics. Every v0.3.0 producer keeps working — schema changes are additive optional, env vars are widened not replaced.

---

## Who needs to read this

| Setup | Action |
| ----- | ------ |
| You use TopicForge through Claude Desktop / Claude Code / Cursor / Cline with `pip install topicforge` and no custom code | **Read §1 and §2 only.** Everything else is producer-side. |
| You import `topicforge` modules in your own Python code | **Read all sections.** Three new MCP tools, four `ParticipantInfo` lifecycle fields, four `BagAnalysis` enrichment fields, and a new `MiddlewareAdapter` method group. |
| You validate MCP responses against a pinned JSON Schema with `additionalProperties: false` | **Read §3 carefully.** `ParticipantInfo`, `BagAnalysis`, `HealthReport`, `AdapterName`, `DdsBackend` all widened. |
| You ran TopicForge v0.3.0 with `TOPICFORGE_MODE=live` + `TOPICFORGE_DDS_BACKEND=cyclone` and got DDS-only-mode errors on the 5 ROS2 tools | **§4 — the CompositeAdapter now serves both surfaces simultaneously.** Source ROS2 and re-run ; the errors disappear. |
| You ran TopicForge v0.3.0 against arbitrary user topics with `peek_dds_samples` and got the "v0.3.x roadmap" `AdapterError` | **§5 — the error is gone.** The tool now returns best-effort decoded samples with a `_decode_status` annotation. |

---

## 1. Three new MCP tools (additive)

v0.4.0 explicitly breaks the documented 8-tool ceiling three times — each break is acknowledged in `docs/projet-file/mcp-02-spec.md §2` and in the CHANGELOG `[0.4.0]` section.

- **`participant_events(domain_id, lookback_seconds)`** (Phase 1) — DDS participant `discovered` / `lost` events over a configurable window. Default lookback 300 s, range 1..86400, hard cap 200 events newest-first. Backed by the new `LifecycleBuffer` shared between Cyclone (polling reconciliation) and Fast DDS (listener callbacks).
- **`topic_metrics(topic, window_seconds, domain_id)`** (Phase 2) — temporal metrics : observed frequency, sequence gaps, latency p50/p95/p99 over a sliding window. Default window 60 s, range 1..3600. Buffer cap `MAX_SAMPLES_PER_TOPIC=1000`, drop-oldest. Opportunistic fill : the metrics buffer accumulates ONLY as `peek_dds_samples` is exercised (Cyclone + Fast 2.6.x Python bindings do not expose at-sample-receive callbacks).
- **`peek_bag_samples(path, topic, count)`** (Phase 3) — post-mortem inspection : decoded samples from a recorded `.mcap` / `.db3` / `.bag` file. Distinct from `peek_dds_samples` (live bus) and `sample_messages` (ROS2 graph live peek). Same `SampleResult` envelope as the other two so an LLM caller reads one schema. Requires `pip install topicforge[bags]` (rosbags Apache 2.0 pure-Python library).

If you maintain a local tool allowlist for the MCP client, add these three names. If you pin against the `tests/test_tools_integration.py::MVP_TOOLS` set, it grew from 8 to 11.

---

## 2. New / changed environment variables and extras

### 2.1 `TOPICFORGE_DDS_BACKEND` accepts 5 new vendor values

The v0.4.0 Phase 1.5 auto-detect chain widens the Literal :

```
v0.3.0 accepted : mock | cyclone | fast | rti | auto
v0.4.0 accepted : mock | cyclone | fast | rti | opensplice | coredx | intercom | opendds | dust | auto
```

The 5 new values target the OMG vendor space :

- `opendds` and `dust` ship as **OSS stubs** — `is_available()` returns False because the upstream Python bindings (`pyopendds`, `dust-dds-python`) are not on PyPI yet. The extras `[dds-opendds]` and `[dds-dust]` are placeholder pins anchoring the auto-detect probe for the day the upstream packages ship. `pip install topicforge[dds-opendds]` today produces a clean install failure.
- `opensplice`, `coredx`, `intercom` are **Pro tier targets** — probed against `topicforge_pro.adapters.<vendor>` rather than the upstream SDK. The OSS core never imports a commercial vendor binding.

### 2.2 `auto` resolution chain widened

```
v0.3.0 : Fast > Cyclone > Mock
v0.4.0 : RTI > OpenSplice > CoreDX > InterCOM (Pro tier, if installed)
         > OpenDDS > Fast > Cyclone > Dust > Mock (OSS)
```

**Backward compat** : Pro tier candidates are only considered when the `topicforge_pro` package is importable on the host. v0.3.0 users without Pro see the chain collapse to `OpenDDS > Fast > Cyclone > Dust > Mock`. Since `opendds` and `dust` are stubs that report `is_available()=False`, the practical effective order remains `Fast > Cyclone > Mock` — unchanged from v0.3.0 unless you've explicitly added a Pro tier package.

### 2.3 New pyproject extras

- `[bags]` — `rosbags>=0.9` (Phase 3, optional bag analysis). **Not** bundled in `[all]` to keep the default footprint small.
- `[dds-opendds]` — `pyopendds>=0.1` placeholder pin (Phase 1.5).
- `[dds-dust]` — `dust-dds-python>=0.1` placeholder pin (Phase 1.5).
- `[dds-all-oss]` — `topicforge[dds] + dds-opendds + dds-dust` for users opting into the stubs.

### 2.4 New pytest markers

- `integration` — real-bus DDS scenarios. Gated out of the default invocation ; run with `pytest -m integration` or the labeled CI workflow.
- `requires_opendds` — auto-skip without the binding (same convention as `requires_cyclonedds`).
- `requires_dust` — same shape.
- `requires_rosbags` — Phase 3 bag tests.

---

## 3. Soft-breaking schema changes

All changes are **additive optional with safe defaults** ; every v0.3.0 producer keeps working. Strict MCP clients pinned to v0.3.0 JSON Schemas with `additionalProperties: false` need to regenerate, like at every minor.

### 3.1 `ParticipantInfo` — 4 lifecycle fields

```python
# v0.3.0
class ParticipantInfo(BaseModel):
    guid: str
    vendor: Literal["cyclone", "fast", "rti", "mock", "unknown"]
    hostname: str | None = None
    domain_id: int
    mode_effective: Literal["mock", "live"]

# v0.4.0
class ParticipantInfo(BaseModel):
    guid: str
    vendor: Literal["cyclone", "fast", "rti", "mock", "unknown"]
    hostname: str | None = None
    domain_id: int
    mode_effective: Literal["mock", "live"]
    first_seen_ns: int | None = None         # NEW (Phase 1)
    last_seen_ns: int | None = None          # NEW (Phase 1)
    status: Literal["active", "left", "unknown"] = "unknown"  # NEW (Phase 1)
    seen_count: int = 0                       # NEW (Phase 1)
```

Backed by `LifecycleBuffer` on Cyclone (polling reconciliation) and Fast DDS (listener-callback native, including `lost` events).

### 3.2 `BagAnalysis` — 4 enrichment fields

```python
# v0.4.0
class BagAnalysis(BaseModel):
    # v0.3.0 fields unchanged
    bag_format: Literal["mcap", "db3", "bag", "unknown"] | None = None  # NEW (Phase 3)
    samples_decoded_count: int = 0                                       # NEW (Phase 3)
    recording_duration_ns: int | None = None                             # NEW (Phase 3)
    participants_recorded: list[ParticipantInfo] = []                    # NEW (Phase 3)
```

`analyze_bag` retains the v0.3.0 `ros2 bag info` text-parse fallback on `Ros2CliAdapter` when rosbags is absent ; the enriched fields populate at their safe defaults in that path.

### 3.3 `HealthReport.ros_backend` — new field

```python
# v0.4.0
ros_backend: Literal["mock", "ros2_cli", "none"] = "none"  # NEW (Phase 1)
```

Symmetric to the existing `dds_backend`. Lets clients distinguish the ROS and DDS halves of a `CompositeAdapter` without reading the `name` tag.

### 3.4 `HealthReport.dds_backend` Literal widened

```
v0.3.0 : "mock" | "cyclone" | "fast" | "rti" | "none"
v0.4.0 : "mock" | "cyclone" | "fast" | "rti" | "opensplice" | "coredx" | "intercom" | "opendds" | "dust" | "none"
```

### 3.5 `AdapterName` Literal widened

Internal type (no MCP-wire impact), but listed for code-level type-checkers. Now includes 5 new vendor tags (`opensplice`, `coredx`, `intercom`, `opendds`, `dust`) and 7 composite tags (`ros2_cli+cyclone`, `ros2_cli+fast`, `ros2_cli+rti`, `ros2_cli+opensplice`, `ros2_cli+coredx`, `ros2_cli+intercom`, `ros2_cli+opendds`).

### 3.6 `TopicMetrics` schema (new — Phase 2)

```python
class TopicMetrics(BaseModel):
    topic: str
    window_seconds: int
    samples_observed: int
    frequency_hz_observed: float | None
    frequency_hz_declared: float | None  # from QoS Deadline
    sequence_gaps_count: int | None
    latency_ns_p50: int | None
    latency_ns_p95: int | None
    latency_ns_p99: int | None
    # ... + boolean availability flags per conditional metric
    mode_effective: Literal["mock", "live"]
```

Frozen, `extra="forbid"`. The None / 0 semantics surface partial-data scenarios cleanly to LLM callers.

### 3.7 `ParticipantEvent` schema (new — Phase 1)

```python
class ParticipantEvent(BaseModel):
    timestamp_ns: int
    event_type: Literal["discovered", "lost"]
    participant: ParticipantInfo
    mode_effective: Literal["mock", "live"]
```

---

## 4. `CompositeAdapter` — the single-adapter limitation is lifted

v0.3.0 selected one adapter at a time : `Ros2CliAdapter` OR a DDS adapter. The 5 ROS2 tools worked on the former and raised `AdapterError(DDS_ONLY_ERROR_MSG)` on the latter ; the 3 DDS tools worked on the latter and raised the inverse error on the former.

v0.4.0 Phase 1 ships `CompositeAdapter` (`adapters/composite.py`). When `TOPICFORGE_MODE=live` is paired with a DDS backend, the factory tries to build **both** halves and wraps them in a composite that routes per-tool category :

- The 5 ROS2 graph tools (`list_topics`, `get_topic_info`, `sample_messages`, `analyze_bag`, `peek_bag_samples`) hit `Ros2CliAdapter`.
- The 6 DDS / observability tools (`list_participants`, `detect_qos_mismatches`, `peek_dds_samples`, `participant_events`, `topic_metrics`) hit the selected DDS adapter.

The composite's `name` collapses to `"ros2_cli+cyclone"` or `"ros2_cli+fast"`. `effective_mode` reports `"live"` whenever either half is live.

**Graceful degradation paths preserved** :

- DDS binding missing → `Ros2CliAdapter` alone (the v0.3.0 fallback).
- ROS2 CLI missing on PATH → DDS-only adapter with the polished `DDS_ONLY_ERROR_MSG` on the 5 ROS2 methods. The v0.5.0 message lists the affected tools and points at the `CompositeAdapter` remediation.
- Neither available → `MockAdapter` (auto mode only).

If your deployment relied on the v0.3.0 error to detect "DDS adapter is selected", switch to inspecting `HealthReport.dds_backend` and `ros_backend` instead — both are populated correctly when a composite is live.

---

## 5. `peek_dds_samples` on user topics — no more `AdapterError`

v0.3.0 raised `AdapterError("v0.3.x roadmap — XTypes/IDL discovery missing")` for any non-builtin topic. v0.4.0 Phase 1 returns best-effort decoded samples with three reserved annotation keys :

- `_decode_status` : `"full"` / `"partial"` / `"raw"`
- `_decode_note` : short diagnostic when the status is non-`full`
- `_raw_bytes_hex` : hex-encoded serialized payload preview when `_decode_status="raw"` (capped at 4096 hex chars ; `_raw_bytes_truncated=True` flags clipping)

The wire shape is identical across Cyclone and Fast DDS. Phase 1.5 added the Cyclone XTypes pipeline ; Fast DDS 2.6.x bindings still ship a partial dynamic XTypes Python surface so the `"raw"` fallback is the common path on Fast user topics — the structural plumbing is identical, the upstream binding completion is the gating factor.

If your code parsed the v0.3.0 `AdapterError` text to detect this case, replace the `try/except` with a `samples[i].payload["_decode_status"]` check.

---

## 6. `MiddlewareAdapter` protocol expansions

Three new methods on the protocol :

```python
def participant_events(
    self, domain_id: int, lookback_seconds: int
) -> list[ParticipantEvent]: ...

def topic_metrics(
    self, topic: str, window_seconds: int, domain_id: int
) -> TopicMetrics: ...

def peek_bag_samples(
    self, path: str, topic: str, count: int
) -> SampleResult: ...
```

All existing adapters implement them. Mock returns deterministic fixtures. `Ros2CliAdapter.peek_bag_samples` delegates to `BagService`. DDS-only adapters (`Cyclone`, `Fast`, `OpenDDS`, `Dust`) raise their existing `DDS_ONLY_ERROR_MSG` on `peek_bag_samples`. `Ros2CliAdapter`, `OpenDDS`, `Dust` raise their existing roadmap errors on `participant_events` and `topic_metrics`.

If you implement a custom adapter (third-party `MiddlewareAdapter` shim, integration scaffold), add the three methods or your adapter will fail protocol conformance at type-check time.

---

## 7. No code change to the 5 ROS2 tools

`health_check`, `list_topics`, `get_topic_info`, `sample_messages`, `analyze_bag` behave identically to v0.3.0 — except `analyze_bag` now populates the new `BagAnalysis` enrichment fields when rosbags is installed (and leaves them at safe defaults otherwise). The `mode_effective` wire contract is unchanged.

---

## 8. Quick checklist

- [ ] Verified the eleven-tool set is allowlisted in your MCP client config (if you allowlist).
- [ ] Regenerated any pinned JSON Schemas for `ParticipantInfo`, `BagAnalysis`, `HealthReport`.
- [ ] Removed any v0.3.x `try/except AdapterError` around `peek_dds_samples` on user topics — replace with `_decode_status` checks.
- [ ] Confirmed `pip install topicforge[bags]` is added wherever `peek_bag_samples` is exercised.
- [ ] If you implemented a custom `MiddlewareAdapter`, added `participant_events`, `topic_metrics`, `peek_bag_samples`.
- [ ] Read the polished `DDS_ONLY_ERROR_MSG` once — its wording changed in v0.5.0 polish.

Questions or migration friction : open an issue at https://github.com/yaniswav/TopicForge/issues.
