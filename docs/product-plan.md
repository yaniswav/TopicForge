# TopicForge — Product Plan

> Strategic source of truth for TopicForge. Vision, target users, phased roadmap, monetization, and risk register. Versioned. Updated when a phase ships or a decision gate triggers.

---

## 1. Identity

TopicForge is **the safety-first read-only MCP for ROS2 robotics**. Where general-purpose ROS-MCP servers let an LLM publish topics, call services, and command robots — useful for demos, untenable for production fleets, defense systems, or anything safety-certified — TopicForge is read-only by **architecture**, not by configuration. There is no write path to misconfigure, no permission system to audit, no liability conversation to have. The MCP client can see the robot stack; it cannot touch it.

Concretely, the server exposes five typed tools today — `health_check`, `list_topics`, `get_topic_info`, `sample_messages`, `analyze_bag` — backed by either a deterministic mock adapter (no ROS2 required) or a `ros2` CLI wrapper (full live introspection). Outputs are frozen Pydantic schemas, stable across runtime modes. Telemetry is opt-in, six fields, zero user payload.

The ROS-MCP category is no longer empty (see §11 Risk register for the competitive landscape as of 2026-05-13). What TopicForge defends, and the rest of the pack will inherit, is the read-only-by-architecture stance and the production-quality engineering envelope around it — frozen schemas, mock-first development, telemetry contract pinned by tests, Windows-first cross-platform, no shell injection, deterministic outputs.

TopicForge is also **MCP 01 of a planned robotics/CV pack** of three to five MCPs (next: DdsForge; then DatasetForge; candidates: Synthetic Data Pipeline Controller, URDF Generator/Validator, Robotics Trajectory Visualizer). All inherit the read-only-by-architecture rule. The conventions established here — Pydantic schemas with `extra="forbid"` and `frozen=True`, adapter protocol, mock-first development, opt-in telemetry — are the template for the rest of the pack.

---

## 2. Why now

LLM agents reason fluently over text and code, but ROS2 introspection lives in a CLI + DDS world they cannot directly reach. Without grounding, an agent asked about a robot's topics will hallucinate topic names, message types, and bag contents — and a downstream user will not always notice until the suggestion fails on real hardware. The MCP standard is the bridge: a stable contract through which the agent gets structured ground truth from the robot stack instead of guessing. TopicForge is the implementation of that bridge for ROS2.

The window is real. MCP adoption is growing in 2026 (Claude Desktop, Claude Code, Cursor, Continue, Cline all speak it). Anthropic's MCP registry is expected to formalize discovery. Robotics teams are exactly the kind of audience that combines high stakes (real hardware), high tedium (`ros2 topic ...` invocations), and high LLM curiosity (Claude as a copilot for ROS pipelines). The category of "MCP for robotics" is not yet crowded.

---

## 3. Target users

Three concentric circles, ranked by strategic priority rather than acquisition cost.

**Strategic core.** Safety-conscious robotics teams: industrial integrators, automotive (AUTOSAR Adaptive surfaces overlap with ROS2 in dev/sim), aerospace simulation, naval, and defense teams that already evaluate AI tooling but cannot accept a write path into a production robot. The read-only-by-architecture stance is a sales asset — it short-circuits the security review that kills hosted control servers. Free tier captures the developer inside the team; Pro tier and the future DDS expansion (§8) target the team-level buyer. This audience is explicitly the strategic anchor of the product, and the reason hook B over hook A or C (see decision log).

**Tactical adjacent.** Small robotics and ML/CV teams (3 to 20 engineers) where the lead is a Claude power user and wants the team's AI tooling to share a grounded view of the stack — without the OSS-control servers being the default answer because of their write-path implications. Pro tier (URDF inspection, bag anomaly detection, multi-bag diff) targets this segment first, at a price point ($12 early / $19/month standard) that fits an individual line item rather than a procurement cycle.

**Individual developer (free tier funnel).** Solo ROS2 developers and robotics ML/CV engineers who already use Claude Desktop or Claude Code daily. They want grounded introspection without bringing up a full rosbridge stack. Free tier captures them. They are the audience for the GitHub README, Reddit launches (r/ROS, r/ClaudeAI, r/robotics), and the 30-second mock-mode demo. Volume on this circle drives discoverability for the other two.

---

## 4. The pack vision

