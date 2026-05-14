# TopicForge — Product Plan

> Strategic source of truth for TopicForge. Vision, target users, phased roadmap, monetization, and risk register. Versioned. Updated when a phase ships or a decision gate triggers.

---

## 1. Identity

TopicForge is **the safety-first read-only MCP for ROS2 robotics**. Where general-purpose ROS-MCP servers let an LLM publish topics, call services, and command robots — useful for demos, untenable for production fleets, defense systems, or anything safety-certified — TopicForge is read-only by **architecture**, not by configuration. There is no write path to misconfigure, no permission system to audit, no liability conversation to have. The MCP client can see the robot stack; it cannot touch it.

Concretely, the server exposes five typed tools today — `health_check`, `list_topics`, `get_topic_info`, `sample_messages`, `analyze_bag` — backed by either a deterministic mock adapter (no ROS2 required) or a `ros2` CLI wrapper (full live introspection). Outputs are frozen Pydantic schemas, stable across runtime modes. Telemetry is opt-in, six fields, zero user payload.

The ROS-MCP category is no longer empty (see §11 Risk register for the competitive landscape as of 2026-05-13). What TopicForge defends, and the rest of the pack will inherit, is the read-only-by-architecture stance and the production-quality engineering envelope around it — frozen schemas, mock-first development, telemetry contract pinned by tests, Windows-first cross-platform, no shell injection, deterministic outputs.

TopicForge is also **MCP 01 of a 2-MCP pack**: TopicForge itself (this product, an umbrella that covers ROS2 today and will grow a DDS observability module — module spec at `docs/projet-file/mcp-02-spec.md`) and **DatasetForge** (Vision Dataset Inspector, the standalone second product — spec at `docs/projet-file/mcp-03-spec.md`). All inherit the read-only-by-architecture rule. The conventions established here — Pydantic schemas with `extra="forbid"` and `frozen=True`, adapter protocol, mock-first development, opt-in telemetry — are the template for the rest of the pack.

---

## 2. Why now

LLM agents reason fluently over text and code, but ROS2 introspection lives in a CLI + DDS world they cannot directly reach. Without grounding, an agent asked about a robot's topics will hallucinate topic names, message types, and bag contents — and a downstream user will not always notice until the suggestion fails on real hardware. The MCP standard is the bridge: a stable contract through which the agent gets structured ground truth from the robot stack instead of guessing. TopicForge is the implementation of that bridge for ROS2.

The window is real. MCP adoption is growing in 2026 (Claude Desktop, Claude Code, Cursor, Continue, Cline all speak it). Anthropic's MCP registry is expected to formalize discovery. Robotics teams are exactly the kind of audience that combines high stakes (real hardware), high tedium (`ros2 topic ...` invocations), and high LLM curiosity (Claude as a copilot for ROS pipelines). The category of "MCP for robotics" is not yet crowded.

---

## 3. Target users

Three concentric circles, ranked by strategic priority rather than acquisition cost.

**Strategic core.** Safety-conscious robotics teams: industrial integrators, automotive (AUTOSAR Adaptive surfaces overlap with ROS2 in dev/sim), aerospace simulation, naval, and defense teams that already evaluate AI tooling but cannot accept a write path into a production robot. The read-only-by-architecture stance is a sales asset — it short-circuits the security review that kills hosted control servers. Free tier captures the developer inside the team; Pro tier targets the team-level buyer. The DDS module roadmapped in §4 / §8 covers the same audience under one product surface — no separate `DdsForge` install, no second contract. This audience is explicitly the strategic anchor of the product, and the reason hook B over hook A or C (see decision log).

**Tactical adjacent.** Small robotics and ML/CV teams (3 to 20 engineers) where the lead is a Claude power user and wants the team's AI tooling to share a grounded view of the stack — without the OSS-control servers being the default answer because of their write-path implications. Pro tier (URDF inspection, bag anomaly detection, multi-bag diff) targets this segment first, at a price point ($12 early / $19/month standard) that fits an individual line item rather than a procurement cycle.

