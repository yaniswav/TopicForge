# Audit follow-up triage: v0.2.0 (refreshed v0.5.0)

Adoption-prep sprint, 2026-05-14. Triage of the two pre-v0.2.0 audits.

> **2026-05-18 refresh (v0.5.0 polish sprint).** Each item is re-checked
> against the current tree. A items shipped during v0.2.0 adoption-prep
> ; six of the nine B items are now closable, leaving three that stay
> deferred. See the "Status v0.5.0" column on each item.

## Source rapports

- `security-audit-v0.1.2.md`: branche `audit/security`, SHA `ae80df3`
- `architecture-audit-v0.1.2.md`: branche `audit/architecture`, SHA `e12d9e9`

Scoped sections (per sprint brief): security "Hardening opportunities" (6 bullets) and architecture "Refactor opportunities (post-v0.2)" (8 items). Other sections (strengths, extensibility, long-term signals, conventions audit, security roadmap-v0.3+) are out of triage scope ; the security audit's "Roadmap v0.3+" section is already pre-tagged for deferral.

---

## A: Address now (in this sprint); all shipped v0.2.0

### A1: Document `HealthReport.ros2_distro` as env disclosure by design

- **Origine** : security audit, "Hardening opportunities", bullet 6 (line ~22 of report).
- **Description** : `health.py:29` reads `ROS_DISTRO` from the parent env and returns it verbatim in `HealthReport`. Low-sensitivity but reachable by any MCP client.
- **Fix prévu** : Add an explicit note to `HealthReport.ros2_distro` field description in `models/schemas.py` flagging the env-disclosure as intentional under the local-trust threat model. No behavior change.
- **Status v0.5.0** : **CLOSED in v0.2.0**: see `models/schemas.py` `HealthReport.ros2_distro` Field description.

### A2: Document `mode_effective` asymmetry across response models

- **Origine** : architecture audit, "Refactor opportunities", item 6.
- **Description** : `mode_effective` is on `TopicInfo` / `SampleResult` / `BagAnalysis` but absent from `HealthReport` and `MessageSample`. Asymmetry defensible (Health reports mode via its own fields ; samples nest inside `SampleResult`) but undocumented.
- **Fix prévu** : One-line note appended to `_MODE_EFFECTIVE_DESC` constant in `models/schemas.py` explaining the asymmetry. No behavior change.
- **Status v0.5.0** : **CLOSED in v0.2.0**: note present at `models/schemas.py:_MODE_EFFECTIVE_DESC`.

### A3: Update stale `TODO(roadmap)` on `parse_echo_yaml`

- **Origine** : architecture audit, "Refactor opportunities", item 8 (and "Conventions audit" WARN line).
- **Description** : `parse_echo_yaml` is no longer called by the live adapter since v0.1.2 (replaced by `parse_csv_echo`). The TODO(roadmap) at `adapters/ros2_live/adapter.py:244` says `rclpy` will obsolete it: already obsoleted by `parse_csv_echo`. The function is still tested (`tests/test_live_adapter_parse.py`).
- **Fix prévu** : Update the comment block above `parse_echo_yaml` to reflect actual status (kept for the test suite as a reference parser, no longer in the hot path). No code change. Removing the function entirely would be a B item (removes 3 tests, design decision).
- **Status v0.5.0** : **CLOSED in v0.2.0**: comment block refreshed.

### A4: Move `MAX_SAMPLE_COUNT` out of `services.inspector` into a shared constants module

- **Origine** : architecture audit, "Refactor opportunities", item 2.
- **Description** : `services/health.py:11` cross-imports `MAX_SAMPLE_COUNT` from `services/inspector.py`. The audit flags this as a smell that duplicates once DDS adds its own per-tool caps. And DDS has shipped (`peek_dds_samples` in `services/inspector.py:90`).
- **Fix prévu** : Create `services/constants.py` hosting `MAX_SAMPLE_COUNT` (and any future per-tool caps). Update imports in `services/inspector.py`, `services/health.py`, and `tests/test_health.py`. Mechanical relocation, no logic change.
- **Status v0.5.0** : **CLOSED in v0.2.0**: `services/constants.py:21` is the canonical home. `services/inspector.py:38` re-exports it for backward-compat with v0.1.x importers.

---

## B: Deferred to v0.3+; refreshed dispositions v0.5.0

### Security-side: `B1`-`B5` still DEFER

The five "Hardening opportunities" bullets remain hosted-context-only or future-adapter-only concerns. No work shipped between v0.2.0 and v0.4.0 since the threat model is still local-trust (`README.md` "Security model"). They live in `docs/product-plan.md section 5` as "Audit-driven v0.3 candidates" and stay there until the hosted MCP endpoint (Phase 3 in section 7) reopens them.