The strategic bet is **pack breadth over single-MCP depth**. Three to five focused MCPs serving overlapping robotics, middleware, and CV audiences compound: one user installs one tool, returns for the next, and after three the user is locked into the ecosystem. The alternative — driving TopicForge to feature parity with an `rclpy` SDK — is more familiar engineering work but loses to the bigger story and runs straight into the crowded ROS-MCP space (§11 competitive landscape).

MCP 01 — TopicForge — is shipping (read-only ROS2 introspection).

**MCP 02 — DdsForge.** Locked 2026-05-13. Safety-first read-only DDS observability across middleware vendors (CycloneDDS in OSS, RTI Connext in Pro tier). Five tools: `health_check`, `list_participants`, `list_topics`, `detect_qos_mismatches`, `peek_samples`. Targets the DDS-native audience (defense, aerospace, automotive AUTOSAR Adaptive, naval, industrial) that the ROS-MCP competitive set does not address. Full spec lives in `docs/projet-file/mcp-02-spec.md`. The `RosAdapter` protocol generalizes to `MiddlewareAdapter` cleanly — co-development cost with TopicForge is low.

**MCP 03 — DatasetForge.** Vision Dataset Inspector. Read images + annotations (COCO at MVP; YOLO / HF Datasets on roadmap) and answer structured questions about class balance, split coherence, annotation quality. Targets the ML/CV audience overlapping with TopicForge but distinct from DdsForge. Full spec preserved verbatim in `docs/projet-file/mcp-03-spec.md` (this spec was authored as MCP 02 before the competitive audit reassigned the slot).

**MCP 04 / 05 candidates (unranked).** URDF Generator / Validator, Synthetic Data Pipeline Controller (Blender / Gazebo / Isaac Sim), Robotics Trajectory Visualizer. Ranking and selection deferred until DdsForge ships and the pack-shared infrastructure extraction (per `MCP 03 §11` open questions) is done.

The pack is sequenced so each MCP attacks an uncrowded segment and inherits the layer separation, mock-first development, opt-in telemetry, and read-only-by-architecture commitments from TopicForge. The first three MCPs are the rule-of-three trigger for extracting pack-shared infrastructure (telemetry, license, settings resolver) into a separate template repo.

---

## 5. Phase 1 — Foundations (in progress)

**Done:**

- v0.1.0 (2026-05-12) — MVP shipped on PyPI. Five MCP tools, mock + live CLI adapters, full Pydantic schemas, ruff + pytest, CI on Python 3.11 + 3.12, GitHub Action publishing on tag `v*`.
- v0.1.1 (2026-05-13) — Opt-in anonymous telemetry behind `TOPICFORGE_TELEMETRY=on`. Six-field event payload (`tool_name`, `latency_ms`, `mode`, `version`, `session_id`, `success`), pluggable transport, structured-log default, OFF-means-no-network pinned by unit test.
- v0.1.2 (2026-05-13) — `sample_messages` in live mode returns real publish-time timestamps for `Header`-stamped messages via `ros2 topic echo --csv --once` and the new `parse_csv_echo` pure parser; headerless types still return `0` (rmw receive timestamps remain a roadmap item tied to the `rclpy`-backed adapter). Every tool response now carries `mode_effective: Literal["mock", "live"]` (`TopicInfo`, `SampleResult`, `BagAnalysis`), backed by a new `effective_mode` property on the `RosAdapter` protocol — soft-breaking on the producer side, additive over the wire. DdsForge (MCP 02) spec drafted at `docs/projet-file/mcp-02-spec.md`; DatasetForge (MCP 03) spec at `docs/projet-file/mcp-03-spec.md`.
- Live mode validated end-to-end against ROS2 Jazzy on a developer workstation.

**Remaining for Phase 1:**

