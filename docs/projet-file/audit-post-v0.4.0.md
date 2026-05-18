# Post-v0.4.0 audit

> Branch `chore/post-v0.4.0-audit-and-cleanup`, 2026-05-18.
> Read-only audit + light cleanup before the v0.5.0 polish sprint.
> Baseline tests: **394 passed, 24 skipped**, lint clean.
>
> Note on filename: the user-facing prompt referenced
> `post-v0.4.0-audit.md`, but the gitignore allowlist patterns
> (`!/docs/projet-file/*-audit-*.md` and `!/docs/projet-file/audit-*.md`)
> require either an embedded `-audit-` or a leading `audit-`. We use
> `audit-post-v0.4.0.md` so the report is tracked by default.

---

## Executive summary

| Dim | Topic                          | Status | Headline                                                                                    |
| --- | ------------------------------ | ------ | ------------------------------------------------------------------------------------------- |
| 1   | Git state                      | PASS   | main on 4316c0b, tag v0.4.0 peeled to same SHA, origin aligned, no stale feat branches      |
| 2   | Version state                  | WARN   | code + pyproject + changelog heading at 0.4.0 ; `[0.4.0]` missing date ; `[Unreleased]` empty placeholder + link missing |
| 3   | PyPI deployment                | PASS   | `publish.yml` run #25937555249 success, PyPI returns latest `0.4.0`                         |
| 4   | GitHub release                 | WARN   | release exists, body is one-line title only — full notes missing                            |
| 5   | `pro/` folder integrity        | PASS   | 0 files committed, 15 files on disk, gitignore line 226 active                              |
| 6   | Dev artifacts                  | WARN   | `pr-body-draft.md` untracked at repo root — residue from ship session                       |
| 7   | Doc consistency                | WARN   | Tool count drift (5 / 9 / 11 used inconsistently) ; DDS_QUICKSTART §4 describes a v0.3.0 limitation lifted by v0.4.0 Phase 1 |
| 8   | TODO(roadmap) + audit triage   | WARN   | 7 markers, all legitimately forward-looking ; v0.2.0 triage doc not refreshed (B6/B9/A4 closed but unmarked) |

Zero **FAIL**. Three **PASS**. Five **WARN**, all documentation / housekeeping.
No functional regression detected.

---

## Dimension 1 — Git state — **PASS**

```text
main            4316c0b876a9d79a24c5313f04dc9845c7f4ba75
origin/main     4316c0b876a9d79a24c5313f04dc9845c7f4ba75
v0.4.0 (peel)   4316c0b876a9d79a24c5313f04dc9845c7f4ba75
```

- Annotated tag `v0.4.0` (object `63da0dd`) signed "TopicForge v0.4.0 — DDS observability maturation", tagger `yaniswav`, dated 2026-05-15.
- `git branch -a | findstr feat/v0.4.0` → **empty**. All four feature branches (`feat/v0.4.0-phase1-…`, `…phase15-…`, `…phase2-…`, `…phase3-…`) cleaned up.
- `git worktree list` → single entry (the main worktree). No orphan.

## Dimension 2 — Version state — **WARN**

| Source                          | Value      | Status            |
| ------------------------------- | ---------- | ----------------- |
| `pyproject.toml :: version`     | `"0.4.0"`  | OK                |
| `src/topicforge/__init__.py`    | `"0.4.0"`  | OK                |
| `CHANGELOG.md` heading          | `## [0.4.0]` (no date) | **drift** |
| `CHANGELOG.md` `[Unreleased]`   | section heading absent | **drift** |
| `CHANGELOG.md` link reference   | `[Unreleased]: …compare/v0.3.0...HEAD` | **drift** |
| `CHANGELOG.md` link reference   | `[0.4.0]: …` missing | **drift** |

Issues:

