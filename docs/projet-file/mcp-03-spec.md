# MCP 03 — Vision Dataset Inspector (DatasetForge)

> Spec draft for the third MCP in the robotics/CV pack. Sibling to TopicForge.
> Working name: **DatasetForge**. Brand TBD — naming is the maintainer's call;
> the spec describes the product.

> **Note on slot.** This spec was originally drafted as MCP 02 on 2026-05-13.
> Following the competitive-landscape audit the same day, MCP 02 was reassigned
> to **DdsForge** (DDS observability — see `mcp-02-spec.md`) to occupy an
> uncrowded category and align with the safety-first / DDS-expansion arc.
> DatasetForge becomes MCP 03 in the pack; the spec content below is unchanged.

This is a strategic-internal document (`docs/projet-file/`), not a user-facing
README. It exists so the next Claude Code session can pick up the build with
zero re-derivation. Conventions reuse TopicForge verbatim; only genuine
differences are flagged. When a section says "same as TopicForge", that is
load-bearing — do not re-invent.

---

## 1. Identity

**DatasetForge** — *Computer-vision dataset inspector MCP for AI agents.*

Read images + annotations from a local directory (COCO at MVP; YOLO / HF
Datasets on the roadmap) and answer structured questions about class
balance, split coherence, annotation quality, and representative samples
through a small typed tool surface. Target users are the same robotics
ML / CV engineers who installed TopicForge: they train detection /
segmentation / pose models for the robots whose topics they introspect
with MCP 01.

**Pack position.** MCP 03 of a planned 3-to-5-MCP robotics/CV pack.
TopicForge grounds the agent on the *runtime* robot stack (MCP 01);
DdsForge grounds it on the *DDS layer beneath ROS or beyond ROS* (MCP 02);
DatasetForge grounds it on the *training data* that produced the perception
models running on that stack (MCP 03). Read-only by design — no editing,
no relabeling.

---

## 2. MVP scope (locked, exactly 5 tools)

| Tool             | Purpose                                                       |
| ---------------- | ------------------------------------------------------------- |
| `health_check`   | Environment & mode introspection — always succeeds            |
| `list_datasets`  | Discover dataset roots under the configured directory         |
| `get_dataset_info` | Structured info for one dataset (counts, format, splits)   |
| `class_balance`  | Per-class image / instance counts (incl. zero-count classes)  |
| `sample_images`  | Peek at representative images with file path + labels         |

Do not expand. New tools follow the `add-mcp-tool` skill pattern lifted from
TopicForge. The write path (annotation editing, dataset mutation, model
training) is **out of scope on purpose** — same safety / trust / liability
posture as TopicForge.

Pro-tier candidates already identified (do not ship in MVP): near-duplicate
detection, annotation-quality scoring, multi-version dataset diff,
embedding-based outlier ranking, license/PII audit on dataset images.

---

## 3. Architecture (locked)

Same layer separation as TopicForge. This is the load-bearing decision; do
not bypass it.

```
MCP client → server (FastMCP) → tools/ → services/ → adapters/ → (COCO | mock fixtures)
                  │                │              ↑
                  │            telemetry/    models/ (Pydantic schemas)
                  ▼
      pro/ (optional, auto-detected, license-gated)
```

Layer responsibilities mirror TopicForge §3 verbatim. The only renamed
piece is the adapter protocol:

```python
class DatasetAdapter(Protocol):
    name: AdapterName            # "mock" | "live"
    @property
    def effective_mode(self) -> AdapterName: ...
    def is_available(self) -> bool: ...
    def list_datasets(self) -> list[DatasetInfo]: ...
    def get_dataset_info(self, name: str) -> DatasetInfo: ...
    def class_balance(self, name: str) -> ClassBalance: ...
    def sample_images(self, name: str, count: int) -> list[ImageSample]: ...
```

