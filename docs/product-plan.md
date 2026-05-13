# TopicForge — Product Plan

## Vision

Make AI agents *useful* to robotics developers by giving them grounded, structured access to the ROS2 world — starting with topic inspection and bag analysis, expanding into URDF, datasets, and simulation.

## Target users

1. **ROS2 application developers**
   Building robot behaviors, debugging topic graphs, integrating sensors. They live in a terminal alongside `ros2 topic` and want their AI assistant to live there too.

2. **Robotics ML / CV engineers**
   Work with recorded bags, train perception models, validate data quality. They need fast structured summaries of recordings without writing a one-off script every time.

3. **Robotics team leads & toolsmiths**
   Want their teams' AI assistants to *understand* the robot stack instead of hallucinating about it. They are the buyers for paid tiers.

4. **Indie robotics builders & researchers**
   Hobbyists, startup founders, researchers who already use Claude / Cursor / Windsurf and want their LLM to actually help with ROS instead of giving plausible-sounding nonsense.

## Pain points TopicForge addresses

- ROS2 introspection lives in a CLI and DDS world that LLM agents cannot directly see.
- Bag files are opaque without specialized tooling; "what's in this bag?" routinely takes ten commands and a yak shave.
- AI assistants hallucinate ROS APIs, topic names, message types, and bag layouts because they have no live signal.
- Onboarding a new dev to an unfamiliar robot codebase is slow — the topic graph is tribal knowledge.
- Debugging "why is my robot not moving?" benefits enormously from a structured, agent-readable view of the topic graph.

## Product value

- **Grounding.** Structured, JSON-typed tool outputs replace LLM guesses about your topic graph.
- **Speed.** One tool call replaces a multi-command bash workflow.
- **Safety.** Read-only inspection by default. TopicForge never publishes, never sends a command to a robot.
- **Portability.** Works against a real ROS2 setup *or* a deterministic mock, so it fits CI, demos, screencasts, and live debugging.
- **Composability.** It is an MCP server — any MCP client (Claude Desktop, Claude Code, Cursor, etc.) gets it for free.

## Differentiation

There are many ROS introspection tools (rqt, Foxglove, plotjuggler, custom scripts). There is no production-minded **MCP** server for ROS. TopicForge claims that niche first and is positioned to expand into adjacent robotics AI workflows: URDF tooling, dataset inspection, simulation control.

The strategic moat is *the MCP surface itself* — the right set of well-typed, well-described tools that LLMs love to call.

## Monetization

### Tier 0 — Free / OSS core
- Core inspection MCP server (this MVP)
- Mock mode, live mode, basic bag analysis
- Self-hosted, runs locally

### Tier 1 — Pro (paid, per developer)
- URDF validation & generation tools
- Advanced bag anomaly detection (clock jumps, frame drops, TF tree gaps)
- Multi-bag comparison & regression detection
- Dataset (rosbag → ML) export helpers
- Priority support

### Tier 2 — Team
- Hosted MCP endpoint with auth
- Org-wide bag library indexing & search
- CI/CD integration (PR comments summarizing bag changes between branches)
- Audit logs

### Tier 3 — Marketplace / Add-ons
- Blender synthetic data pipeline controller
- Gazebo / Isaac Sim controller MCP
- Vendor-specific integrations (NVIDIA, Clearpath, Universal Robots)

## Roadmap from MVP to sellable product

### Phase 0 — MVP (this repository)
- 5 core MCP tools (`health_check`, `list_topics`, `get_topic_info`, `sample_messages`, `analyze_bag`)
- Mock & live (CLI-based) adapters
- Clean architecture, full test coverage on services/adapters
- Goal: usable on day one with Claude Desktop, demoable without a robot

### Phase 1 — Reliability & polish (4–6 weeks after MVP)
- `rclpy`-backed live adapter for richer and faster sampling
- Robust bag parsing across `.bag`, `.mcap`, `.db3` (with native `.mcap` reader)
- Streaming-friendly tool outputs (windowed echo, time-range sampling)
- Opt-in usage telemetry (no payload data; tool name + latency only)
- Hardening: better error messages, clearer mock/live boundary, performance budgets

### Phase 2 — Differentiated features (Pro tier candidates)
- **URDF inspector / validator** — parse URDF/xacro, return joints, links, inertias, collision geometry, common issue heuristics
- **Bag anomaly detector** — statistical + rule-based detection of clock jumps, dropped frames, TF tree breaks, frequency drift
- **Bag diff & regression report** — compare two bags from the same scenario, surface meaningful deltas
- **Dataset export** — rosbag → COCO / Hugging Face Datasets for perception model training
- **Topic graph visualizer payload** — return graph data the client can render

### Phase 3 — Distribution
- Public release on PyPI and GitHub
- Listing on MCP server directories (Anthropic registry, community lists)
- Claude Code plugin packaging
- Marketing site with browser-runnable mock demo
- Launch posts (Hacker News, r/ROS, robotics newsletters)

### Phase 4 — Hosted & Team
- Hosted MCP endpoint (HTTPS + auth)
- Org workspace concept (shared bag indexes, team config)
- CI integration (GitHub Action that comments bag stats on PRs)
- SSO, audit logs, SLA

### Phase 5 — Adjacent surfaces
- Blender / synthetic data MCP server
- Sim controller MCP (Gazebo, Isaac, Webots)
- Hardware vendor integrations
- Cross-product MCP suite branding

## Non-goals (for now)

- **Actuating real robots from the MCP server.** The write path is out of scope on purpose — safety, trust, liability. Eventually a Pro feature behind explicit gating.
- **General-purpose ROS replacement.** TopicForge augments ROS2 tooling; it doesn't try to replace it.
- **A web UI.** The MCP client *is* the UI. Browser dashboards are a Phase-4+ consideration.

## Risk register

| Risk                                              | Mitigation                                                          |
| ------------------------------------------------- | ------------------------------------------------------------------- |
| `rclpy` fragility across distros                  | CLI-based adapter is the default live path; `rclpy` is opt-in       |
| Bag format fragmentation (`.bag`, `.mcap`, `.db3`)| Lean on `ros2 bag info` first; add native readers only when justified |
| MCP protocol churn                                | Pin `mcp` to a known-good range; track release notes                |
| LLM misinterpretation of outputs                  | Strict schemas, generous `description` fields, structured envelopes |
| ROS2 ecosystem decline                            | Adapter pattern lets us pivot to ROS1 / Zenoh / Foxglove protocols  |
| Commoditization by Anthropic-built ROS MCP        | Move fast on differentiated Phase-2 features (URDF, anomalies)      |

## Success signals

- A robotics developer can install, configure with Claude Desktop, and ask "what topics are on my robot right now?" in **under 5 minutes**.
- A bag analysis call returns a structured summary that a human reviewer would *also* write.
- Mock mode is good enough that the demo screencast can be recorded without a robot present.
- A first paying team adopts the Pro tier for URDF + anomaly detection within 3 months of public launch.

## TODO markers in code

Roadmap items are tagged in the codebase with `# TODO(roadmap):` comments so future contributors can find natural extension points without reading this document:

- `# TODO(roadmap): rclpy-backed adapter`
- `# TODO(roadmap): URDF tools`
- `# TODO(roadmap): bag anomaly detection`
- `# TODO(roadmap): dataset export`
- `# TODO(roadmap): synthetic data pipeline`
