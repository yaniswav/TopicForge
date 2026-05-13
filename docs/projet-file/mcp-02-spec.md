# MCP 02 — DDS Observability MCP (DdsForge)

> Spec draft for the second MCP in the robotics/CV pack. Sibling to TopicForge,
> built on the same `*Adapter` protocol generalization. Working name:
> **DdsForge**. Brand TBD — naming is the maintainer's call.

> **Status.** Draft. Authored 2026-05-13 during the v0.1.2 prep window, after
> the competitive-landscape audit that surfaced at least six existing ROS-MCP
> projects and zero non-ROS DDS-MCP projects. This spec locks the MCP 02 slot
> on DdsForge and slides DatasetForge (the previous MCP 02 draft) to MCP 03,
> preserved verbatim under `mcp-03-spec.md`.

This is a strategic-internal document (`docs/projet-file/`), not a user-facing
README. Conventions reuse TopicForge verbatim; only genuine differences are
flagged. When a section says "same as TopicForge", that is load-bearing — do
not re-invent.

---

## 1. Identity

**DdsForge** — *Safety-first read-only MCP for DDS observability across
middleware vendors.*

Where DDS users today reach for vendor-specific tools (RTI Admin Console,
Cyclone DDS CLI, `rtiddsspy`) to inspect a live bus, DdsForge exposes the
same observation surface — participants, topics, QoS profiles, samples,
mismatch diagnostics — through a single typed MCP tool surface that an LLM
agent can drive. Read-only by **architecture**, not by configuration: there
is no write path to misconfigure, no permission system to audit, no
liability conversation to have.

**Pack position.** MCP 02 of a planned 3-to-5-MCP robotics/CV pack.
TopicForge (MCP 01) grounds the agent on the *runtime ROS2 graph*;
DdsForge grounds it on the *bare DDS layer beneath ROS or beyond ROS*;
DatasetForge (MCP 03) grounds it on the *training data* that produced the
perception models running on that stack.

Target users — same concentric circles as TopicForge §3 but tilted further
toward the strategic core:

- **Strategic core.** DDS-native engineering teams in defense, aerospace,
  automotive AUTOSAR Adaptive, naval, and industrial integration. They
  cannot ship a tool that publishes back to the bus or modifies QoS in
  production; an LLM agent that *only* observes is the only acceptable shape.
- **Tactical adjacent.** Mid-size robotics and middleware teams running
  ROS2 alongside non-ROS DDS components (a common pattern in industrial
  multi-stack environments). DdsForge complements TopicForge in this context.
- **Funnel.** DDS-curious developers prototyping with CycloneDDS. Free tier
  captures them and drives discoverability.

The strategic point is not "another DDS console". The point is **an
AI-agent-driven DDS diagnostic assistant** that handles the "why doesn't
this subscriber receive anything?" / "what QoS mismatch is preventing
communication?" / "compare observed topology to expected" class of
questions that current GUI tools require manual interpretation for.

---

## 2. MVP scope (locked, exactly 5 tools)

| Tool                    | Purpose                                                                                  |
| ----------------------- | ---------------------------------------------------------------------------------------- |
| `health_check`          | Middleware detection (Cyclone / RTI / mock), version, domain id, server introspection    |
| `list_participants`     | Discovered DDS participants on the configured domain, with vendor and hostname           |
| `list_topics`           | Discovered topics with reader / writer counts and the effective QoS profile per endpoint |
| `detect_qos_mismatches` | Analyzer: surface reader/writer QoS incompatibilities that prevent communication         |
| `peek_samples`          | Recent serialized samples on a topic, with type info and receive timestamps              |

Do not expand. New tools follow an `add-mcp-tool`-equivalent skill (to be
created as `.claude/skills/ddsforge/add-mcp-tool/`, lifted from TopicForge).

The write path — publishing samples, calling RPCs, modifying QoS at
runtime, controlling domains — is **out of scope by architecture,
permanently**. This is the load-bearing positioning commitment. Defense
and aerospace acceptance depends on it.

Pro-tier candidates already identified (do not ship in MVP): RTI Connext
live adapter, recorded-bus replay analysis, multi-domain comparison,
QoS-policy-pack heuristics beyond the core four (Reliability, Durability,
History, Deadline → Liveliness, Ownership, Partition, TimeBasedFilter,
LatencyBudget).