1. **`## [0.4.0]` line has no date.** Other release headings carry `- YYYY-MM-DD` (e.g. `## [0.3.0] - 2026-05-14`). Should be `## [0.4.0] - 2026-05-15`.
2. **No fresh `## [Unreleased]` section** above `[0.4.0]`. Keep a Changelog convention is to leave an empty `[Unreleased]` heading at the top so the next sprint has a receptacle.
3. **Link references at bottom of CHANGELOG are stale**:
   - Line 557: `[Unreleased]: …/compare/v0.3.0...HEAD` should be `…/compare/v0.4.0...HEAD`
   - No `[0.4.0]: …/compare/v0.3.0...v0.4.0` line — the new tag's link is missing.

All of these are safe doc-only edits → **Phase 2 cleanup candidates**.

## Dimension 3 — PyPI deployment — **PASS**

- `gh run list --workflow=publish.yml --limit 3` → most recent run `25937555249` (tag `v0.4.0`) → **completed / success / 39m56s**. (The 39-minute duration is the OIDC sign + index-propagation tail — normal for hatchling+twine.)
- `https://pypi.org/pypi/topicforge/json` → `info.version = "0.4.0"`, releases list `['0.1.0', '0.1.2', '0.2.0', '0.3.0', '0.4.0']`.
- The 0.1.1 gap is pre-existing (yanked, traceable in CHANGELOG history) and not a v0.4.0 concern.

## Dimension 4 — GitHub release — **WARN**

- `gh release view v0.4.0` → release exists, not draft, not prerelease, published `2026-05-15T20:10:25Z`.
- **Body is a one-line title**: literally `"TopicForge v0.4.0 — DDS observability maturation"`. No cumulative recap of the four phases, no migration notes, no link to CHANGELOG sections.
- The CHANGELOG `[0.4.0]` section is rich (Phases 1 / 1.5 / 2 / 3 with sub-milestones), so the source material exists — it just was never copied into the release notes.

**Recommendation: leave for v0.5.0 polish.** The release page is public-facing copy and rewriting it touches marketing surface area outside this branch's scope. The user explicitly said "pas de publication marketing".

## Dimension 5 — `pro/` folder integrity — **PASS**

```text
git ls-tree -r HEAD --name-only | findstr "^pro/"   → empty
Get-ChildItem pro -Recurse -File | Measure-Object   → Count = 15
git check-ignore pro/README.md pro/pyproject.toml   → .gitignore:226: pro/
```

The pro/ tier scaffolding (`README.md`, `pyproject.toml`, `tests/`, `topicforge_pro/` with 15 total files) survives intact on the local disk while staying gitignored. Decision D7 from Phase 1.5 holds.

## Dimension 6 — Dev artifacts — **WARN**

`git status --untracked-files=all` reports one residual artifact:

- **`pr-body-draft.md`** — root-level, ~250 lines, drafted PR body for the v0.4.0 release PR. Contains messy UTF-16 BOM artifacts from PowerShell output redirection (`�...` runs in the first lines). Pure ephemeral, not gitignored.

**Recommendation: delete in Phase 2.** Marketing drafts have an allowlisted home under `docs/projet-file/launch-posts/`, but this is a PR-body scratch file, not a launch post.

## Dimension 7 — Documentation consistency — **WARN**

### 7a. Tool count drift

The repo holds **11 MCP tools** as of v0.4.0 Phase 3 (`tests/test_tools_integration.py::MVP_TOOLS` is authoritative). Surface-level references found:

| Location                                              | Says         | Correct? | Note                                                                                  |
| ----------------------------------------------------- | ------------ | -------- | ------------------------------------------------------------------------------------- |
| `tests/test_tools_integration.py:16-37` (MVP_TOOLS)   | 11 tools     | YES      | authoritative                                                                         |
| `docs/projet-file/mcp-02-spec.md:121`                 | 11 tools     | YES      | updated in Phase 3                                                                    |
| `CLAUDE.md:22`                                        | five MCP tools | YES (intended)  | local-only AI manual ; the "MVP scope (locked)" section, frozen by design |
| `README.md:18-24` (top table)                         | 5 tools      | partial  | shows only ROS2 tools ; DDS/v0.4.0 tools listed separately at line 178-180            |
| `README.md:174`                                       | "Three new MCP tools" (= DDS module) | **STALE** | counts only the v0.2.0 trio ; misses `participant_events`, `topic_metrics`, `peek_bag_samples` |
| `README.md:182`                                       | "all 9 tools" (mock surface)         | **STALE** | should be 11                                                                          |
| `README.md:271`                                       | "One of the five MVP tools"          | **STALE** | telemetry contract field description                                                  |
| `docs/TESTING.md:17,24,31`                            | "five MCP tools"/"five tools"        | **STALE** | three occurrences in the path-picker and Path 1 lead                                  |

