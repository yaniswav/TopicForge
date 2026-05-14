# Migrating from TopicForge v0.1.x to v0.2.0

Reading time : 5 minutes. v0.2.0 is mostly additive over v0.1.2, but two surfaces are soft-breaking and worth a once-over before you upgrade.

---

## Who needs to read this

| Setup | Action |
| ----- | ------ |
| You use TopicForge through Claude Desktop / Claude Code / Cursor / Cline with `pip install topicforge` and no custom code | **Read §1 and §4 only.** Everything else is internal to the server. |
| You import `topicforge` modules in your own Python code | **Read all sections.** Two soft-breaking changes affect imports and schemas. |
| You validate MCP responses against a pinned JSON Schema with `additionalProperties: false` | **Read §2 carefully.** v0.2.0 adds optional fields to `TopicInfo` and `HealthReport`. |

---

## 1. New tools and env vars (additive — no migration needed)

v0.2.0 ships 3 new MCP tools alongside the existing 5 ROS2 tools :

- `list_participants(domain_id)` → `list[ParticipantInfo]`
- `detect_qos_mismatches(topic)` → `list[MismatchReport]`
- `peek_dds_samples(topic, count)` → `SampleResult`

Existing clients see the new tools the next time they query `list_tools()` ; no client code change required to ignore them.

Two new env vars — both optional, safe defaults :

- `TOPICFORGE_DDS_BACKEND` — `mock | cyclone | rti | auto`, default `mock`. DDS module is opt-in.
- `TOPICFORGE_DDS_DOMAIN_ID` — `0..232`, default `0`.

If you don't set them, v0.2.0 behaves exactly like v0.1.2 from the client's perspective, plus three new tools that raise an `AdapterError` with a remediation pointer when invoked.

---

## 2. Soft-breaking schema changes

### 2.1 `TopicInfo` schema

Three additive optional fields were added — all default `None` :

- `reader_count: int | None`
- `writer_count: int | None`
- `qos_profile: QosProfile | None`

**Producer side** : Python code constructing `TopicInfo` directly is unaffected. The defaults compile and v0.1.x constructor calls still work.

**Client side (over MCP)** : additive on the wire — an MCP client consuming JSON sees three extra optional keys per `TopicInfo` and is unaffected unless it strictly validates against the v0.1.x schema with `additionalProperties: false`. **Strict MCP clients that validated v0.1.x responses against the `TopicInfo` schema with `additionalProperties: false` will reject v0.2.0 responses unless their schema is regenerated. Standard MCP clients that read tool descriptions dynamically are unaffected.**

### 2.2 `HealthReport` schema

Same shape : three additive optional fields with safe defaults :

- `dds_backend: Literal["mock", "cyclone", "rti", "none"] = "none"`
- `dds_domain_id: int | None = None`
- `middleware_available: bool = False`

Same remediation as `TopicInfo` — regenerate strict schemas pinned to v0.1.x.

---

## 3. Renamed protocol (backward-compat alias kept)

The `RosAdapter` protocol in `topicforge.adapters.base` has been generalized into `MiddlewareAdapter` — a superset covering both ROS2 graph methods and the new DDS methods under one contract.

```python
# v0.1.x
from topicforge.adapters.base import RosAdapter

# v0.2.0 — both still work
from topicforge.adapters.base import RosAdapter          # backward-compat alias
from topicforge.adapters.base import MiddlewareAdapter   # canonical name
```

`RosAdapter` is preserved as a type alias `RosAdapter = MiddlewareAdapter`, so any import of `RosAdapter` keeps type-checking. Internal `Ros2CliAdapter.name` value moved from `"live"` to `"ros2_cli"` — an internal tag, not the MCP-wire `mode_effective` field. The wire-facing `mode_effective: Literal["mock", "live"]` is unchanged.

Two new types live alongside :

- `AdapterName = Literal["mock", "ros2_cli", "cyclone", "rti"]` — internal tag, widened to support DDS.
- `EffectiveMode = Literal["mock", "live"]` — extracted, wire-facing.

---

## 4. Optional install for DDS

The default install footprint of `pip install topicforge` is **unchanged from v0.1.2**. No new dependency.

To unlock the DDS module on a real broker, opt into the extras :

```bash
pip install topicforge[dds]
```

This pulls `cyclonedds>=0.10` (BSD-licensed Python bindings, ~20 MB on install). Mock and ROS2-only installs are unaffected.

`pip install topicforge[all]` is an alias of `[dds]` for now ; future extras will join it.

---

## 5. v0.2.0 MVP limitation

TopicForge v0.2.0 runs **one adapter at a time** — selected by `TOPICFORGE_MODE` plus `TOPICFORGE_DDS_BACKEND`. With `TOPICFORGE_DDS_BACKEND=cyclone`, the 3 DDS tools work and the 5 ROS2 tools raise `AdapterError` with a clear remediation pointer. Reverse holds for the default `TOPICFORGE_DDS_BACKEND=mock`.

A composite adapter that delegates per-tool category is a v0.2.x roadmap item. The full matrix lives in `docs/DDS_QUICKSTART.md §4`.

For end-to-end testing of all 8 tools simultaneously, use `TOPICFORGE_MODE=mock` — the mock adapter serves both ROS2 and DDS surfaces from deterministic fixtures.

---

## 6. Testing the migration

After `pip install --upgrade topicforge`, sanity-check :

```bash
TOPICFORGE_MODE=mock python -m topicforge
```

In your MCP client, you should see **8 tools** in `list_tools()`. If you only see 5, the upgrade didn't take effect ; verify with `python -c "import topicforge; print(topicforge.__version__)"` — should be `0.2.0`.

For a real broker test, follow `docs/DDS_QUICKSTART.md §2`.

---

## 7. Rollback

```bash
pip install "topicforge==0.1.2"
```

v0.1.x and v0.2.0 share the same MCP wire surface for the 5 ROS2 tools, so a rollback is safe — clients see 5 tools instead of 8, the additive `TopicInfo` fields disappear (back to v0.1.x shape), env vars `TOPICFORGE_DDS_*` are silently ignored.

If you migrated client code to import `MiddlewareAdapter`, switch the import back to `RosAdapter` — the v0.1.x name.

---

## 8. Full changelog

See `CHANGELOG.md` section `[0.2.0] - 2026-05-14` for the complete list of changes, additions, soft-breaks, and notes.

Issues or unexpected migration breaks : https://github.com/yaniswav/TopicForge/issues.
