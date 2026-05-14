# Migrating from TopicForge v0.2.0 to v0.3.0

Reading time : 5 minutes. v0.3.0 is mostly additive over v0.2.0 — the v0.2.0 Cyclone stub becomes a real adapter, and a parallel Fast DDS adapter ships alongside it. The wire contract widens to admit a new vendor value but the 5 ROS2 graph tools are byte-for-byte unchanged.

---

## Who needs to read this

| Setup | Action |
| ----- | ------ |
| You use TopicForge through Claude Desktop / Claude Code / Cursor / Cline with `pip install topicforge` and no custom code | **Read §1 and §4 only.** Everything else is internal to the server. |
| You import `topicforge` modules in your own Python code | **Read all sections.** The DDS adapters are now real, soft-breaking schema changes affect serialization. |
| You validate MCP responses against a pinned JSON Schema with `additionalProperties: false` | **Read §2 carefully.** `ParticipantInfo.vendor` and `HealthReport.dds_backend` widened. |
| You ran TopicForge v0.2.0 with `TOPICFORGE_DDS_BACKEND=cyclone` and saw the stub-roadmap error | **§5 — the stub is gone**, replaced by real CycloneDDS discovery. The error message disappears. |

---

## 1. New backend and accepted env var values (additive — no migration needed)

v0.3.0 ships a second OSS DDS backend, `fast` (eProsima Fast DDS), alongside the now-real `cyclone` (Eclipse CycloneDDS). Both observe every conformant vendor on the bus via the OMG-DDS-RTPS protocol guarantee — see [`docs/dds-interop-matrix.md`](dds-interop-matrix.md) for the canonical positioning.

New env-var values :

- `TOPICFORGE_DDS_BACKEND=fast` — select the Fast DDS adapter
- `TOPICFORGE_DDS_BACKEND=auto` — now prefers Fast DDS > Cyclone DDS > Mock (was Cyclone > Mock in v0.2.0). See §3 for the backward-compat behavior.

Existing settings are unchanged ; defaults (`TOPICFORGE_DDS_BACKEND=mock`, `TOPICFORGE_DDS_DOMAIN_ID=0`) are honored as before.

---

## 2. Soft-breaking schema changes

### 2.1 `ParticipantInfo.vendor`

The Literal widened to include `"fast"` :

```python
# v0.2.0
vendor: Literal["cyclone", "rti", "mock", "unknown"]

# v0.3.0
vendor: Literal["cyclone", "fast", "rti", "mock", "unknown"]
```

**Producer side** : Python code constructing `ParticipantInfo` directly is unaffected unless you were explicitly type-checking against the v0.2.0 Literal.

**Client side (over MCP)** : additive — clients see a new possible enum value. Strict MCP clients pinned to the v0.2.0 JSON Schema with `additionalProperties:false` or an enum whitelist will reject `"vendor": "fast"` until regenerated.

### 2.2 `HealthReport.dds_backend`

Same shape :

```python
# v0.2.0
dds_backend: Literal["mock", "cyclone", "rti", "none"] = "none"

# v0.3.0
dds_backend: Literal["mock", "cyclone", "fast", "rti", "none"] = "none"
```

Plus a behavioral fix : in v0.2.0, `HealthService.report()` always returned the schema defaults `dds_backend="none"` / `dds_domain_id=None` / `middleware_available=False` regardless of configuration (latent bug). v0.3.0 populates them from `Settings.effective_dds_backend`, `Settings.dds_domain_id`, and `importlib.util.find_spec` on the active backend's Python module.

---

## 3. `auto` resolution order changed

v0.2.0 : `cyclonedds` importable → cyclone ; else → mock.

v0.3.0 : `fastdds` importable → fast ; else `cyclonedds` importable → cyclone ; else → mock.

**Backward compat** : v0.2.0 users with only `cyclonedds` installed see no change — Fast is unimportable on their host so the chain falls through to Cyclone exactly as before. Only users with BOTH SDKs installed see the new Fast-preferred ordering.