---

## 3. Architecture (locked)

Same layer separation as TopicForge §3 — this is the load-bearing decision,
do not bypass.

```
MCP client → server (FastMCP) → tools/ → services/ → adapters/ → (Cyclone | RTI | mock)
                  │                │              ↑
                  │            telemetry/    models/ (Pydantic schemas)
                  ▼
      pro/ (optional, auto-detected, license-gated, RTI lives here)
```

The novel piece relative to TopicForge is the **`MiddlewareAdapter`
protocol** — a parallel to `RosAdapter` but designed cross-vendor from
day one:

```python
@runtime_checkable
class MiddlewareAdapter(Protocol):
    name: AdapterName  # "mock" | "cyclone" | "rti"

    @property
    def effective_mode(self) -> Literal["mock", "live"]: ...

    def is_available(self) -> bool: ...

    def list_participants(self) -> list[ParticipantInfo]: ...
    def list_topics(self) -> list[TopicInfo]: ...
    def detect_qos_mismatches(self, topic: str | None = None) -> list[MismatchReport]: ...
    def peek_samples(self, topic: str, count: int) -> list[SampleResult]: ...
```

`effective_mode` propagation through every tool response is inherited from
TopicForge v0.1.2 (Stream B) — `mode_effective` is a required field on every
response carrier. **This is not optional**; it is part of the pack-wide
contract. The `RosAdapter` protocol in TopicForge generalizes into the
`MiddlewareAdapter` protocol cleanly — same shape, broader domain.

Adapter implementations at MVP:

- **`MockMiddlewareAdapter`** — deterministic fixtures modeling a
  two-participant scenario with one well-matched topic (reader+writer
  compatible) and one mismatched topic (deliberate QoS incompatibility, to
  exercise `detect_qos_mismatches`). Always available. The mock fixture is
  the demo asset and the test substrate.
- **`CycloneDdsAdapter`** (OSS, default live) — built on the `cyclonedds`
  Python bindings (BSD-licensed, available via pip on Windows / Linux /
  macOS). The default `auto` resolution targets this when `cyclonedds`
  imports successfully.
- **`RtiConnextAdapter`** (Pro tier, optional add-on) — uses RTI's
  `rti.connextdds` bindings or the older `rticonnextdds-connector`;
  license-gated via `DDSFORGE_LICENSE_KEY` mirroring TopicForge's Pro
  architecture. Auto-detected by `_try_register_pro(mcp)` in
  `server/app.py`. Failure to register Pro is a logged no-op — the OSS
  MVP never depends on RTI being installable.

OpenSplice is EOL and explicitly not pursued.

**Pure analyzers live module-level.** `detect_mismatches(reader_qos,
writer_qos) -> list[MismatchReason]` is a pure function called by adapters
after they have collected the QoS profiles. This mirrors TopicForge's
"pure parsers" convention (see `.claude/skills/topicforge/write-pure-parser/`)
— testable against synthesized QoS pairs without any DDS middleware
installed.

---

## 4. Runtime modes

Same triplet shape as TopicForge, plus an extra explicit-backend mode for
the live tier, selected via `DDSFORGE_MODE`:

| Mode      | When to use                                            | Backend                                          |
| --------- | ------------------------------------------------------ | ------------------------------------------------ |
| `mock`    | Development, demos, CI, screencasts                    | Deterministic fixtures (two-participant scenario)|
| `cyclone` | Force CycloneDDS (skip RTI even if Pro is installed)   | `cyclonedds` Python bindings                     |
| `rti`     | Force RTI (Pro tier, license required)                 | RTI Python bindings (license-gated)              |
| `auto`    | Detect best available; fall back to mock               | RTI > Cyclone > mock                             |

`auto` resolution lives in `Settings.effective_middleware`
(`config/settings.py`). Final operational fallback (when an adapter
instantiates but `is_available()` returns False) lives in
`services/factory.py`. **One place each** — same constraint as TopicForge §4.

Runtime env vars:

- `DDSFORGE_LOG_LEVEL` — `DEBUG | INFO | WARNING | ERROR`, default `INFO`.
- `DDSFORGE_DOMAIN_ID` — DDS domain id to observe, default `0`. Validated
  as `0 <= int <= 232`.