- **B1: `TOPICFORGE_ROS2_BIN` allowlist for hosted contexts.** Security audit, "Hardening opportunities" #1. **DEFER** (no change). Where : `docs/product-plan.md section 5`.
- **B2: Scrubbed `subprocess.run(env=...)` for hosted contexts.** Security audit, "Hardening opportunities" #2. **DEFER** (no change). Where : `docs/product-plan.md section 5`.
- **B3: `analyze_bag` workspace-root allowlist sandbox.** Security audit, "Hardening opportunities" #3. **DEFER** (no change). Where : `docs/product-plan.md section 5`.
- **B4: `_validate_bag_path` Path.resolve traversal rejection.** Security audit, "Hardening opportunities" #4. **DEFER** (no change). Where : `docs/product-plan.md section 5` (bundled with B3).
- **B5: Stricter `stderr_tail` sanitization for adapters running user-supplied commands.** Security audit, "Hardening opportunities" #5. **DEFER** (no change). Where : `docs/product-plan.md section 5`.

The security audit's own "Roadmap v0.3+" section (5 items) remains pre-tagged for the hosted-endpoint phase.

### Architecture-side: `B6` / `B9` / `B10` CLOSED ; `B7` / `B8` still DEFER

- **B6: `AdapterName` / `effective_mode` literal split.** Architecture audit, "Refactor opportunities" item 1. **Status : CLOSED in v0.2.0**: `AdapterName` widened to `Literal["mock", "ros2_cli", "cyclone", "rti"]` and `EffectiveMode` extracted as a separate `Literal["mock", "live"]` (`adapters/base.py:25-37`). Not a v0.3+ deferral ; documented here for audit-trail completeness.
- **B7: Collapse `Mode` / `ResolvedMode` / `AdapterName` into a single tri-mode hierarchy.** Architecture audit item 3. **DEFER** (no change). Design decision still pending: which module owns the canonical `RuntimeMode` Literal ? Plan with the v0.6 wire-contract review alongside B8. Where : `docs/product-plan.md section 5` "Audit-driven v0.3 candidates".
- **B8: Tighten `HealthReport.mode` / `requested_mode` from `str` to `Literal`.** Architecture audit item 4. **DEFER** (no change). Schema soft-breaking: strict MCP clients validating against v0.2.0 schema would need re-generation. Plan with v0.6 wire-contract review. Where : `docs/product-plan.md section 5`.
- **B9: DDS topic-name regex (allow `::` and DDS separators).** Architecture audit item 5. **Status : CLOSED in v0.3.0**: `_validate_topic_name_dds` (`services/inspector.py:56`) ships the relaxed validator with the explicit "Resolves audit-2026-05-14 'Refactor opportunities' #5" comment. The strict ROS2 validator stays in place for the 5 ROS2 graph methods.
- **B10: Inspector validation symmetry (`list_topics` vs `get_topic_info`).** Architecture audit item 7. **Status : CLOSED in v0.5.0: WONT-FIX by design.** Decision : `list_topics` takes no MCP-level arguments, so an Inspector-side gate has nothing to validate. Peer methods like `get_topic_info` validate ; the asymmetry is structural, not accidental. The inline `TODO(roadmap, audit-2026-05-14)` at `services/inspector.py:76` is retired and replaced with a permanent design note. Reopen only if a future tool variant ships with args that need normalizing in `list_topics`.

---

## C: Rejected

None. Every item from the two scoped sections falls into A or B. No v0.2.0-philosophy-incompatible recommendations were issued: both audits are aligned with the project's locked decisions in `CLAUDE.md`.

---

## Summary: refreshed 2026-05-18 (v0.5.0)

- **4 A items** (originally scoped for v0.2.0): all SHIPPED. Zero behavior change, zero test regression.
- **9 B items** (originally deferred to v0.3+):
  - **6 CLOSED** : A4 + B6 in v0.2.0 ; B9 in v0.3.0 ; B10 in v0.5.0 as WONT-FIX-by-design. (A1, A2, A3 are A-class but listed here for the audit-trail count.) Strictly B-class : **3 CLOSED** (B6, B9, B10).
  - **6 DEFER** : B1, B2, B3, B4, B5 (hosted-context security hardening) and B7, B8 (architecture wire-contract decisions). All live in `docs/product-plan.md section 5`.
- **0 C items**.

This document is now load-bearing only for the 6 deferred items. When the hosted MCP endpoint sprint (Phase 3 in `product-plan.md section 7`) opens, refresh again: most security-side items will reopen at that point.
