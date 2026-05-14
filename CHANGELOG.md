# Changelog

All notable changes to TopicForge are documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Strategic

- (2026-05-14) **Mono-MCP pivot.** The 3-to-5-MCP pack draft is collapsed into a 2-product strategy: TopicForge umbrella (this product — covers ROS2 today and will grow a DDS observability module via a generalized `MiddlewareAdapter` protocol in v0.2.0+), and **DatasetForge** (Vision Dataset Inspector, the standalone second product). DdsForge as a standalone repo is cancelled — its spec is reframed as the TopicForge DDS module spec at `docs/projet-file/mcp-02-spec.md`. Motif: solo-maintenance cost of two parallel repos was the binding constraint, and ROS2 / DDS are the same problem shape (typed pub/sub graph introspection) under the same `MiddlewareAdapter` superset. No code change in this release ; documentary pivot only.

## [0.1.2] - 2026-05-13

### Fixed

- `sample_messages` now returns real publish-time timestamps in live mode for `Header`-stamped messages. The live adapter previously shelled out to `ros2 topic echo --once`, which does not emit timestamps, so `MessageSample.timestamp_ns` was always `0`. The invocation is now `ros2 topic echo --csv --once`, whose flattened CSV exposes `header.stamp.sec` and `header.stamp.nanosec` as the first two columns for any `Header`-stamped message; the new `parse_csv_echo` parser reconstructs `timestamp_ns = sec * 1_000_000_000 + nanosec` and strips those two columns out of the payload. **Headerless message types** (e.g. `std_msgs/String`, `geometry_msgs/Twist`) still return `timestamp_ns=0` — they carry no embedded timestamp. Surfacing the rmw **receive** timestamp (rather than the publish-time `header.stamp`) for arbitrary message types remains a roadmap item tied to the future `rclpy`-backed adapter.

### Added

- **`mode_effective` on every tool response (schema, soft-breaking additive).** `TopicInfo`, `SampleResult`, and `BagAnalysis` now carry a required `mode_effective: Literal["mock", "live"]` field. A new `effective_mode` property on the `RosAdapter` protocol is the single source of truth; `Ros2CliAdapter` returns `"live"`, `MockAdapter` returns `"mock"`, services thread it through at result construction time. **Producer side**: Python code constructing these models directly must now supply `mode_effective` — models are `frozen=True, extra="forbid"` with no default. **Client side (over MCP)**: additive — an MCP client consuming JSON sees one extra key per response and is unaffected unless it strictly validates against the v0.1.1 schema with a no-extra-keys assumption.
- **DdsForge spec** (`docs/projet-file/mcp-02-spec.md`). Strategic draft for MCP 02: safety-first read-only DDS observability across middleware vendors (CycloneDDS OSS, RTI Connext Pro tier). Five tools, `MiddlewareAdapter` protocol, mock + cyclone + rti + auto modes. Reviewer notes appended (2026-05-13): wrong cross-reference in §11 flagged.
- **DatasetForge spec** (`docs/projet-file/mcp-03-spec.md`). Vision Dataset Inspector spec, re-slotted to MCP 03 after competitive-landscape audit that surfaced zero non-ROS DDS-MCP projects and made DdsForge the stronger MCP 02 candidate. Reviewer notes appended (2026-05-13): contradictory §11 phrasing and two implicitly-resolved open questions flagged.

### Changed

- **Safety-first read-only repositioning.** README and `docs/product-plan.md §1` now lead with "read-only by architecture, not by configuration" as the primary identity. Pack candidate list updated: MCP 02 is now DdsForge (non-ROS DDS observability, zero-competition niche); DatasetForge slides to MCP 03. Strategic context in `docs/product-plan.md §4` and §8 (DDS-complete horizon).
- **Internal API.** `Inspector.sample_messages` now returns a `SampleResult` envelope (previously a `list[MessageSample]`). The MCP-facing tool handler is reduced to a thin pass-through. No effect on the tool's wire-level response shape (handlers already wrapped the list into `SampleResult`), but flagged here for anyone importing `Inspector` directly outside this repo.