- `DDSFORGE_TELEMETRY` — opt-in anonymous telemetry, same six-field
  payload contract as TopicForge (`tool_name`, `latency_ms`, `mode`,
  `version`, `session_id`, `success`). OFF means verified no-op via a unit
  test inherited verbatim from TopicForge.
- `DDSFORGE_LICENSE_KEY` — `dfp_*`-prefixed key, consumed only by the
  optional `ddsforge_pro` add-on. The OSS core ignores this variable.

Naming pattern `<PACK>_<KNOB>` is the pack-wide convention. When MCP 03
(DatasetForge) ships, factor these into a shared `pack_config` helper
rather than duplicating the resolver three times.

---

## 5. Stack (locked)

Same as TopicForge §5:

- **Python 3.11+**
- **`mcp >= 1.0.0`** (FastMCP)
- **`pydantic >= 2.6`** — `extra="forbid"` and `frozen=True` via the shared
  `_CONFIG` in `models/schemas.py`
- **`cyclonedds`** (Python bindings) for the default live adapter —
  BSD-licensed, installable via pip on Windows / Linux / macOS
- **`pytest`** for tests; mock-mode tests never require any DDS middleware
  installed
- **`ruff`** for lint and format
- **Hatchling** for build

Pro adapter pins `rti.connextdds` lazily; the OSS core does not import it
and the absence of RTI never breaks the install.

---

## 6. Engineering principles

Inherit verbatim from `topicforge/CLAUDE.md §6` (clean architecture, type
hints, structured outputs, graceful degradation, mock mode is mandatory,
no giant files, no premature abstraction). Three DdsForge-specific deltas:

- All middleware access goes through adapters. Handlers never import
  `cyclonedds` or `rti`; services never construct DDS entities. The
  `MiddlewareAdapter` protocol is the only cross-vendor abstraction that
  earns its keep at MVP.
- Pure analyzers separate from middleware I/O. `detect_mismatches` is a
  pure function over `(reader_qos, writer_qos)`; the adapter collects the
  QoS pairs and calls it. Tests pin the analyzer against synthesized QoS
  pairs without any middleware installed (same pattern as TopicForge's
  `parse_topic_list` etc.).
- Read-only by architecture, not by configuration. No method on
  `MiddlewareAdapter` ever takes a "publish", "modify", or "write"
  parameter. The protocol shape itself enforces the safety promise.

---

## 7. Phase 1 targets

The MVP bootstrap = the entire Phase 1, scoped to fit in two weeks of
part-time work once it kicks off (post-TopicForge v0.1.2).

- **v0.1.0** — MVP ship on PyPI. Five MCP tools, `MockMiddlewareAdapter` +
  `CycloneDdsAdapter`, full Pydantic schemas with `mode_effective`, ruff +
  pytest, CI on Python 3.11 / 3.12, GitHub Action publishing on tag `v*`
  via OIDC.
- **v0.1.1** — Opt-in anonymous telemetry (`DDSFORGE_TELEMETRY=on`). Same
  six-field payload as TopicForge so a future shared endpoint can serve
  both. Identical OFF-means-no-network pin via unit test.
- **v0.1.2 candidates** (one per release, only if real user demand):
  - Extended QoS-mismatch coverage (Liveliness, Ownership, Partition).
  - `peek_samples` with type metadata for custom IDL types.
  - Multi-domain observation (`DDSFORGE_DOMAIN_IDS=0,1,2`).
  - Server-side telemetry endpoint (cross-pack, shared with TopicForge).
- **v0.2.0** — `RtiConnextAdapter` ships as the Pro tier first feature.
  License-gated, BYO RTI license. Same `_try_register_pro(mcp)` pattern.

When a Phase 1 item ships, retire the matching `# TODO(roadmap):` tag in
code and the corresponding entry here.

---

## 8. Risk register

Pack-wide risks (MCP churn, time dilution, competitive landscape) inherit
from `topicforge/docs/product-plan.md §11`. DdsForge-specific risks:

- **Vendor binding instability.** `cyclonedds` Python bindings have
  evolved since 2023; pin a major version and run smoke tests on every
  release. Mitigation: keep Cyclone-specific code behind the
  `MiddlewareAdapter` protocol so a binding rev is an adapter patch, not
  a project rewrite.
- **RTI licensing complexity.** Pro tier customers must bring their own
  RTI Connext license. This is a procurement conversation, not a
  self-serve flow. Mitigation: Pro tier is intentionally low-volume /
  high-value; do not optimize for self-serve until volume warrants.