- `rclpy`-backed live adapter behind the same `RosAdapter` protocol. Returns native typed payloads, exposes per-message **rmw receive timestamps** (the missing piece for headerless message types after v0.1.2's `header.stamp` extraction), supports windowed echo. Lazy import — if `rclpy` is not installable on the host, the CLI adapter remains the fallback. See `.claude/skills/topicforge/add-ros2-adapter/SKILL.md`. Decision: do not start until at least one external user explicitly asks for it (pack breadth > MCP depth).
- Native `.mcap` reader for richer bag analysis (replaces `ros2 bag info` text parsing).
- Windowed and time-range sampling for `sample_messages` (depends on `rclpy` adapter).
- Server-side telemetry endpoint. The `Transport` callable is already pluggable; this is the day Fly.io / S3 lands.
- Hardening pass: improved error messages, performance budgets, additional cross-distro parser robustness (`parse_topic_list` etc. on Iron / Kilted).

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

## 8. Horizons — the DDS-complete expansion

With the safety-first positioning (§1, §3 strategic core), the DDS expansion is **less a horizon and more a strategic continuation**. The read-only-by-architecture stance is exactly what makes TopicForge legible to defense, aerospace, automotive AUTOSAR Adaptive, and naval stacks — none of which would accept a rosbridge-style write path through an LLM. The marketing should stay quiet until Phase 2 has shipped (no enterprise pricing page, no defense-flavored landing), but the **architecture and persona work can begin during Phase 1**.

**Plan.** Generalize the `RosAdapter` abstraction into a `DdsAdapter` family. Ship `CycloneDdsAdapter` in open-source v0.x to address non-ROS DDS users. Reserve commercial DDS implementations (RTI Connext, Fast DDS Pro) for the `topicforge_pro` tier with BYO-license, aligned with the existing license-gated Pro architecture. OpenSplice is EOL — not pursued.

**Phasing.** Phase 1 keeps the abstraction layer compatible (this is already true — the `RosAdapter` protocol generalizes cleanly to a `DdsAdapter` superset). Phase 2 introduces the `DdsAdapter` protocol as a parallel to `RosAdapter` and ships `CycloneDdsAdapter` if at least one non-ROS user has asked. Phase 3 — and only Phase 3 — surfaces this in marketing and pricing.

**Enterprise sales caveat.** Defense and aerospace are interested but require enterprise motion (RFPs, security review, export controls). Pursue only after three or more open-source logos in the broader DDS user base (non-ROS) validate the positioning. Do not approach defense primes cold; let the open-source CycloneDDS traction surface the inbound.

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

  Mitigation: TopicForge's defensible angle is **read-only by architecture** (no write path that even *can* misconfigure), and the safety-first positioning that follows from it (§1, §3 strategic core). Bag analysis remains useful in the tool surface but is not the headline. The DDS expansion (§8) is the long-term moat the competitors cannot easily match.

- **Positioning collapse.** The biggest non-technical risk: shipping with a hook that is feature-parity-with-robotmcp-minus-write-path. That looks worse than them and competes on volume we will lose. Mitigation: the README and product-plan §1 must lead with safety-first, not with "ground truth for ROS". Audit every release for hook drift.
- **MCP standard churn.** Mitigation: pin `mcp >= 1.0.0` and follow the FastMCP API. Major MCP version bumps will require coordinated releases; CHANGELOG signals breaking changes.
- **ROS2 CLI output drift across distros.** Pure parsers exist precisely so a new distro is a parser tweak, not an adapter rewrite. `.claude/skills/topicforge/write-pure-parser/SKILL.md` codifies the convention. `parse_echo_yaml` in particular is brittle and is the parser most likely to regress on Iron / Kilted.
- **Insufficient Pro tier demand.** Mitigation: no Pro feature ships until ten teams sign up (terms in `docs/pro.md`). If demand stalls, the MVP stays the product. No sunk cost on unshipped paywall features.
- **Cross-platform regressions on Windows.** TopicForge's primary developer environment is Windows. The Makefile uses POSIX shell syntax; users on plain PowerShell need the documented escape hatches. Mitigation: tested directly in CI on `ubuntu-latest` only today; Windows coverage is documented in `docs/TESTING.md` and exercised manually before each release.
- **Telemetry trust.** Even opt-in telemetry can damage trust if the payload contract drifts. Mitigation: `tests/test_telemetry.py::test_payload_contains_only_whitelisted_keys` pins the six allowed keys. Any change requires a CHANGELOG entry and a README Telemetry section update in the same PR.
- **Time / focus dilution.** A solo maintainer trying to drive five MCPs, a Pro tier, marketing, and a future DDS pivot is the realistic risk. Mitigation: explicit phase gates (do not start Phase 2 until Phase 1 is shipped, do not act on the DDS horizon until Phase 2 has shipped) — though §8 now allows architectural prep for DDS during Phase 1.

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
