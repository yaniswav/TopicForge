# TopicForge: Product Plan

> Strategic source of truth for TopicForge. Vision, target users, phased roadmap, monetization, and risk register. Versioned. Updated when a phase ships or a decision gate triggers.

---

## 1. Identity

TopicForge is **the safety-first read-only MCP for ROS2 robotics**. Where general-purpose ROS-MCP servers let an LLM publish topics, call services, and command robots (useful for demos, untenable for production fleets, defense systems, or anything safety-certified), TopicForge is read-only by **architecture**, not by configuration. There is no write path to misconfigure, no permission system to audit, no liability conversation to have. The MCP client can see the robot stack; it cannot touch it.

Concretely, the server exposes **eleven typed read-only tools today** (v0.5.2): the five ROS2-graph tools (`health_check`, `list_topics`, `get_topic_info`, `sample_messages`, `analyze_bag`) plus the six DDS / observability tools shipped across v0.2.0-v0.4.0 (`list_participants`, `detect_qos_mismatches`, `peek_dds_samples`, `participant_events`, `topic_metrics`, `peek_bag_samples`). They are backed by a deterministic mock adapter (no ROS2/DDS required), a `ros2` CLI wrapper, or an OSS DDS participant (Eclipse CycloneDDS / eProsima Fast DDS). Outputs are frozen Pydantic schemas, stable across runtime modes. Telemetry is opt-in, six fields, zero user payload.

The ROS-MCP category is no longer empty (see section 11 Risk register for the competitive landscape as of 2026-05-13). What TopicForge defends, and the rest of the pack will inherit, is the read-only-by-architecture stance and the production-quality engineering envelope around it: frozen schemas, mock-first development, telemetry contract pinned by tests, Windows-first cross-platform, no shell injection, deterministic outputs.

TopicForge is also **MCP 01 of a 2-MCP pack**: TopicForge itself (this product, an umbrella that covers ROS2 today and will grow a DDS observability module; module spec at `docs/projet-file/mcp-02-spec.md`) and **DatasetForge** (Vision Dataset Inspector, the standalone second product; spec at `docs/projet-file/mcp-03-spec.md`). All inherit the read-only-by-architecture rule. The conventions established here (Pydantic schemas with `extra="forbid"` and `frozen=True`, adapter protocol, mock-first development, opt-in telemetry) are the template for the rest of the pack.

---

## 2. Why now

LLM agents reason fluently over text and code, but ROS2 introspection lives in a CLI + DDS world they cannot directly reach. Without grounding, an agent asked about a robot's topics will hallucinate topic names, message types, and bag contents, and a downstream user will not always notice until the suggestion fails on real hardware. The MCP standard is the bridge: a stable contract through which the agent gets structured ground truth from the robot stack instead of guessing. TopicForge is the implementation of that bridge for ROS2.

The window is real. MCP adoption is growing in 2026 (Claude Desktop, Claude Code, Cursor, Continue, Cline all speak it). Anthropic's MCP registry is expected to formalize discovery. Robotics teams are exactly the kind of audience that combines high stakes (real hardware), high tedium (`ros2 topic ...` invocations), and high LLM curiosity (Claude as a copilot for ROS pipelines). The category of "MCP for robotics" is not yet crowded.

---

## 3. Target users

Three concentric circles, ranked by strategic priority rather than acquisition cost.

**Strategic core.** Safety-conscious robotics teams: industrial integrators, automotive (AUTOSAR Adaptive surfaces overlap with ROS2 in dev/sim), aerospace simulation, naval, and defense teams that already evaluate AI tooling but cannot accept a write path into a production robot. The read-only-by-architecture stance is a sales asset: it short-circuits the security review that kills hosted control servers. Free tier captures the developer inside the team; the commercial-support tier (section 6, section 9) targets the team-level buyer that needs custom integration work. The DDS module roadmapped in section 4 / section 8 covers the same audience under one product surface: no separate DDS MCP install, no second contract. This audience is explicitly the strategic anchor of the product, and the reason hook B over hook A or C (see decision log).