**Individual developer (free tier funnel).** Solo ROS2 developers and robotics ML/CV engineers who already use Claude Desktop or Claude Code daily. They want grounded introspection without bringing up a full rosbridge stack. Free tier captures them. They are the audience for the GitHub README, Reddit launches (r/ROS, r/ClaudeAI, r/robotics), and the 30-second mock-mode demo. Volume on this circle drives discoverability for the other two.

---

## 4. The pack vision

The strategic bet is **pack breadth via two focused products plus a modular surface inside TopicForge**. Two MCPs, not three to five — solo maintenance cost was the binding constraint and the 2026-05-14 audit collapsed the earlier 3-to-5-MCP plan accordingly.

**MCP 01 — TopicForge umbrella.** Covers ROS2 introspection today (shipped v0.1.2) and DDS observability as the next module (roadmapped, see §8). The `RosAdapter` protocol generalizes into a `MiddlewareAdapter` protocol that supports CycloneDDS in the OSS core and RTI Connext under the existing `topicforge_pro` license-gated package. One install (`pip install topicforge`, optional extras for DDS), one CLI, one license key — the umbrella keeps the developer ergonomics tight while extending coverage to the DDS-native audience the ROS-MCP competitive set does not reach. Module spec at `docs/projet-file/mcp-02-spec.md`.

**MCP 02 — DatasetForge.** Vision Dataset Inspector. Read images + annotations (COCO at MVP; YOLO / HF Datasets on roadmap) and answer structured questions about class balance, split coherence, annotation quality. Targets the ML/CV audience overlapping with TopicForge but distinct enough in domain (training data vs runtime graph) to warrant a separate product, separate repo, separate PyPI name. Full spec at `docs/projet-file/mcp-03-spec.md` (the file is still named `mcp-03-spec.md` for historical continuity ; the slot is MCP 02 of the 2-MCP pack).

**Motif of the pivot.** Earlier drafts of this plan sequenced a 3-to-5-MCP pack with DdsForge as a separate MCP 02 standalone product. The 2026-05-14 audit collapsed that into a 2-product strategy: TopicForge as an umbrella covering both middlewares, DatasetForge as the second standalone product. The binding constraints were (a) solo-maintenance cost of running two repos in parallel and (b) the fact that ROS2 and DDS are the same problem shape — a typed pub/sub graph that needs structured introspection — and the `RosAdapter` protocol already generalizes to a `MiddlewareAdapter` superset with zero rework. Two products instead of three reduces the surface area without losing coverage.

The umbrella commits TopicForge to a slightly broader scope (cap: 5 ROS2 tools today + at most 3 DDS-side tools when the module ships, ceiling enforced by §11). The pack inherits the layer separation, mock-first development, opt-in telemetry, and read-only-by-architecture commitments from TopicForge. Pack-shared infrastructure extraction (telemetry, license, settings resolver into a `pack-template/` repo) becomes a non-decision at 2 products: fork-and-tweak from TopicForge to DatasetForge is acceptable ; revisit only if a third product is ever planned.

---

## 5. Phase 1 — Foundations (in progress)

**Done:**

- v0.1.0 (2026-05-12) — MVP shipped on PyPI. Five MCP tools, mock + live CLI adapters, full Pydantic schemas, ruff + pytest, CI on Python 3.11 + 3.12, GitHub Action publishing on tag `v*`.
- v0.1.1 (2026-05-13) — Opt-in anonymous telemetry behind `TOPICFORGE_TELEMETRY=on`. Six-field event payload (`tool_name`, `latency_ms`, `mode`, `version`, `session_id`, `success`), pluggable transport, structured-log default, OFF-means-no-network pinned by unit test.
- v0.1.2 (2026-05-13) — `sample_messages` in live mode returns real publish-time timestamps for `Header`-stamped messages via `ros2 topic echo --csv --once` and the new `parse_csv_echo` pure parser; headerless types still return `0` (rmw receive timestamps remain a roadmap item tied to the `rclpy`-backed adapter). Every tool response now carries `mode_effective: Literal["mock", "live"]` (`TopicInfo`, `SampleResult`, `BagAnalysis`), backed by a new `effective_mode` property on the `RosAdapter` protocol — soft-breaking on the producer side, additive over the wire. Strategic specs drafted in the same prep window for both pack-mates: see current state at `docs/projet-file/mcp-02-spec.md` (TopicForge DDS module spec — reframed from a DdsForge standalone draft to a module-internal spec by the 2026-05-14 pivot) and `docs/projet-file/mcp-03-spec.md` (DatasetForge, slot now MCP 02 of the 2-product pack).
- Live mode validated end-to-end against ROS2 Jazzy on a developer workstation.