The priority reflects the OMG May 2025 interop matrix where Fast DDS is validated against all five other vendors on 47/47 pairs (see [`docs/dds-interop-matrix.md`](dds-interop-matrix.md)).

---

## 4. Pyproject extras refactor

The default install footprint of `pip install topicforge` is **unchanged from v0.2.0**.

DDS-related extras split per-vendor :

```bash
# Single vendor — install only the SDK you need
pip install topicforge[dds-cyclone]   # cyclonedds>=0.10
pip install topicforge[dds-fast]      # fastdds>=2.6.1,<3

# Union — both SDKs (was v0.2.0 [dds] which had cyclonedds only)
pip install topicforge[dds]           # cyclonedds + fastdds
pip install topicforge[all]           # alias of [dds]
```

**v0.2.0 → v0.3.0 install-footprint compat** : `pip install topicforge[dds]` in v0.3.0 now installs `fastdds` in addition to `cyclonedds`. Migrate to `pip install topicforge[dds-cyclone]` to preserve the v0.2.0 footprint exactly.

---

## 5. `CycloneDdsAdapter` stub → real implementation

v0.2.0 shipped `CycloneDdsAdapter` as a protocol-compliant stub : the 3 DDS methods (`list_participants`, `detect_qos_mismatches`, `peek_dds_samples`) raised `AdapterError("CycloneDdsAdapter is a v0.2.0 stub...")` even with `cyclonedds` installed.

v0.3.0 replaces the stub with actual CycloneDDS discovery. With `pip install topicforge[dds-cyclone]` and `TOPICFORGE_DDS_BACKEND=cyclone`, the 3 DDS tools serve real results from the bus.

**Caveat — `peek_dds_samples` v0.3.0 scope** : works on the 4 builtin DCPS topics (`DCPSParticipant`, `DCPSSubscription`, `DCPSPublication`) with full metadata payloads. Arbitrary user topics raise an `AdapterError` pointing at the v0.3.x XTypes/IDL roadmap — IDL discovery for arbitrary types is the next patch.

---

## 6. New tests, new pytest marker

`pytest -m requires_fastdds` runs the Fast-DDS-dependent tests ; auto-skips when `fastdds` is not installed. Mirrors the existing `requires_cyclonedds` marker.

`tests/test_dds_cross_vendor.py` runs 9 parametrized tests against each backend — covers the cross-vendor contract that both adapters produce the same Pydantic shape and the same DDS-only error messages.

---

## 7. Testing the migration

```bash
pip install --upgrade topicforge
TOPICFORGE_MODE=mock python -m topicforge
```

In your MCP client, you should see **8 tools** in `list_tools()`. `health_check` should now report `dds_backend: "mock"`, `dds_domain_id: 0`, `middleware_available: true` (v0.2.0 always reported `"none"`, `null`, `false`).

For a real broker test :

```bash
pip install topicforge[dds-cyclone]      # or [dds-fast] or [dds] for both
TOPICFORGE_MODE=live TOPICFORGE_DDS_BACKEND=cyclone python -m topicforge
```

Then ask Claude to "list DDS participants on the current bus" — you should see real participants discovered on domain 0. Follow `docs/DDS_QUICKSTART.md` for a full walkthrough.

---

## 8. Rollback

```bash
pip install "topicforge==0.2.0"
```

v0.2.0 and v0.3.0 share the same MCP wire surface for the 5 ROS2 tools. On rollback :

- Clients see the same 8 tools (no tool removal).
- `ParticipantInfo.vendor` drops back to the v0.2.0 Literal — `"fast"` participants observed in v0.3.0 will not have a representation under v0.2.0, so they would either fall back to `"unknown"` (in the live adapter) or be absent (the mock fixture's `"fast"` participant is filtered if you rolled the schema back).
- `TOPICFORGE_DDS_BACKEND=fast` is rejected with a `ValueError` ("expected one of (mock, cyclone, rti, auto)").

---

## 9. Full changelog

See `CHANGELOG.md` section `[0.3.0] - 2026-05-14` for the complete list.

Issues or unexpected migration breaks : https://github.com/yaniswav/TopicForge/issues.