**Tactical adjacent.** Small robotics and ML/CV teams (3 to 20 engineers) where the lead is a Claude power user and wants the team's AI tooling to share a grounded view of the stack, without the OSS-control servers being the default answer because of their write-path implications. The candidate diagnostic features once planned for this segment (URDF inspection, bag anomaly detection, multi-bag diff; section 6) no longer ship as a self-serve subscription tier, so this segment's near-term value capture runs through the free tier and word-of-mouth rather than a priced individual line item (section 9).

**Individual developer (free tier funnel).** Solo ROS2 developers and robotics ML/CV engineers who already use Claude Desktop or Claude Code daily. They want grounded introspection without bringing up a full rosbridge stack. Free tier captures them. They are the audience for the GitHub README, Reddit launches (r/ROS, r/ClaudeAI, r/robotics), and the 30-second mock-mode demo. Volume on this circle drives discoverability for the other two.

---

## 4. The pack vision

The strategic bet is **pack breadth via two focused products plus a modular surface inside TopicForge**. Two MCPs, not three to five: solo maintenance cost was the binding constraint and the 2026-05-14 audit collapsed the earlier 3-to-5-MCP plan accordingly.

**MCP 01: TopicForge umbrella.** Covers ROS2 introspection (shipped v0.1.2) and DDS observability, which **shipped as a module across v0.2.0-v0.4.0** (see section 8): no longer roadmapped. The `RosAdapter` protocol was generalized into a `MiddlewareAdapter` protocol that supports Eclipse CycloneDDS and eProsima Fast DDS in the OSS core; RTI Connext and OpenSplice coverage also exists, in the `topicforge_pro` package, offered through a commercial engagement rather than a subscription license key (section 6, section 9; the license-gated Pro subscription itself is abandoned as of 2026-09-05). One install (`pip install topicforge`, optional extras for DDS), one CLI: the umbrella keeps the developer ergonomics tight while extending coverage to the DDS-native audience the ROS-MCP competitive set does not reach. Module spec at `docs/projet-file/mcp-02-spec.md`.

**MCP 02: DatasetForge.** Vision Dataset Inspector. Read images + annotations (COCO at MVP; YOLO / HF Datasets on roadmap) and answer structured questions about class balance, split coherence, annotation quality. Targets the ML/CV audience overlapping with TopicForge but distinct enough in domain (training data vs runtime graph) to warrant a separate product, separate repo, separate PyPI name. Full spec at `docs/projet-file/mcp-03-spec.md` (the file is still named `mcp-03-spec.md` for historical continuity ; the slot is MCP 02 of the 2-MCP pack).

**Motif of the pivot.** Earlier drafts of this plan sequenced a 3-to-5-MCP pack with a separate DDS observability MCP as MCP 02. The 2026-05-14 audit collapsed that into a 2-product strategy: TopicForge as an umbrella covering both middlewares, DatasetForge as the second standalone product. The binding constraints were (a) solo-maintenance cost of running two repos in parallel and (b) the fact that ROS2 and DDS are the same problem shape (a typed pub/sub graph that needs structured introspection) and the `RosAdapter` protocol already generalizes to a `MiddlewareAdapter` superset with zero rework. Two products instead of three reduces the surface area without losing coverage.

The umbrella commits TopicForge to a broader scope than first drafted: the DDS module shipped **six** DDS / observability tools across v0.2.0-v0.4.0 (not the three originally scoped), taking the surface to **11 tools total**. The original 8-tool ceiling was formally revised: see the re-scope decision in section 11. The pack inherits the layer separation, mock-first development, opt-in telemetry, and read-only-by-architecture commitments from TopicForge. Pack-shared infrastructure extraction (telemetry, license, settings resolver into a `pack-template/` repo) becomes a non-decision at 2 products: fork-and-tweak from TopicForge to DatasetForge is acceptable ; revisit only if a third product is ever planned.

---

## 5. Phase 1: Foundations (in progress)

**Done:**