### Internal

- Docstring fix in `parse_csv_echo`: the example output now shows post-strip payload keys as `col_0`, `col_1` (the parser re-indexes from `col_0` after dropping the two timestamp columns), matching the existing test in `tests/test_live_adapter_parse.py`.

## [0.1.1] - 2026-05-13

### Added

- **Opt-in anonymous usage telemetry** behind `TOPICFORGE_TELEMETRY=on` (default: off). When enabled, each MCP tool call emits a single event with six fields only: `tool_name`, `latency_ms`, `mode`, `version`, `session_id` (random UUID per process, never persisted), and `success`. No topic names, message bodies, bag paths, hostnames, or environment data ever leave the process. See the README "Telemetry" section for the full payload contract and opt-out instructions.
- `src/topicforge/telemetry/` module with `TelemetryClient`, `TelemetryEvent`, and an `instrument()` decorator that wraps tool handlers with timing + emit. When telemetry is off, `instrument()` is the identity function — zero overhead and zero possibility of a network call in the OFF code path.
- Pluggable `Transport` callable; v0.1.1 ships a structured-log transport. A future S3-backed HTTP endpoint will plug in without touching tool handlers.
- 29 telemetry tests covering: default-off behaviour, env var parsing (`on`/`1`/`true`/`yes`/`enabled` vs anything else), payload shape and key allowlist, payload privacy (user input never leaks), session id stability and per-process uniqueness, transport-exception isolation, decorator signature preservation, and end-to-end verification that the OFF code path never invokes the transport.

### Changed

- `Settings` gained a `telemetry_enabled: bool` field.
- `build_app(...)` accepts optional `telemetry` and `telemetry_transport` parameters for test injection.
- `register_tools(...)` now takes a `TelemetryClient`.
- `.env.example` documents `TOPICFORGE_TELEMETRY`.
- README adds a `Telemetry` section and updates the Security model note to reflect opt-in telemetry availability.

## [0.1.0] - 2026-05-12

Initial MVP release of TopicForge — ROS Topic Inspector & Bag Analyzer MCP server.

### Added

- Five read-only MCP tools exposed over FastMCP: `health_check`, `list_topics`, `get_topic_info`, `sample_messages`, and `analyze_bag`.
- `RosAdapter` protocol in `adapters/base.py` defining the contract every backend implements.
- Mock adapter (`adapters/ros2_mock/`) with deterministic fixtures modeling a small differential mobile robot equipped with a LIDAR and an RGB camera.
- Live adapter (`adapters/ros2_live/`) built on subprocess wrappers around the `ros2` CLI, with pure module-level parsers tested independently of any ROS2 install.
- Three runtime modes selectable via `TOPICFORGE_MODE`: `mock`, `live`, and `auto`. The `auto` resolution lives in `Settings.effective_mode`; the live-to-mock fallback when the adapter cannot start lives in `services/factory.py`.
- Windows-first cross-platform support: executable resolution via `shutil.which` (handles `ros2.cmd` / `ros2.bat` shims), `subprocess.run` called with absolute paths and never `shell=True`, all filesystem paths via `pathlib.Path`.
- Pydantic v2 schemas in `models/` configured with `extra="forbid"` and `frozen=True`, returned as the structured payload of every tool.
- Pytest suite that runs entirely without a ROS2 environment, covering services, mock adapter, and live-adapter parsers.
- Build, lint, and tooling configuration: Python 3.11+, `mcp >= 1.0.0` (FastMCP), `pydantic >= 2.6`, pytest, ruff, hatchling.
- Licensed under the MIT License.

### Notes

- The write path (publishing, commanding robots) is intentionally out of scope for the MVP.
- `analyze_bag` in live mode parses `ros2 bag info` text output; deeper anomaly detection remains mock-only for now.

[Unreleased]: https://github.com/yaniswav/TopicForge/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/yaniswav/TopicForge/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/yaniswav/TopicForge/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/yaniswav/TopicForge/releases/tag/v0.1.0
