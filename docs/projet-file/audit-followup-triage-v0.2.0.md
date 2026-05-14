# Audit follow-up triage — v0.2.0

Adoption-prep sprint, 2026-05-14. Triage of the two pre-v0.2.0 audits.

## Source rapports

- `security-audit-v0.1.2.md` — branche `audit/security`, SHA `ae80df3`
- `architecture-audit-v0.1.2.md` — branche `audit/architecture`, SHA `e12d9e9`

Scoped sections (per sprint brief): security "Hardening opportunities" (6 bullets) and architecture "⚠️ Refactor opportunities (post-v0.2)" (8 items). Other sections (strengths, extensibility, long-term signals, conventions audit, security roadmap-v0.3+) are out of triage scope ; the security audit's "Roadmap v0.3+" section is already pre-tagged for deferral.

---

## A — Address now (in this sprint)

### A1 — Document `HealthReport.ros2_distro` as env disclosure by design

- **Origine** : security audit, "Hardening opportunities", bullet 6 (line ~22 of report).
- **Description** : `health.py:29` reads `ROS_DISTRO` from the parent env and returns it verbatim in `HealthReport`. Low-sensitivity but reachable by any MCP client.
- **Fix prévu** : Add an explicit note to `HealthReport.ros2_distro` field description in `models/schemas.py` flagging the env-disclosure as intentional under the local-trust threat model. No behavior change.

### A2 — Document `mode_effective` asymmetry across response models

- **Origine** : architecture audit, "⚠️ Refactor opportunities", item 6.
- **Description** : `mode_effective` is on `TopicInfo` / `SampleResult` / `BagAnalysis` but absent from `HealthReport` and `MessageSample`. Asymmetry defensible (Health reports mode via its own fields ; samples nest inside `SampleResult`) but undocumented.
- **Fix prévu** : One-line note appended to `_MODE_EFFECTIVE_DESC` constant in `models/schemas.py` explaining the asymmetry. No behavior change.

### A3 — Update stale `TODO(roadmap)` on `parse_echo_yaml`

- **Origine** : architecture audit, "⚠️ Refactor opportunities", item 8 (and "Conventions audit" WARN line).
- **Description** : `parse_echo_yaml` is no longer called by the live adapter since v0.1.2 (replaced by `parse_csv_echo`). The TODO(roadmap) at `adapters/ros2_live/adapter.py:244` says `rclpy` will obsolete it — already obsoleted by `parse_csv_echo`. The function is still tested (`tests/test_live_adapter_parse.py`).
- **Fix prévu** : Update the comment block above `parse_echo_yaml` to reflect actual status (kept for the test suite as a reference parser, no longer in the hot path). No code change. Removing the function entirely would be a B item (removes 3 tests, design decision).

### A4 — Move `MAX_SAMPLE_COUNT` out of `services.inspector` into a shared constants module

- **Origine** : architecture audit, "⚠️ Refactor opportunities", item 2.
- **Description** : `services/health.py:11` cross-imports `MAX_SAMPLE_COUNT` from `services/inspector.py`. The audit flags this as a smell that duplicates once DDS adds its own per-tool caps — and DDS has shipped (`peek_dds_samples` in `services/inspector.py:90`).
- **Fix prévu** : Create `services/constants.py` hosting `MAX_SAMPLE_COUNT` (and any future per-tool caps). Update imports in `services/inspector.py`, `services/health.py`, and `tests/test_health.py`. Mechanical relocation, no logic change.

---

## B — Defer to v0.3+

### Security-side (5 items from "Hardening opportunities", + the 5 items already in "Roadmap v0.3+" carried forward)

- **B1 — `TOPICFORGE_ROS2_BIN` allowlist for hosted contexts.** Security audit, "Hardening opportunities" #1. Defer : hosted multi-tenant concern, the v0.2.0 threat model is local-trust per README "Security model" line 239. Where to add : `docs/product-plan.md §5`.
- **B2 — Scrubbed `subprocess.run(env=...)` for hosted contexts.** Security audit, "Hardening opportunities" #2. Defer : same hosted concern. Where : `docs/product-plan.md §5`.
- **B3 — `analyze_bag` workspace-root allowlist sandbox.** Security audit, "Hardening opportunities" #3. Defer : same hosted concern. Where : `docs/product-plan.md §5`.
- **B4 — `_validate_bag_path` Path.resolve traversal rejection.** Security audit, "Hardening opportunities" #4. Defer : couples with B3 (only meaningful with a workspace root). Where : `docs/product-plan.md §5` (bundled with B3).
- **B5 — Stricter `stderr_tail` sanitization for adapters running user-supplied commands.** Security audit, "Hardening opportunities" #5. Defer : not relevant in v0.2.0 (no user-supplied-command adapter exists). Where : `docs/product-plan.md §5`.

The security audit's own "Roadmap v0.3+" section (5 items) is already pre-tagged for deferral and will be cross-referenced from `product-plan.md §5`.

### Architecture-side (7 items from "⚠️ Refactor opportunities", excluding items 2, 6, 8 which are A1/A2/A3/A4)

- **B6 — `AdapterName` / `effective_mode` literal split.** Architecture audit, "⚠️ Refactor opportunities" item 1. **Status : already CLOSED in v0.2.0** — `AdapterName` widened to `Literal["mock", "ros2_cli", "cyclone", "rti"]` and `EffectiveMode` extracted as a separate `Literal["mock", "live"]` (`adapters/base.py:25-37`). Not a v0.3+ deferral ; documented here for audit-trail completeness.
- **B7 — Collapse `Mode` / `ResolvedMode` / `AdapterName` into a single tri-mode hierarchy.** Architecture audit item 3. Defer : design decision (which module owns the canonical Literal ?). Where : `docs/product-plan.md §5` "Audit-driven v0.3 candidates".
- **B8 — Tighten `HealthReport.mode` / `requested_mode` from `str` to `Literal`.** Architecture audit item 4. Defer : schema soft-breaking — strict MCP clients validating against v0.2.0 schema would need re-generation. Plan with v0.3 wire-contract review. Where : `docs/product-plan.md §5`.
- **B9 — DDS topic-name regex (allow `::` and DDS separators).** Architecture audit item 5. Defer : depends on the real CycloneDdsAdapter implementation (v0.2.x patch per `mcp-02-spec.md §7`). The v0.2.0 stub raises before any regex check fires, so no immediate breakage. Where : inline `TODO(roadmap, audit-2026-05-14)` in `services/inspector.py` near `_TOPIC_NAME_RE`.
- **B10 — Inspector validation symmetry (`list_topics` vs `get_topic_info`).** Architecture audit item 7. Defer : the asymmetry is defensible per the existing "symmetric gate" docstring ; future DDS tools (`list_participants`) follow the same pattern. Where : inline `TODO(roadmap, audit-2026-05-14)` in `services/inspector.py:list_topics`.

---

## C — Rejected

None. Every item from the two scoped sections falls into A or B. No v0.2.0-philosophy-incompatible recommendations were issued — both audits are aligned with the project's locked decisions in `CLAUDE.md`.

---

## Summary

- **4 A items** (address now in this sprint) — all docstring / mechanical relocation, zero behavior change, zero risk of test regression.
- **9 B items** (defer to v0.3+) — added to `docs/product-plan.md §5` as a new sub-list "Audit-driven v0.3 candidates (from 2026-05-14 audits)", plus 2 inline `TODO(roadmap, audit-2026-05-14)` markers.
- **0 C items**.
- **1 architecture item (B6) already closed by v0.2.0 work** — documented here for completeness, not requeued.