**Remaining for Phase 1:**

- `rclpy`-backed live adapter behind the same `RosAdapter` protocol. Returns native typed payloads, exposes per-message **rmw receive timestamps** (the missing piece for headerless message types after v0.1.2's `header.stamp` extraction), supports windowed echo. Lazy import — if `rclpy` is not installable on the host, the CLI adapter remains the fallback. See `.claude/skills/topicforge/add-ros2-adapter/SKILL.md`. Decision: do not start until at least one external user explicitly asks for it (pack breadth > MCP depth).
- Native `.mcap` reader for richer bag analysis (replaces `ros2 bag info` text parsing).
- Windowed and time-range sampling for `sample_messages` (depends on `rclpy` adapter).
- Server-side telemetry endpoint. The `Transport` callable is already pluggable; this is the day Fly.io / S3 lands.
- Hardening pass: improved error messages, performance budgets, additional cross-distro parser robustness (`parse_topic_list` etc. on Iron / Kilted).

**Audit-driven v0.3 candidates (from 2026-05-14 audits).** Sourced from `docs/projet-file/audit-followup-triage-v0.2.0.md`. Each item is a B-classified follow-up from the v0.1.2 security or architecture audit ; bundled here so the v0.3+ plan has the full picture in one place.

- **Security hardening (deferred ; all hosted-context-only or future-adapter-only).**
  - `TOPICFORGE_ROS2_BIN` allowlist for hosted multi-tenant contexts (security audit "Hardening" #1).
  - Scrubbed `subprocess.run(env=...)` instead of inheriting the full parent env (security audit "Hardening" #2).
  - `analyze_bag` workspace-root sandbox with `--workspace-root` allowlist (security audit "Hardening" #3).
  - `_validate_bag_path` Path.resolve traversal rejection, bundled with the workspace-root work (security audit "Hardening" #4).
  - Stricter `stderr_tail` sanitization once a user-supplied-command adapter is on the table (security audit "Hardening" #5).
  - Security audit's "Roadmap v0.3+" section — 5 additional items covering sandboxed `analyze_bag`, the `TOPICFORGE_ROS2_BIN_ALLOWLIST` env, env-scrub for subprocess, and signed `topicforge_pro` plugin entry point.
- **Architecture refactors (deferred ; design or wire-contract decisions).**
  - Collapse `Mode` / `ResolvedMode` / `AdapterName` into a unified `RuntimeMode` hierarchy (architecture audit "Refactor" #3).
  - Tighten `HealthReport.mode` / `requested_mode` from `str` to `Literal` (architecture audit "Refactor" #4 ; wire soft-breaking, plan with v0.3 contract review).
  - DDS topic-name regex relaxation to accept `::` separators and DDS-native shapes — couples with the real `CycloneDdsAdapter` implementation in v0.2.x (architecture audit "Refactor" #5 ; inline `TODO(roadmap, audit-2026-05-14)` in `services/inspector.py`).
  - Inspector validation symmetry across pass-through tools — review when new tools land (architecture audit "Refactor" #7 ; inline `TODO(roadmap, audit-2026-05-14)` in `services/inspector.py`).

Each Phase 1 item retires its matching `# TODO(roadmap):` marker in the code when it ships.

---

## 6. Phase 2 — Pro tier + pack growth

Phase 2 starts when Phase 1 is feature-complete and ten Pro early-access slots are reserved (see `docs/pro.md`). The two work streams run in parallel.

**Pro tier (`topicforge_pro` package).** Auto-detected at runtime via `_try_register_pro(mcp)` in `server/app.py`; license-gated via `TOPICFORGE_LICENSE_KEY` (consumed only by the Pro package). The three headline features, all read-only:

- **URDF Inspector.** Parse `.urdf` / `.xacro` files, surface structural failure modes (zero inertias, self-collisions, broken `mesh://` paths, dangling parents). Lets an agent reason about kinematics before touching a controller.
- **Bag Anomaly Detector.** Statistical + rule-based scan of `.mcap` / `.db3` recordings: clock jumps, frame drops, TF tree breaks, frequency drift, stale transforms, sensor desync. Returns a ranked list of anomalies with `(timestamp, severity, topic, evidence)`.
- **Multi-bag Diff.** Compare two recordings from the same scenario (before/after, sim vs real, two hardware revisions) and surface meaningful deltas — the diff most teams currently produce with throwaway Python.

Pricing terms in `docs/pro.md`: $12/month locked in for life for the first ten customers, $19/month afterward. No payment is collected until at least one Pro feature is in customers' hands; if demand is not there, the OSS MVP stays the product.

**Pack growth.** MCP 02 ships in Phase 2. Convention reuse is the metric: each new MCP should reach a useful MVP in two weeks of part-time work by importing the patterns established here (adapter protocol, mock-first, opt-in telemetry, frozen Pydantic schemas, `make check`). If a new MCP costs significantly more, the template needs work, not the MCP.

---

## 7. Phase 3 — Hosted, marketplace, ecosystem

Phase 3 starts when at least one Pro feature has shipped and the pack has three MCPs. Targets:

- **Hosted MCP endpoint** with auth, for teams that cannot run a subprocess on every developer's laptop. Requires path isolation and a stricter `TOPICFORGE_ROS2_BIN` policy (currently the server trusts whatever path the user provides — fine for local trust, not for hosted).
- **Marketplace presence:** MCPize Pro listing, Apify Store entry, Anthropic registry submission when it opens. Fiche templates owned by the `docs-curator` agent.
- **Cross-MCP shared infrastructure:** pack-wide telemetry endpoint, shared license server, shared CLI for installing the full pack.
- **Selected community contributions:** parsers for additional distros, additional dataset formats for the Vision Inspector, etc. Maintainer rules unwritten so far.

---

## 8. Horizons — the DDS module roadmap

After the 2026-05-14 mono-MCP pivot, DDS is no longer a long-term horizon — it is a roadmapped module of TopicForge with concrete phasing. The read-only-by-architecture stance generalizes cleanly from ROS2 introspection to bare DDS observability, which is exactly what makes TopicForge legible to defense, aerospace, automotive AUTOSAR Adaptive, naval, and industrial stacks. Public marketing of the DDS scope stays muted until the module actually ships (no enterprise pricing page, no defense-flavored landing), but the architectural work begins in Phase 1.

**Phasing.**

- **Phase 1 (v0.2.0 TopicForge).** Generalize the `RosAdapter` protocol into a `MiddlewareAdapter` superset (`adapters/base.py`). `RosAdapter` becomes an alias / sub-shape of `MiddlewareAdapter`. Ship `MockMiddlewareAdapter` with deterministic two-participant DDS fixtures (one well-matched topic, one deliberately mismatched). No new MCP tools exposed yet — protocol prep only.
- **Phase 2 (v0.3.0 TopicForge).** `CycloneDdsAdapter` shipped in OSS via an optional install extra: `pip install topicforge[dds]` brings the `cyclonedds` Python bindings (BSD-licensed). Three new MCP tools added to the TopicForge surface: `list_participants`, `detect_qos_mismatches`, `peek_dds_samples`. `health_check` and `list_topics` extended to expose middleware-side info when the module is active. Phase 2 also fits Pro work in parallel (URDF Inspector etc. — see §6).
- **Phase 3 (v0.4.0+).** `RtiConnextAdapter` ships under `topicforge_pro` (BYO RTI Connext license, gated by `TOPICFORGE_LICENSE_KEY`). `FastDdsAdapter` evaluated only on explicit user demand. OpenSplice is EOL — not pursued.

**Enterprise sales caveat.** Defense and aerospace are interested but require enterprise motion (RFPs, security review, export controls). Pursue only after three or more open-source logos in the broader DDS user base (non-ROS) validate the positioning. Do not approach defense primes cold ; let the open-source CycloneDDS traction surface the inbound. Gated by Gate G3 in §12.

---

## 9. Monetization

Three tiers, layered:

- **Free (MIT).** The MVP. `pip install topicforge`. No upsell in the README beyond a single line at the bottom pointing to Pro. Free is the lead magnet, not a teaser.
- **Pro (commercial license, separate package).** $12/month for the first ten early-access customers, $19/month afterward. Cancel anytime. License gating is environment-variable based (`TOPICFORGE_LICENSE_KEY`); the Pro package fails closed on missing or invalid keys. Pricing terms and disclaimers in `docs/pro.md`.
- **Enterprise (future, not before Phase 3).** Hosted endpoint, BYO commercial DDS, multi-seat licensing, support SLA. Triggered only by inbound interest with budget; no outbound until then.

The mental model is "indie SaaS with a credible Pro tier", not "open source with an enterprise gate". The Pro features must be features no one would mistake for a paywalled rewrap of the OSS MVP.

---

## 10. Distribution

Channels, in order of priority:

- **PyPI.** Primary. `topicforge` package, auto-published on tag `v*` via OIDC Trusted Publisher (`.github/workflows/publish.yml`). Pro package distributed separately.
- **GitHub.** README is the conversion surface for the open-source funnel. Loom demo embedded, badges, install snippet, claude_desktop_config example.
- **MCPize.** First marketplace. Free listing + Pro tier listing once Pro is shipping.
- **Apify Store.** Robotics is not their core but the audience overlap with synthetic data / web scraping is real.
- **Anthropic MCP registry.** Apply when it opens. Likely the highest-leverage channel of the bunch once it exists.
- **Reddit / LinkedIn / X.** Launch posts per release. Format and tone owned by the `docs-curator` agent — honest, non-corporate, "I built this for myself".

---

## 11. Risk register

The risks worth tracking explicitly. Updated 2026-05-13 with the competitive landscape audit.

- **Competitive landscape (NEW, 2026-05-13).** The ROS-MCP category is no longer empty. Known projects, by relevance:
  - `robotmcp/ros-mcp-server` (1.2k stars, Apache 2.0, on PyPI as `ros-mcp`, v3.0.1): rosbridge-based, **write path**, topics/services/actions/parameters, ROS 1+2, viral demos (Isaac Sim, Unitree). The incumbent and the one we are not.
  - "ROSBag MCP Server" (arXiv 2511.03497, Nov 2025): wraps `ros2 bag list` / `ros2 bag info`, analyzes trajectories, scans, transforms, time series. Academic credibility; bag analysis is *not* an empty niche.
  - `araitaiga/rosout_mcp`, `TakanariShimbo/rosbridge-mcp-server`, `lpigeon/ros_mcp_server`, kakimochi's ROS 2 MCP: smaller projects covering overlapping ground.
  - Open Robotics Cloud Robotics WG has discussed ROSBag MCP publicly (2025-09-24).

  Mitigation: TopicForge's defensible angle is **read-only by architecture** (no write path that even *can* misconfigure), and the safety-first positioning that follows from it (§1, §3 strategic core). Bag analysis remains useful in the tool surface but is not the headline. The DDS module (§4 umbrella, §8 phasing) is the long-term moat the competitors cannot easily match — and after the 2026-05-14 mono-MCP pivot, DDS coverage is a TopicForge module rather than a separate product, which keeps the moat under a single install.

- **Positioning collapse.** The biggest non-technical risk: shipping with a hook that is feature-parity-with-robotmcp-minus-write-path. That looks worse than them and competes on volume we will lose. Mitigation: the README and product-plan §1 must lead with safety-first, not with "ground truth for ROS". Audit every release for hook drift.
- **MCP standard churn.** Mitigation: pin `mcp >= 1.0.0` and follow the FastMCP API. Major MCP version bumps will require coordinated releases; CHANGELOG signals breaking changes.
- **ROS2 CLI output drift across distros.** Pure parsers exist precisely so a new distro is a parser tweak, not an adapter rewrite. `.claude/skills/topicforge/write-pure-parser/SKILL.md` codifies the convention. `parse_echo_yaml` in particular is brittle and is the parser most likely to regress on Iron / Kilted.
- **Insufficient Pro tier demand.** Mitigation: no Pro feature ships until ten teams sign up (terms in `docs/pro.md`). If demand stalls, the MVP stays the product. No sunk cost on unshipped paywall features.
- **Cross-platform regressions on Windows.** TopicForge's primary developer environment is Windows. The Makefile uses POSIX shell syntax; users on plain PowerShell need the documented escape hatches. Mitigation: tested directly in CI on `ubuntu-latest` only today; Windows coverage is documented in `docs/TESTING.md` and exercised manually before each release.
- **Telemetry trust.** Even opt-in telemetry can damage trust if the payload contract drifts. Mitigation: `tests/test_telemetry.py::test_payload_contains_only_whitelisted_keys` pins the six allowed keys. Any change requires a CHANGELOG entry and a README Telemetry section update in the same PR.
- **Time / focus dilution.** A solo maintainer trying to drive two products (TopicForge umbrella + DatasetForge), a Pro tier inside each, marketing, and the DDS module on top of TopicForge is the realistic risk. The 2026-05-14 pivot from a 3-to-5-MCP pack to a 2-product strategy reduced the surface but did not eliminate the risk. Mitigation: explicit phase gates (do not start Phase 2 until Phase 1 is shipped, do not act on the DDS module marketing until Phase 2 has shipped) — though §8 schedules `MiddlewareAdapter` protocol prep during Phase 1.
- **Scope creep within the TopicForge umbrella.** Combining ROS2 + DDS introspection in one product risks bloating the tool surface beyond what a focused MCP should expose. Mitigation: tool surface stays capped at the 5 ROS2 tools today ; the DDS module adds at most 3 new tools (`list_participants`, `detect_qos_mismatches`, `peek_dds_samples`) when it ships. Any 9th tool needs an explicit re-scope discussion documented in this register before code lands.

---

## 12. Decision gates

Three explicit gates, designed so a "no" stops the corresponding workstream cleanly.

- **Gate G1 — Phase 1 → Phase 2.** Triggered when v0.1.x has shipped all Phase 1 items (rclpy adapter, native MCAP reader, windowed sampling, telemetry endpoint) AND ten Pro early-access slots are reserved. If reservation count stalls below five for two months after the Pro page launch, hold Phase 2 and reassess Pro positioning instead of building features.
- **Gate G2 — Pack expansion.** Triggered when TopicForge has stable PyPI weekly install counts above a threshold (target: 100/week sustained over a month) AND the MCP 02 spec is ratified. Below the threshold, do not start MCP 02; the issue is reach, not surface area.
- **Gate G3 — DDS horizon activation.** Triggered when at least three open-source logos (named teams using TopicForge or a pack MCP and willing to be cited) AND a credible enterprise inbound (defense / aero with budget) arrive in the same quarter. Below this, the DDS abstraction stays internal — architectural prep only, no marketing, no pricing, no public roadmap update.

The gates are not aspirational. A workstream that has not cleared its gate gets explicitly paused — the maintainer's time is the scarcest resource, not ideas.

---

## 13. Maintenance of this document

This file is the canonical strategic plan. It is referenced from `README.md`, `CLAUDE.md`, `docs/pro.md`, `.claude/agents/docs-curator.md`, `.claude/agents/qa-reviewer.md`, and the `release-checklist` skill. Drift between this plan and the code or the user-facing docs is treated as a `docs-curator` task per `CLAUDE.md` §10.

Update cadence: revise at every minor version release (`v0.X.0`) and after every decision gate trigger. Smaller patches (`v0.x.Y`) do not require an update here. Roadmap markers in code (`# TODO(roadmap): <topic>`) point to entries in this file by topic — when a marker is retired, the corresponding line here should be updated to reflect the new state.