**Recommendation: align only the simple numeric drifts (README:182 9→11) in Phase 2.** The `README.md:174` rewrite needs a new mini-table for the v0.4.0 trio, and TESTING.md needs a prose pass — both are v0.5.0 docs polish work.

### 7b. v0.3.0 references

Most v0.3.0 references in user-facing docs are **legitimate historical context** (CHANGELOG entries, migration notes, the `v0.3.0` PyPI tag timeline, the OMG May 2025 interop snapshot date). One **factual stalemate** stands out:

- **`docs/DDS_QUICKSTART.md` §4 "Single-adapter limitation (v0.3.0)"** describes a limitation that **v0.4.0 Phase 1 explicitly lifted** by introducing `CompositeAdapter`. The whole table at line 106-112 is incorrect post-v0.4.0 — the live+cyclone row should now show "Composite (ROS2 CLI + Cyclone) — ROS2 tools work, DDS tools work". Also line 114: "A composite adapter that delegates per-tool category … is on the v0.3.x roadmap" — that adapter shipped.

This is a real user-facing inaccuracy, but the fix is a multi-paragraph rewrite of the section. **Recommendation: v0.5.0 polish, sub-milestone 5.2 (DDS docs cascade).**

## Dimension 8 — TODO(roadmap) + audit-followup triage — **WARN**

### 8a. `TODO(roadmap)` markers

```text
src/topicforge/tools/handlers.py:445  URDF tools
src/topicforge/tools/handlers.py:446  bag anomaly detection
src/topicforge/tools/handlers.py:447  dataset export (rosbag → COCO / HF Datasets)
src/topicforge/tools/handlers.py:448  synthetic data pipeline (Blender / Gazebo / Isaac)
src/topicforge/adapters/ros2_live/adapter.py:9     rclpy-backed adapter (module docstring pointer)
src/topicforge/adapters/ros2_live/adapter.py:145   rclpy-backed adapter (windowed echo)
src/topicforge/adapters/ros2_mock/fixtures.py:485  bag anomaly detection
src/topicforge/services/inspector.py:76            validation symmetry (architecture audit #7)
```

All 8 markers point to features **not shipped by v0.4.0** (rclpy adapter, URDF, bag anomaly detection, dataset export, synthetic data, validation symmetry). None requires retirement in this branch.

### 8b. `audit-followup-triage-v0.2.0.md` status drift

The triage document was last updated 2026-05-14 (pre-v0.3.0). Re-checking each item against the current tree:

| Item   | Description                                                  | Status in v0.4.0           |
| ------ | ------------------------------------------------------------ | -------------------------- |
| A1     | `HealthReport.ros2_distro` env-disclosure note               | likely CLOSED (v0.2.0)     |
| A2     | `mode_effective` asymmetry note                              | likely CLOSED (v0.2.0)     |
| A3     | `parse_echo_yaml` stale TODO refresh                         | likely CLOSED (v0.2.0)     |
| **A4** | `MAX_SAMPLE_COUNT` shared constants module                   | **CLOSED** — `services/constants.py` line 21 |
| B1-B5  | Hosted-context security hardening                            | still open (no work done)  |
| B6     | `AdapterName` / `effective_mode` literal split               | CLOSED (v0.2.0)            |
| B7     | Collapse `Mode`/`ResolvedMode`/`AdapterName`                 | still open                 |
| B8     | `HealthReport.mode` Literal tightening                       | still open                 |
| **B9** | DDS topic-name regex (allow `::`)                            | **CLOSED** in v0.3.0 — see `services/inspector.py:55` "Resolves audit-2026-05-14 'Refactor opportunities' #5" |
| B10    | Inspector validation symmetry                                | still open — `services/inspector.py:76` |