- **QoS mismatch false positives.** The analyzer must err on the side of
  completeness without flooding output. Mitigation: classify mismatches
  as `incompatible` (definitely blocks comm) vs `risky` (may degrade but
  not block). Tests pin both categories against fixture QoS pairs.
- **Audience overlap with TopicForge.** Some users will install both for
  ROS2 + non-ROS DDS in the same stack. Mitigation: explicit cross-link
  in both READMEs; do not duplicate the ROS2-graph-specific framing in
  DdsForge — DdsForge is the bare DDS layer.
- **No write path == no demo virality.** Unlike `robotmcp/ros-mcp-server`
  which can demo Claude piloting an Unitree dog, DdsForge cannot do a
  viral "AI controls the X" video. Mitigation: lean into the diagnostic
  story — a screencast of an LLM identifying a QoS mismatch in 10 seconds
  versus a human reading vendor docs is a different kind of compelling,
  and the right kind for the audience.
- **Defense / aerospace require enterprise sales motion.** RFPs, security
  reviews, export controls. Mitigation: enterprise tier is gated by the
  pack-wide G3 trigger (three open-source logos + inbound-with-budget) —
  do not pursue outbound enterprise until OSS validation lands.
- **Pack execution risk.** Solo maintainer shipping TopicForge,
  DdsForge, and (later) DatasetForge in parallel is the realistic risk.
  Mitigation: explicit phase gating — DdsForge does not start v0.1.0
  implementation until TopicForge v0.1.2 has shipped AND the
  `MiddlewareAdapter` architectural decisions are ratified.

---

## 9. Monetization

Same three-tier model as TopicForge §9.

- **Free (BSD or MIT).** `MockMiddlewareAdapter` + `CycloneDdsAdapter`.
  CycloneDDS being BSD-licensed makes redistribution clean.
  `pip install ddsforge`. Single line at the README bottom pointing to Pro.
- **Pro (commercial license).** `RtiConnextAdapter` and Pro-only
  analyzers under a separate `ddsforge_pro` package. License-gated via
  `DDSFORGE_LICENSE_KEY`. Pricing aligned with TopicForge Pro: $12/month
  for the first ten early-access customers, $19/month after, or annual
  invoice for procurement-bound customers. No Pro feature ships until 10
  early-access slots are reserved (pack-wide rule, not per-MCP).
- **Enterprise (future, Phase 3+).** Hosted endpoint with auth, multi-
  vendor BYO license, security-review-friendly deployment artifacts,
  support SLA. Defense / aerospace enterprise motion. Gated by pack-wide
  G3 trigger.

The Pro tier reaches break-even faster than TopicForge's Pro likely would,
because the DDS audience has higher willingness to pay than ROS2
hobbyists — but the absolute volume is smaller. Plan accordingly.

---

## 10. What to avoid

Inherit verbatim from `topicforge/CLAUDE.md §11` (no generic framework, no
UI, no hardcoded paths, tests have no external deps, no vague exceptions,
respect layer separation, never break mock mode). DdsForge-specific
additions:

- Do not publish anything onto the bus. Ever. There is no debug mode, no
  test mode, no override. The protocol does not even expose a write
  method. This is the safety contract.
- Do not depend on RTI being installable in the OSS test suite. RTI
  bindings are commercial and license-gated; CI runs without them. The
  OSS `cyclone` and `mock` modes must both stay green forever.
- Do not silently coerce QoS policies between vendors' enums. Mismatch
  detection compares against canonical DDS spec values, not RTI-specific
  or Cyclone-specific extensions. Vendor extensions get explicit handling
  with `# vendor: rti` / `# vendor: cyclone` comments.
- Do not chase the GUI consoles' feature parity. The wedge is
  MCP-native LLM grounding for diagnostic conversations, not a richer
  Python API or a richer UI.

---

## 11. Open questions (resolve before v0.1.0)

Deliberate gaps for the maintainer — not roadmap items, but pre-kickoff
decisions.

- **Package name.** `ddsforge` matches the pack pattern. Verify it is
  free on PyPI before committing. Fallbacks: `dds-inspector`,
  `dds-observer`, `forgedds`.