- v0.1.0 (2026-05-12): MVP shipped on PyPI. Five MCP tools, mock + live CLI adapters, full Pydantic schemas, ruff + pytest, CI on Python 3.11 + 3.12, GitHub Action publishing on tag `v*`.
- v0.1.1 (2026-05-13): Opt-in anonymous telemetry behind `TOPICFORGE_TELEMETRY=on`. Six-field event payload (`tool_name`, `latency_ms`, `mode`, `version`, `session_id`, `success`), pluggable transport, structured-log default, OFF-means-no-network pinned by unit test.
- v0.1.2 (2026-05-13): `sample_messages` in live mode returns real publish-time timestamps for `Header`-stamped messages via `ros2 topic echo --csv --once` and the new `parse_csv_echo` pure parser; headerless types still return `0` (rmw receive timestamps remain a roadmap item tied to the `rclpy`-backed adapter). Every tool response now carries `mode_effective: Literal["mock", "live"]` (`TopicInfo`, `SampleResult`, `BagAnalysis`), backed by a new `effective_mode` property on the `RosAdapter` protocol: soft-breaking on the producer side, additive over the wire. Strategic specs drafted in the same prep window for both pack-mates: see current state at `docs/projet-file/mcp-02-spec.md` (TopicForge DDS module spec; reframed from a standalone DDS-MCP draft to a module-internal spec by the 2026-05-14 pivot) and `docs/projet-file/mcp-03-spec.md` (DatasetForge, slot now MCP 02 of the 2-product pack).
- Live mode validated end-to-end against ROS2 Jazzy on a developer workstation.

**Remaining for Phase 1 (status confirmed unchanged as of 2026-09-05):**