**Recommendation: leave for v0.5.0 sub-milestone 5.3 (audit closure).** The user explicitly noted "Closure des items audit déférés (le scope v0.5.0)". Re-checking each of B1-B10 against the current tree and editing the triage doc is non-trivial work that belongs in the v0.5.0 sprint.

---

## Phase 2 — fixes applied (in this branch)

Per the user's "minor fix sûr" gate, only the items below were applied:

1. **CHANGELOG `## [0.4.0]` → `## [0.4.0] - 2026-05-15`** (release date from the `v0.4.0` tag).
2. **CHANGELOG fresh empty `## [Unreleased]` heading** above `[0.4.0]`.
3. **CHANGELOG link references** updated: `[Unreleased]` now compares from `v0.4.0...HEAD` ; new `[0.4.0]` link inserted between `[Unreleased]` and `[0.3.0]`.
4. **`pr-body-draft.md` removed** from the working tree.
5. **`README.md:182`** : "all 9 tools" → "all 11 tools" (single mechanical count fix).

That is the entire delta. No code changes under `src/topicforge/`. No tests touched. No pyproject changes.

---

## Phase 3 — items deferred to v0.5.0 polish

Mapped to the user's anticipated v0.5.0 sub-milestone structure:

### Sub-milestone 5.1 — README + intro polish

- **README.md:174** rewrite "Three new MCP tools" mini-table → expand to include `participant_events`, `topic_metrics`, `peek_bag_samples`.
- **README.md:271** telemetry contract docs: "One of the five MVP tools" → revise wording (it describes the `tool_name` log field's allowed values).
- **README.md:8** lead paragraph: optionally refresh from "v0.3.0 multi-vendor" framing to a v0.4.0 framing emphasising observability + bag analysis.

### Sub-milestone 5.2 — DDS docs cascade

- **`docs/DDS_QUICKSTART.md` §4** rewrite: drop the "v0.3.0 single-adapter limitation" framing, document the v0.4.0 Composite adapter behaviour.
- **`docs/DDS_QUICKSTART.md` §5** update `peek_dds_samples` scope: v0.4.0 Phase 1.5 introduced XTypes full Cyclone decode for arbitrary user topics.
- **`docs/TESTING.md` §"Pick your path"** refresh tool count from "five" to "eleven" in all three occurrences.

### Sub-milestone 5.3 — Audit closure + triage refresh

- Re-walk `docs/projet-file/audit-followup-triage-v0.2.0.md` item by item against the v0.4.0 tree, mark CLOSED for A4 / B6 / B9 (already closed) and decide CLOSE / DEFER for B1-B5, B7, B8, B10.
- Consider closing the inline `TODO(roadmap, audit-2026-05-14)` at `services/inspector.py:76` if B10 is judged "won't fix by design".

### Out-of-sprint — release polish

- **GitHub release v0.4.0 body** — replace the title-only body with the cumulative four-phase recap drawn from the CHANGELOG. Editable post-hoc via `gh release edit v0.4.0 --notes-file …`. Marketing surface, deliberately not touched here.

---

## Final verification

- Baseline (pre-cleanup) — `python -m pytest` → **394 passed, 24 skipped** ; `python -m ruff check src tests` → **All checks passed!**.
- Post-cleanup — same. Zero src/test changes, so zero regression risk.
- `pyproject.toml` version stays `"0.4.0"`. `__version__` stays `"0.4.0"`. No tag created.
- `git ls-tree -r HEAD --name-only | findstr "^pro/"` → empty.
- `git status` on this branch after cleanup: clean working tree.