`effective_mode` propagation through every tool response is inherited from
TopicForge v0.1.2 (Stream B) — `mode_effective` is a required field on every
response carrier so a downstream LLM cannot confuse a live response with a
mock one. **This is not optional**; it is part of the pack-wide contract.

Adapter implementations at MVP:
- `MockAdapter` — deterministic in-memory tiny COCO-shaped dataset (5
  classes, ~20 images, two splits). Always available.
- `CocoAdapter` — reads `instances_<split>.json` and the matching image
  directory. No PyTorch / no Pillow at import time; Pillow is lazy-imported
  only inside `sample_images` for image dimensions, and absence falls back
  to `width=null, height=null`.

`YoloAdapter`, `PascalVocAdapter`, `HfDatasetsAdapter` are Phase 1 roadmap
items behind the same `DatasetAdapter` protocol. Adding one follows an
`add-dataset-adapter` skill (lifted from TopicForge's `add-ros2-adapter`).

---

## 4. Runtime modes

Same triplet as TopicForge, selected via `DATASETFORGE_MODE`:

| Mode    | When to use                                                  | Backend                |
| ------- | ------------------------------------------------------------ | ---------------------- |
| `mock`  | Development, demos, CI, screencasts                          | Deterministic fixtures |
| `live`  | Real dataset directory configured and readable               | COCO reader            |
| `auto`  | Detect a dataset root; fall back to mock if absent (default) | Best available         |

The `auto` resolution lives in `Settings.effective_mode` (`config/settings.py`).
Final fallback (live → mock when the configured root is missing) lives in
`services/factory.py`. **One place each** — same constraint as TopicForge §4.

Runtime env vars:

- `DATASETFORGE_LOG_LEVEL` — `DEBUG | INFO | WARNING | ERROR`, default `INFO`.
- `DATASETFORGE_ROOT` — absolute path to the directory containing one or
  more dataset roots. Required for `live`. Validated at adapter
  construction; non-existent path → fall back to mock with a warning.
- `DATASETFORGE_TELEMETRY` — opt-in anonymous telemetry, same payload
  contract as TopicForge (six-field event, OFF means verified no-op).
- `DATASETFORGE_LICENSE_KEY` — `dsf_*`-prefixed key, consumed only by the
  optional `datasetforge_pro` add-on.

Naming pattern (`<PACK>_<KNOB>`) is the pack-wide convention. When the third
MCP lands, factor these into a shared `pack_config` helper rather than
duplicating the resolver three times.

---

## 5. Stack (locked)

Same as TopicForge §5: Python 3.11+, `mcp >= 1.0.0`, `pydantic >= 2.6`
(`extra="forbid"`, `frozen=True` via shared `_CONFIG`), `pytest`, `ruff`,
Hatchling. No alternatives without strong justification.

**One optional dep**: `Pillow`, lazy-imported only inside `sample_images`
for image dimension probing. Absence tolerated (width/height → `null`).
Not declared as a hard dep — adding ~3 MB to every install for an optional
return field is dishonest.

---

## 6. Engineering principles

Inherit verbatim from `topicforge/CLAUDE.md §6` (clean architecture, type
hints, structured outputs, graceful degradation, mock mode is mandatory,
no giant files, no premature abstraction). Two DatasetForge-specific
deltas:

- All filesystem reads go through adapters. Handlers never call `open()`;
  services never parse annotation JSON. The `DatasetAdapter` protocol is
  the only abstraction that earns its keep at MVP — no premature `Class`,
  `Annotation`, `Split` interfaces.
- Cross-platform via `pathlib` only — no `subprocess` layer to worry about,
  which is the one place DatasetForge is simpler than TopicForge.

---

## 7. Phase 1 targets

The MVP bootstrap = the entire Phase 1 here, deliberately scoped to fit in
two weeks of part-time work.

- **v0.1.0** — MVP ship on PyPI. Five MCP tools, mock + COCO adapter,
  Pydantic schemas with `mode_effective`, `ruff` + `pytest`, CI on Python
  3.11 + 3.12, GitHub Action publishing on tag `v*`.
- **v0.1.1** — Opt-in anonymous telemetry (`DATASETFORGE_TELEMETRY=on`).
  Same six-field payload as TopicForge so a future shared endpoint can
  serve both. Identical OFF-means-no-network pin via a unit test.
- **v0.1.2 candidates** (one per release, only if real user demand):
  - `YoloAdapter` (single-class-file YOLO format).
  - Native `instances_val2017.json`-style multi-split discovery
    (auto-detect `train` / `val` / `test`).
  - `sample_images` time-budget / random-seed control for reproducibility.
  - Server-side telemetry endpoint (cross-pack, shared with TopicForge).

When a Phase 1 item ships, retire the matching `# TODO(roadmap):` tag in
code and the corresponding entry here.

---

## 8. Risk register

Pack-wide risks (MCP churn, time dilution) inherit from
`topicforge/docs/product-plan.md §11`. DatasetForge-specific risks:

- **COCO format drift.** Wild-COCO-ish exports from Roboflow / Label Studio
  / CVAT diverge from the canonical 2014/2017 shape. Mitigation: pure
  `parse_coco_json` with explicit field defaults, fixtures per exporter.
- **Persona split (robotics ML vs pure CV).** Targets the former first for
  pack synergy. Mitigation: track sign-up free-text answers; revisit
  positioning at the G2 gate if pure-CV signal dominates.
- **Pillow optionality.** Lazy-imported; absence → `width/height = null`.
  Pin with a stubbed-import test.
- **Dataset size.** 50 GB roots can't be parsed synchronously. MVP cap:
  refuse `instances_*.json` files > 200 MB with a clear `AdapterError`,
  exposed via `health_check.max_annotation_bytes` (mirrors TopicForge's
  `max_sample_count`). Streaming parsers are roadmap.
- **Pro-tier overlap with FiftyOne / Voxel51 / Roboflow.** The MCP angle
  is the wedge — LLM-grounded inspection from inside Claude / Cursor /
  Cline. Do not chase UI parity; if a year in we are, positioning is wrong.

---

## 9. Monetization

Same three-tier model as TopicForge §9 (Free MIT / Pro commercial /
Enterprise inbound-only). Pricing terms reused verbatim from `docs/pro.md`
— $12/mo locked-for-life for the first 10 Pro customers, $19/mo after.
License gating via `DATASETFORGE_LICENSE_KEY`, fails closed on
missing/invalid keys. **No Pro feature ships until 10 early-access slots
are reserved** (pack-wide rule, not per-MCP).

Pro-tier headline candidates (under evaluation, do not commit):

- **Near-duplicate detection** — perceptual hashing, returns ranked
  clusters with `(image_id, hash_distance, sample_neighbors)`.
- **Annotation-quality score** — per-image confidence from bbox-size
  outliers, mask-edge entropy, class-cooccurrence priors.
- **Multi-version dataset diff** — surface added/removed/relabeled images
  between two snapshots.

Enterprise is gated by the pack-wide G3 trigger
(`product-plan.md §12`) — three open-source logos + inbound-with-budget,
*both MCPs* triggered together, not per-MCP.

---

## 10. What to avoid

Inherit verbatim from `topicforge/CLAUDE.md §11` (no generic framework, no
UI, no hardcoded paths, tests have no external deps, no vague exceptions,
respect layer separation, never break mock mode). DatasetForge-specific
additions:

- Do not silently coerce image data to tensors / numpy arrays.
  `sample_images` returns paths + labels + optional dimensions, never
  pixel data. Pixel access is the trainer's job, not the inspector's.
- Do not annotate / re-label / re-export. Read-only is the safety
  contract — same posture as TopicForge's no-publish rule.
- Do not chase FiftyOne / Voxel51 feature parity. The wedge is MCP-native
  LLM grounding, not a richer Python API.

---

## 11. Open questions (resolve before v0.1.0)

Deliberate gaps for the maintainer — not roadmap items.

- **Naming.** `DatasetForge` matches the pack pattern but is broader than
  the CV scope. Alternative: `VisionForge`. Decision before launch.
- **First live adapter.** COCO is the proposed default; YOLO targets a
  different (smaller, hobbyist) audience. Block on signal from 3 robotics
  MLE DMs in the install funnel before locking.
- **Pack-shared infrastructure.** Phase 1 of DatasetForge duplicates
  TopicForge's telemetry / license / settings resolver. MCP 03 must not.
  Lift to a `pack-template/` repo when MCP 03 starts, not before.

---

## 12. References

- `topicforge/CLAUDE.md` — operating manual; §3 / §5 / §6 / §7 / §8 / §11
  inherit verbatim.
- `topicforge/docs/product-plan.md` — pack vision (§4), monetization (§9),
  decision gates G2/G3 (§12).
- `topicforge/docs/pro.md` — pricing terms reused for the Pro tier.
- `topicforge/.claude/skills/topicforge/` — `add-mcp-tool`,
  `write-pure-parser`, `update-mock-fixtures`, `release-checklist` apply
  near-verbatim. Fork-and-tweak at MVP; lift to a shared template when
  MCP 03 starts.

---

## Reviewer notes (2026-05-13)

> This section was added by the spec-reviewer agent during the v0.1.2 prep
> review. It does not modify the spec body. Issues listed below must be
> resolved before or at implementation kickoff.

### Issue 1 — Contradictory phrasing in §11 (Pack-shared infrastructure)

**Location.** §11, "Pack-shared infrastructure" bullet:
> "Phase 1 of DatasetForge duplicates TopicForge's telemetry / license /
> settings resolver. MCP 03 must not. Lift to a `pack-template/` repo when
> MCP 03 starts, not before."

**Problem.** The first sentence says Phase 1 of DatasetForge *will* duplicate
the infrastructure. The second sentence says MCP 03 *must not* duplicate it.
This is self-contradictory and leaves the implementer without a clear
sequence. The intent (per `mcp-02-spec.md §11`) is that DatasetForge, as the
third user of the pattern, triggers the extraction *before* its own Phase 1
implementation begins — so DatasetForge Phase 1 builds on the shared template
rather than duplicating it again. The current phrasing inverts this.

**Suggested resolution.** Rewrite to: "DatasetForge is the third user of the
pattern — the rule-of-three trigger for extraction. Lift the telemetry /
license / settings resolver to a `pack-template/` repo at DatasetForge
kickoff, before Phase 1 implementation. DatasetForge Phase 1 then builds on
the shared template rather than duplicating it."

### Issue 2 — Two open questions implicitly resolved by product-plan.md §4

**Location.** §11, "Naming" and "First live adapter" bullets.

**Problem.**

- **Naming.** `product-plan.md §4` already uses "DatasetForge" as the
  canonical name throughout ("MCP 03 — DatasetForge", "DatasetForge becomes
  MCP 03 in the pack"). The §11 open question asks for a decision between
  `DatasetForge` and `VisionForge`, but the strategic plan has already
  committed to `DatasetForge`. This question is implicitly resolved and
  should be formally closed to avoid confusion at kickoff.

- **First live adapter.** `product-plan.md §4` states "Read images +
  annotations (COCO at MVP; YOLO / HF Datasets on roadmap)" — COCO is locked
  as the first live adapter at the strategic level. The §11 gate ("Block on
  signal from 3 robotics MLE DMs") contradicts this commitment, implying the
  decision is still open when the product plan has already closed it.

**Suggested resolution.** Mark both bullets as resolved in §11 (or remove
them), noting that `product-plan.md §4` is the authority: DatasetForge is the
locked name, COCO is the locked first live adapter.