- `rclpy`-backed live adapter behind the same `RosAdapter` protocol. Returns native typed payloads, exposes per-message **rmw receive timestamps** (the missing piece for headerless message types after v0.1.2's `header.stamp` extraction), supports windowed echo. Lazy import: if `rclpy` is not installable on the host, the CLI adapter remains the fallback. See `.claude/skills/topicforge/add-ros2-adapter/SKILL.md`. Decision: do not start until at least one external user explicitly asks for it (pack breadth > MCP depth).
- Native `.mcap` reader for richer bag analysis (replaces `ros2 bag info` text parsing).
- Windowed and time-range sampling for `sample_messages` (depends on `rclpy` adapter).
- Server-side telemetry endpoint. The `Transport` callable is already pluggable; this is the day Fly.io / S3 lands.
- Hardening pass: improved error messages, performance budgets, additional cross-distro parser robustness (`parse_topic_list` etc. on Iron / Kilted).

**Audit-driven v0.3 candidates (from 2026-05-14 audits).** Sourced from `docs/projet-file/audit-followup-triage-v0.2.0.md`, which is the canonical tracker. Each item is a B-classified follow-up from the v0.1.2 security or architecture audit. The audits number their own findings, so the B-labels are given here to bridge the two schemes; dispositions below were re-verified against the code on 2026-09-05.

- **Security hardening (deferred ; all hosted-context-only or future-adapter-only).**
  - `TOPICFORGE_ROS2_BIN` allowlist for hosted multi-tenant contexts (security audit "Hardening" #1 = B1; DEFER).
  - Scrubbed `subprocess.run(env=...)` instead of inheriting the full parent env (security audit "Hardening" #2 = B2; DEFER).
  - `analyze_bag` workspace-root sandbox with `--workspace-root` allowlist (security audit "Hardening" #3 = B3; DEFER).
  - `_validate_bag_path` Path.resolve traversal rejection, bundled with the workspace-root work (security audit "Hardening" #4 = B4; DEFER).
  - Stricter `stderr_tail` sanitization once a user-supplied-command adapter is on the table (security audit "Hardening" #5 = B5; DEFER).
  - Security audit's "Roadmap v0.3+" section: 5 additional items covering sandboxed `analyze_bag`, the `TOPICFORGE_ROS2_BIN_ALLOWLIST` env, env-scrub for subprocess, and signed `topicforge_pro` plugin entry point.
- **Architecture refactors, still open (wire-contract decisions).**
  - Collapse `Mode` / `ResolvedMode` / `AdapterName` into a unified `RuntimeMode` hierarchy (architecture audit "Refactor" #3 = B7; DEFER). Open question: which module owns the canonical Literal. Plan alongside B8 at the next wire-contract review.
  - Tighten `HealthReport.mode` / `requested_mode` from `str` to `Literal` (architecture audit "Refactor" #4 = B8; DEFER). Soft-breaking on the wire: strict clients validating against the v0.2.0 schema would need to regenerate.
- **Architecture refactors, closed. Kept for the audit trail, not work items.**
  - DDS topic-name regex relaxed to accept `::` and DDS-native shapes (architecture audit "Refactor" #5 = B9). **Closed in v0.3.0**: `_validate_topic_name_dds` ships at `services/inspector.py:51` with the resolving comment. The strict ROS2 validator stays for the five ROS2 graph methods.
  - Inspector validation symmetry across pass-through tools (architecture audit "Refactor" #7 = B10). **Closed in v0.5.0 as WONT-FIX by design**: `list_topics` takes no MCP-level arguments, so an Inspector-side gate would have nothing to validate. The asymmetry is structural. The inline TODO was retired and replaced by a permanent design note at `services/inspector.py:72`. Reopen only if a future variant ships with arguments to normalize.

Each Phase 1 item retires its matching `# TODO(roadmap):` marker in the code when it ships.

---

## 6. Phase 2: Commercial support + pack growth

**Revision (2026-09-05): the license-gated Pro subscription is abandoned.** The plan below priced three diagnostic features behind `TOPICFORGE_LICENSE_KEY` at $12 to $19 per month. Three findings closed that path:

- No verifiable public precedent of a standalone local MCP server sold on a recurring subscription enforced by a local license key.
- A local MCP server is a subprocess the client launches, so any client-side license check is bypassable by construction. This was not a hypothetical risk: the gate was never actually wired into the shipped code. `services/factory.py` never calls into `pro/topicforge_pro/license.py`; that module's skeleton parses a key and checks its validity window, but nothing in the request path consults it.
- The announced DDS-tier exclusivity does not hold. The free OSS core already observes any RTPS-conformant vendor on the bus, including RTI, through the standard builtin discovery topics, with no vendor-proprietary binding required (section 8).

Replacement: **commercial support and integration on request**, inbound-only, with no listed price. The strategic-core segment this product targets (defense, aerospace, automotive; section 3) buys custom integration work as a five-figure engagement, not a $12/month line item. The `topicforge_pro` package (the `RtiConnextAdapter`, an `OpenSpliceAdapter` legacy-support stub, `CoreDX`/`Intercom` placeholders, and the license-parsing skeleton in `license.py`) still exists in the repo; its commercial-vendor DDS coverage is now something a paid engagement draws on rather than a self-serve subscription SKU. `docs/pro.md` carries the current framing.

**Original plan (superseded; kept for record).** Phase 2 was to start when Phase 1 was feature-complete and ten Pro early-access slots were reserved. As measured 2026-08-31, reservations stood at 0 of 10 (`docs/projet-file/traction/latest-summary.md`; see also Gate G1, section 12), which is part of what prompted the pivot above rather than continuing to wait on that gate. The three headline features once planned behind the license gate, all read-only:

- **URDF Inspector.** Parse `.urdf` / `.xacro` files, surface structural failure modes (zero inertias, self-collisions, broken `mesh://` paths, dangling parents). Lets an agent reason about kinematics before touching a controller.
- **Bag Anomaly Detector.** Statistical + rule-based scan of `.mcap` / `.db3` recordings: clock jumps, frame drops, TF tree breaks, frequency drift, stale transforms, sensor desync. Returns a ranked list of anomalies with `(timestamp, severity, topic, evidence)`.
- **Multi-bag Diff.** Compare two recordings from the same scenario (before/after, sim vs real, two hardware revisions) and surface meaningful deltas: the diff most teams currently produce with throwaway Python.

These remain candidate deliverables for a commercial engagement rather than a shipped OSS or subscription feature.

**Pack growth.** MCP 02 ships in Phase 2. Convention reuse is the metric: each new MCP should reach a useful MVP in two weeks of part-time work by importing the patterns established here (adapter protocol, mock-first, opt-in telemetry, frozen Pydantic schemas, `make check`). If a new MCP costs significantly more, the template needs work, not the MCP.

---

## 7. Phase 3: Hosted, marketplace, ecosystem

Phase 3 starts when both pack products (TopicForge and DatasetForge) have shipped a stable minor release and at least one paid commercial-support engagement (section 6) has closed. (Revised 2026-09-05: the previous trigger, "at least one Pro feature has shipped and the pack has three MCPs," could never fire under the 2-product pack decided in section 4, and referenced the now-abandoned Pro subscription tier from section 6.) Targets:

- **Hosted MCP endpoint** with auth, for teams that cannot run a subprocess on every developer's laptop. Requires path isolation and a stricter `TOPICFORGE_ROS2_BIN` policy (currently the server trusts whatever path the user provides; fine for local trust, not for hosted).
- **Marketplace presence:** MCPize free listing (no Pro listing, see section 9), Apify Store entry; Anthropic MCP registry submission already done (listed since 2026-09-05, see section 10). Fiche templates owned by the `docs-curator` agent.
- **Cross-MCP shared infrastructure:** pack-wide telemetry endpoint, shared CLI for installing the full pack.
- **Selected community contributions:** parsers for additional distros, additional dataset formats for the Vision Inspector, etc. Maintainer rules unwritten so far.

---

## 8. Horizons: the DDS module roadmap

After the 2026-05-14 mono-MCP pivot, DDS observability shipped as a module of TopicForge across v0.2.0-v0.4.0 (see section 1, section 4). The read-only-by-architecture stance generalizes cleanly from ROS2 introspection to bare DDS observability, which is exactly what makes TopicForge legible to defense, aerospace, automotive AUTOSAR Adaptive, naval, and industrial stacks. Public marketing of the DDS scope stays muted (no enterprise pricing page, no defense-flavored landing) until the enterprise sales caveat below clears its gate.

**Phasing (shipped history, corrected 2026-09-05).**

- **Phase 1 (v0.2.0, 2026-05-14).** Generalized the `RosAdapter` protocol into a `MiddlewareAdapter` superset (`adapters/base.py`); `RosAdapter` retained as a backward-compat alias. Shipped `MockMiddlewareAdapter` fixtures and a protocol-compliant `CycloneDdsAdapter` stub: the lazy import, `is_available()`, and routing worked, but the 3 DDS methods raised a roadmap `AdapterError`. Three new MCP tools added: `list_participants`, `detect_qos_mismatches`, `peek_dds_samples`.
- **OSS multi-vendor (v0.3.0, 2026-05-14).** Real `CycloneDdsAdapter` and, in the same release, `FastDdsAdapter` shipped together (`pip install topicforge[dds-cyclone]` / `[dds-fast]` / `[dds]` for both as shipped at the time; since 0.5.3 only `[dds-cyclone]` and `[dds]`, which resolves to Cyclone, exist, because the `fastdds` binding is not on PyPI), both OSS and BSD-licensed, both implemented (615 and 581 lines respectively at the time, in `adapters/dds_cyclone/adapter.py` and `adapters/dds_fast/adapter.py`; neither has been run against a live bus). Framed as OMG DDS-RTPS multi-vendor observation: either OSS participant sees any conformant vendor on the bus (RTI Connext, OpenDDS, CoreDX, Dust DDS, etc.) through the standard builtin discovery topics, with no vendor-proprietary binding required. This vendor-agnostic reach through two OSS bindings is the actual moat, not a per-vendor adapter count.
- **v0.4.0 Phase 1.5 (2026-05-14 sprint, later hardening releases).** Added `OpenDdsAdapter` and `DustDdsAdapter` in OSS, both permanent stubs: `is_available()` always returns False, there is no maintained Python binding upstream for either, and the `[dds-opendds]` / `[dds-dust]` install extras failed at install time as of 2026-09-05 (removed in 0.5.3, together with `[dds-fast]` and `[dds-all-oss]`). Also scaffolded the `topicforge_pro` package (`RtiConnextAdapter`, an `OpenSpliceAdapter` legacy-support stub pointing existing OpenSplice users at direct-contact integration scoping, `CoreDX`/`Intercom` placeholders, and the `license.py` parsing skeleton). That package was the planned Pro delivery mechanism; the subscription pricing wrapped around it is retired (section 6, section 9). The code itself, and its RTI Connext / OpenSplice coverage, remains available through a paid commercial engagement.

- **v0.5.3 (2026-10-01), review fixes.** Removed the extras that referenced PyPI names nobody owns (`fastdds`, `dust-dds-python`; a dependency-confusion risk), removed the automatic `topicforge_pro` plugin hook and the commercial `TOPICFORGE_DDS_BACKEND` values, disabled user-topic payload decoding (it never worked), stated `topic_metrics`' real scope (builtin topics only), made `health_check` report the adapter actually built, honoured an explicit DDS backend without ROS 2, and lowered the Python floor to 3.10. Real-bus validation of the DDS adapters is still not done and remains the first item of open technical debt. Details in `CHANGELOG.md`.

**Enterprise sales caveat.** Defense and aerospace are interested but require enterprise motion (RFPs, security review, export controls). Pursue only after three or more open-source logos in the broader DDS user base (non-ROS) validate the positioning. Do not approach defense primes cold ; let the open-source CycloneDDS traction surface the inbound. Gated by Gate G3 in section 12 (not yet cleared as of the 2026-08-31 measurement).

---

## 9. Monetization

**Revision (2026-09-05): the license-gated Pro subscription tier is abandoned.** See section 6 for the full reasoning: no verifiable precedent for a subscription-priced standalone local MCP server, a local MCP server's client-side license check is bypassable by construction, the gate was never actually wired into `services/factory.py`, and the announced DDS-tier exclusivity does not hold since the free core already observes any RTPS-conformant vendor including RTI. Two tiers remain:

- **Free (MIT).** The MVP. `pip install topicforge`. No upsell in the README beyond a single line at the bottom. Free is the lead magnet, not a teaser.
- **Commercial support and integration (inbound-only, no listed price).** Custom engagements for teams that want dedicated support, integration work, or the commercial DDS vendor coverage that already exists in the `topicforge_pro` package (RTI Connext, OpenSplice legacy support). Sold as a scoped engagement, not a subscription; the strategic-core segment (defense, aerospace, automotive; section 3) buys this kind of work in five-figure engagements, not $12/month line items. No outbound until inbound interest with budget surfaces.

The mental model is "focused open-source tool with a direct line to the maintainer for teams that need more", not "open source with a subscription gate". The old three-tier plan (Free / $12-19/month Pro / future Enterprise) is retired; its Pro-tier feature ideas (URDF Inspector, Bag Anomaly Detector, Multi-bag Diff) are listed in section 6 as candidate engagement deliverables rather than a shipped SKU.

---

## 10. Distribution

Channels, in order of priority:

- **PyPI.** Primary. `topicforge` package, auto-published on tag `v*` via OIDC Trusted Publisher (`.github/workflows/publish.yml`). The `topicforge_pro` package is distributed separately, delivered as part of a commercial engagement rather than self-serve (section 6).
- **GitHub.** README is the conversion surface for the open-source funnel. Loom demo embedded, badges, install snippet, claude_desktop_config example.
- **MCPize.** First marketplace. Free listing only; no separate Pro-tier listing now that the subscription tier is abandoned (section 9).
- **Apify Store.** Robotics is not their core but the audience overlap with synthetic data / web scraping is real.
- **MCP registry (official, Anthropic-backed).** Listed since 2026-09-05 as `io.github.yaniswav/topicforge` v0.5.2, status active (`server.json`, ownership verified via the PyPI `mcp-name` marker shipped in v0.5.2). Caveat: an unrelated SEO product has occupied the `net.topicforge/mcp` namespace since July 2026, at v1.0.0, backed by the domain `topicforge.net`. The `io.github.yaniswav/topicforge` namespace and the `topicforge` PyPI package name are unambiguously ours, but a plain registry search for "topicforge" surfaces that unrelated product too (see section 11 risk register).
- **Reddit / LinkedIn / X.** Launch posts per release. Format and tone owned by the `docs-curator` agent: honest, non-corporate, "I built this for myself".

---

## 11. Risk register

The risks worth tracking explicitly. Updated 2026-09-05 (previous pass: 2026-05-13, competitive-landscape audit).

- **Competitive landscape (NEW, 2026-05-13).** The ROS-MCP category is no longer empty. Known projects, by relevance:
  - `robotmcp/ros-mcp-server` (1.2k stars, Apache 2.0, on PyPI as `ros-mcp`, v3.0.1): rosbridge-based, **write path**, topics/services/actions/parameters, ROS 1+2, viral demos (Isaac Sim, Unitree). The incumbent and the one we are not.
  - "ROSBag MCP Server" (arXiv 2511.03497, Nov 2025): wraps `ros2 bag list` / `ros2 bag info`, analyzes trajectories, scans, transforms, time series. Academic credibility; bag analysis is *not* an empty niche.
  - `araitaiga/rosout_mcp`, `TakanariShimbo/rosbridge-mcp-server`, `lpigeon/ros_mcp_server`, kakimochi's ROS 2 MCP: smaller projects covering overlapping ground.
  - Open Robotics Cloud Robotics WG has discussed ROSBag MCP publicly (2025-09-24).

  Mitigation: TopicForge's defensible angle is **read-only by architecture** (no write path that even *can* misconfigure), and the safety-first positioning that follows from it (section 1, section 3 strategic core). Bag analysis remains useful in the tool surface but is not the headline. The DDS module (section 4 umbrella, section 8 phasing) is the long-term moat the competitors cannot easily match, and after the 2026-05-14 mono-MCP pivot, DDS coverage is a TopicForge module rather than a separate product, which keeps the moat under a single install.

- **Positioning collapse.** The biggest non-technical risk: shipping with a hook that is feature-parity-with-robotmcp-minus-write-path. That looks worse than them and competes on volume we will lose. Mitigation: the README and product-plan section 1 must lead with safety-first, not with "ground truth for ROS". Audit every release for hook drift.
- **MCP standard churn -- realized, not hypothetical (updated 2026-09-05).** The `mcp` Python SDK's `2.0.0` release (2026-07-28, alongside the `2026-07-28` protocol revision) removed `mcp.server.fastmcp` entirely. `pyproject.toml`'s previously unbounded `mcp>=1.0.0` pin meant every fresh `pip install topicforge` resolved to `2.0.0` and produced a server that could not start (`from mcp.server.fastmcp import FastMCP` raised `ImportError`). TopicForge was uninstallable for 25 days (2026-07-28 to 2026-08-22) before anyone noticed: local development already had `mcp 1.27.1` installed, and CI had not run since before the SDK release. Fixed in v0.5.1 by pinning `mcp>=1.0.0,<2` (current, verified in `pyproject.toml`). Open: migrating to the 2.x API (`FastMCP` -> `MCPServer`, stateless protocol, transport options moved to `.run()`) is tracked separately and not yet started.
- **Registry name collision (new, 2026-09-05).** An unrelated SEO product has occupied the `net.topicforge/mcp` namespace in the official MCP Registry since July 2026, at v1.0.0, backed by the domain `topicforge.net`. TopicForge's own registry identity (`io.github.yaniswav/topicforge`) and the `topicforge` PyPI package name are both unambiguously owned and are distinct namespaces, but a plain registry search for "topicforge" surfaces the unrelated product alongside ours. No mitigation beyond the distinct, verified namespace; watch for user confusion in support channels.
- **ROS2 CLI output drift across distros.** Pure parsers exist precisely so a new distro is a parser tweak, not an adapter rewrite. `.claude/skills/topicforge/write-pure-parser/SKILL.md` codifies the convention. `parse_echo_yaml` in particular is brittle and is the parser most likely to regress on Iron / Kilted.
- **Insufficient demand for commercial engagements.** Retired as a Pro-subscription risk (2026-09-05; the subscription tier itself is abandoned, section 6, section 9). Residual form: if inbound commercial-support interest never materializes, the Phase 2 / Phase 3 gates (section 12) simply never trigger; no sunk cost, since nothing is built ahead of a signed engagement.
- **Cross-platform regressions on Windows -- CLOSED (2026-09-05).** Was: CI tested `ubuntu-latest` only, with Windows coverage manual-only before each release. Resolved in v0.5.0: `.github/workflows/ci.yml`'s matrix now runs `{ubuntu-latest, windows-latest} x {3.10, 3.11, 3.12, 3.13}` (3.10 added in 0.5.3, verified current). Residual, smaller risk: the Makefile still uses POSIX shell syntax, so users on plain PowerShell still need the documented escape hatches in `docs/TESTING.md`.
- **Telemetry trust.** Even opt-in telemetry can damage trust if the payload contract drifts. Mitigation: `tests/test_telemetry.py::test_payload_contains_only_whitelisted_keys` pins the six allowed keys. Any change requires a CHANGELOG entry and a README Telemetry section update in the same PR.
- **Time / focus dilution.** A solo maintainer trying to drive two products (TopicForge umbrella + DatasetForge), commercial-support work for each, marketing, and the DDS module on top of TopicForge is the realistic risk. The 2026-05-14 pivot from a 3-to-5-MCP pack to a 2-product strategy reduced the surface but did not eliminate the risk. Mitigation: explicit phase gates (do not start Phase 2 until Phase 1 is shipped, do not act on the DDS module marketing until Phase 2 has shipped), though section 8 schedules `MiddlewareAdapter` protocol prep during Phase 1.
- **Scope creep within the TopicForge umbrella.** Combining ROS2 + DDS introspection in one product risks bloating the tool surface beyond what a focused MCP should expose. **Re-scope decision (2026-07-08, ratified retroactively).** The register's original ceiling (5 ROS2 tools + at most 3 DDS tools, any 9th tool gated on a re-scope discussion documented *here* before code lands) was crossed during v0.4.0 **without that discussion being recorded in this register**, a governance gap surfaced by the 2026-07-08 external audit. The three tools that broke it are deliberate and were acknowledged in the CHANGELOG and `docs/projet-file/mcp-02-spec.md section 2` at ship time: `participant_events` (9th, v0.4.0 Phase 1), `topic_metrics` (10th, Phase 2), `peek_bag_samples` (11th, Phase 3). They are accepted; the revised ceiling is **11 tools**. A 12th tool now needs an explicit re-scope discussion documented in this register before code lands. Mitigation going forward: the `verify-change` skill's doc-drift step and the `docs-curator` sweep keep this register, `README.md`, and `CLAUDE.md` in sync so a ceiling break cannot ship undocumented again.

---

## 12. Decision gates

Three explicit gates, designed so a "no" stops the corresponding workstream cleanly. Status below measured 2026-08-31 (`docs/projet-file/traction/latest-summary.md`).

- **Gate G1: Phase 1 -> Phase 2.** Originally: v0.1.x ships all Phase 1 items (rclpy adapter, native MCAP reader, windowed sampling, telemetry endpoint) AND ten Pro early-access slots are reserved. As measured 2026-08-31: 0 of 10 slots reserved. The Pro-slot half of this gate is now moot: the license-gated Pro subscription it was reserving slots for is abandoned (section 6, section 9). Revised criterion: triggered when v0.1.x ships all Phase 1 items AND at least one paid commercial-support engagement (section 6) has closed. Not yet triggered on either half.
- **Gate G2: Pack expansion.** Triggered when TopicForge has stable PyPI weekly install counts above a threshold (target: 100/week sustained over a month) AND the MCP 02 spec is ratified. As measured 2026-08-31: approximately 66/week, a release-driven spike receding (up from roughly 1/week before the v0.5.1 install-breakage fix); not sustained, threshold not cleared. Below the threshold, do not start MCP 02; the issue is reach, not surface area.
- **Gate G3: DDS horizon activation.** Triggered when at least three open-source logos (named teams using TopicForge or a pack MCP and willing to be cited) AND a credible enterprise inbound (defense / aero with budget) arrive in the same quarter. As measured 2026-08-31: 0 named logos, 0 enterprise inbound; not cleared. Below this, the DDS abstraction stays internal: architectural prep only, no marketing, no pricing, no public roadmap update.

The gates are not aspirational. A workstream that has not cleared its gate gets explicitly paused: the maintainer's time is the scarcest resource, not ideas.

---

## 13. Maintenance of this document

This file is the canonical strategic plan. It is referenced from `README.md`, `CLAUDE.md`, `docs/pro.md`, `.claude/agents/docs-curator.md`, `.claude/agents/qa-reviewer.md`, and the `release-checklist` skill. Drift between this plan and the code or the user-facing docs is treated as a `docs-curator` task per `CLAUDE.md` section 10.

Update cadence: revise at every minor version release (`v0.X.0`) and after every decision gate trigger. Smaller patches (`v0.x.Y`) do not require an update here. Roadmap markers in code (`# TODO(roadmap): <topic>`) point to entries in this file by topic: when a marker is retired, the corresponding line here should be updated to reflect the new state.