- **Repo layout.** Same monorepo as TopicForge, or separate repository?
  Separate is cleaner for the pack story and for CI hygiene; monorepo is
  faster to coordinate. Recommendation: **separate repository**
  (`github.com/yaniswav/DdsForge`), shared conventions via the future
  pack-template extraction described in `topicforge/docs/product-plan.md`
  §13.
- **`cyclonedds` version pin.** Determined at implementation kickoff
  after a smoke test against the current bindings on Windows + Linux.
- **QoS mismatch taxonomy.** Lock the policies checked at v0.1.0
  (recommended: Reliability, Durability, History, Deadline — the four
  that explain >80% of real-world "subscriber doesn't receive" cases).
  Defer Liveliness, Ownership, Partition, TimeBasedFilter, LatencyBudget
  to v0.1.x patches.
- **Pack-shared infrastructure.** v0.1.0 of DdsForge will duplicate
  TopicForge's telemetry / license / settings resolver. **Do not** lift
  to a shared package yet — that is MCP 03's job (DatasetForge will be
  the third user of the pattern, which is when extraction becomes
  worth it per the rule of three).

---

## 12. Why this is MCP 02 (decision log)

For the strategic record, 2026-05-13:

- The ROS-MCP category became crowded between mid-2025 and early 2026 —
  at least six projects (`robotmcp/ros-mcp-server` 1.2k stars, ROSBag MCP
  arXiv 2511.03497, `araitaiga/rosout_mcp`, `kakimochi`'s ROS 2 MCP,
  `lpigeon/ros_mcp_server`, `TakanariShimbo/rosbridge-mcp-server`).
  TopicForge needs an axis that is not "another ROS-MCP".
- The non-ROS DDS-MCP category is empty as of 2026-05-13. None of the
  six known ROS-MCP projects target non-ROS DDS users. Existing DDS
  tooling is vendor-specific GUI / CLI, not MCP-driven.
- The maintainer's background (Thales C++/DDS) is materially relevant —
  credibility on this axis is structural, not invented.
- Hook B (safety-first read-only — see `topicforge/docs/product-plan.md`
  §1) generalizes trivially from "ROS2 introspection" to "DDS
  observability". The architectural commitment is identical.
- The `RosAdapter` protocol generalizes to `MiddlewareAdapter` with zero
  rework — TopicForge's methods (`list_topics`, `get_topic_info`,
  `sample_messages`) are DDS-native concepts that ROS2 happens to expose.
- DatasetForge (the previous MCP 02 candidate) remains a strong product
  for the ML/CV audience. It slides to MCP 03; the spec is preserved
  verbatim under `mcp-03-spec.md` and is the third pack-MCP queued
  for implementation.

The right MCP 02 is DdsForge.

---

## 13. References

- `topicforge/CLAUDE.md` — operating manual; §3 / §5 / §6 / §7 / §8 / §11
  inherit verbatim.
- `topicforge/docs/product-plan.md` — pack vision (§4), monetization
  (§9), risk register (§11), decision gates G2/G3 (§12), DDS horizon (§8).
- `topicforge/docs/pro.md` — pricing terms reused for the Pro tier.
- `topicforge/.claude/skills/topicforge/` — `add-mcp-tool`,
  `write-pure-parser`, `update-mock-fixtures`, `release-checklist` apply
  near-verbatim. Fork-and-tweak at MVP; lift to a shared template when
  MCP 03 starts.
- `docs/projet-file/mcp-03-spec.md` — DatasetForge spec (the next pack
  MCP after DdsForge).

---

## Reviewer notes (2026-05-13)

> This section was added by the spec-reviewer agent during the v0.1.2 prep
> review. It does not modify the spec body. Issues listed below must be
> resolved before or at implementation kickoff.

### Issue 1 — Wrong section reference in §11 (Repo layout)

**Location.** §11, "Repo layout" bullet, last sentence:
> "shared conventions via the future pack-template extraction described in
> `topicforge/docs/product-plan.md` **§13**"

**Problem.** `product-plan.md §13` is titled "Maintenance of this document"
and describes update cadence for the product-plan file itself. The
pack-template / shared-infrastructure extraction concept lives in
`product-plan.md` **§4** ("The first three MCPs are the rule-of-three
trigger for extracting pack-shared infrastructure (telemetry, license,
settings resolver) into a separate template repo.").

**Suggested resolution.** Change `§13` to `§4` in the Repo layout bullet.
