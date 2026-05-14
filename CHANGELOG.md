# Changelog

All notable changes to TopicForge are documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-05-14

### Strategic

- **OMG DDS-RTPS multi-vendor positioning.** TopicForge is now framed as a read-only DDS-RTPS observer that joins the bus via one of two OSS Python participants — Eclipse CycloneDDS or eProsima Fast DDS — and observes every conformant vendor on the domain (RTI Connext, OpenDDS, CoreDX, Dust DDS in Rust, etc.) regardless of host language. See `docs/dds-interop-matrix.md` for the canonical statement and `docs/projet-file/references/omg-dds-interop-2025-05-08.xlsx` for the OMG May 2025 interop reference. The earlier v0.2.0/v0.3.0 phasing (Cyclone-only at v0.3.0, RTI at v0.3.0+) is collapsed: multi-vendor OSS lands together at v0.3.0 ; RTI Pro defers to v0.4.0+.

### Added

- **Real `CycloneDdsAdapter`** — replaces the v0.2.0 stub with actual CycloneDDS discovery via `cyclonedds.builtin.BuiltinDataReader` on the DCPS participant/subscription/publication builtin topics. QoS extracted via `Policy.*` class-name introspection. `take_iter(timeout=...)` for bounded discovery. Lazy-imported via `services.factory`.
- **`FastDdsAdapter`** (`adapters/dds_fast/`) — new parallel OSS adapter built on the `fastdds` Python bindings (BSD-licensed, eProsima). Duck-typed listener subclass aggregates discovery callbacks under an RLock. Bounded `discovery_wait_ms=1500` warm-up after participant creation. `close()` releases the participant via `factory.delete_participant`.
- **`adapters/common/dds_helpers.py`** — vendor-neutral helpers: `canonicalize_vendor_id` (OMG vendor_id → canonical tag), `format_guid` (16-byte GUID → `xxxxxxxx.xxxxxxxx.xxxxxxxx.xxxxxxxx` canonical text), `DDS_ONLY_ERROR_MSG` (shared remediation message). Pure functions, no DDS dependency.
- **Pyproject extras refactor**: `[dds-cyclone]` (Cyclone only), `[dds-fast]` (Fast only), `[dds]` (both — union of v0.2.0 `[dds]` behavior plus fastdds).
- **`TOPICFORGE_DDS_BACKEND=fast`** — new accepted value alongside `mock | cyclone | rti | auto`.
- **3rd mock participant** with `vendor="fast"` exercises the multi-vendor positioning in mock mode.
- **New pytest marker** `requires_fastdds` — auto-skips without the binding.
- **35+ new tests**: `tests/test_dds_helpers.py`, `tests/test_fast_adapter.py`, `tests/test_dds_cross_vendor.py` (parametrized on both adapters), 6 analyzer edge cases in `tests/test_qos_analyzer.py`, 4 new health tests for DDS field population.

### Changed

- **`ParticipantInfo.vendor` Literal widened** to include `"fast"`. **Strict JSON-schema clients pinned to v0.2.0 will reject `vendor:fast` unless their schema is regenerated.** Standard MCP clients reading tool descriptions dynamically are unaffected.
- **`HealthReport.dds_backend` Literal widened** to include `"fast"`. Same soft-breaking caveat.
- **`AdapterName` Literal widened** to include `"fast"` (internal type — no wire impact).
- **`DdsBackend` / `ResolvedDdsBackend`** widened to include `"fast"`.
- **`Settings.effective_dds_backend` auto resolution** now prefers Fast DDS > Cyclone DDS > Mock (was Cyclone > Mock in v0.2.0). v0.2.0 users with only `cyclonedds` installed see no change — Fast is unimportable on their host. Users with both SDKs installed will see Fast selected. Reflects the OMG May 2025 interop matrix.
- **`HealthService.report()`** now populates `dds_backend`, `dds_domain_id`, `middleware_available` — previously returned schema defaults regardless of configuration (v0.2.0 latent bug). `middleware_available` is checked via `importlib.util.find_spec` on the active backend's Python module.
- **`Ros2CliAdapter._DDS_MODULE_INACTIVE_MSG`** updated to mention both `pip install topicforge[dds-cyclone]` and `pip install topicforge[dds-fast]` remediation paths.
- **`Inspector` DDS topic validator relaxed** — `detect_qos_mismatches` and `peek_dds_samples` now accept DDS-native topic names (no leading `/` required, `::` separators allowed) via a new `_validate_topic_name_dds`. The strict ROS2 validator stays in place for the 5 ROS2 graph methods. Resolves audit-2026-05-14 "Refactor opportunities" #5.

### Removed

- **`CycloneDdsAdapter` v0.2.0 stub** — `_NOT_IMPLEMENTED_MSG` and the corresponding `test_dds_surface_raises_stub_error_in_v020` test removed. The 3 DDS methods now serve real results when cyclonedds is installed.

### Notes

- **OMG-DDS-RTPS interoperability** is the protocol guarantee that makes multi-vendor observation work — see `docs/dds-interop-matrix.md` and `docs/projet-file/references/omg-dds-interop-2025-05-08.xlsx`.
- **v0.3.0 `peek_dds_samples` limitation** — full-fidelity on the 4 builtin DCPS topics (`DCPSParticipant`, `DCPSSubscription`, `DCPSPublication`) ; arbitrary user topics raise an `AdapterError` with a v0.3.x roadmap pointer (XTypes/IDL discovery is the missing piece, both for Cyclone via `cyclonedds.dynamic.get_types_for_typeid` and for Fast DDS via XTypes remote type lookup).
- **`pip install topicforge[dds]` in v0.3.0** now pulls BOTH `cyclonedds` and `fastdds` (was Cyclone only in v0.2.0). Use `[dds-cyclone]` or `[dds-fast]` for single-vendor installs. See `docs/MIGRATION_v0.2_to_v0.3.md`.
- **Fast DDS pin**: `fastdds>=2.6.1,<3` — Fast DDS 3.x binding wheels for Python 3.11+ on Windows / Linux are not yet stable. Bump when upstream cuts stable 3.x wheels.
- **No code change to the 5 ROS2 tools** — `health_check`, `list_topics`, `get_topic_info`, `sample_messages`, `analyze_bag` behave identically to v0.2.0. The `mode_effective` wire contract is unchanged ; `health_check` now populates DDS fields correctly.
- **Full migration guide**: `docs/MIGRATION_v0.2_to_v0.3.md`.

## [0.2.0] - 2026-05-14

### Strategic

- **Mono-MCP pivot (2026-05-14).** The 3-to-5-MCP pack draft is collapsed into a 2-product strategy: TopicForge umbrella (this product — covers ROS2 today and grows a DDS observability module starting with v0.2.0), and **DatasetForge** (Vision Dataset Inspector, the standalone second product). The previously-planned standalone DDS-MCP product is cancelled — its spec is reframed as the TopicForge DDS module spec at `docs/projet-file/mcp-02-spec.md`. Motif: solo-maintenance cost of two parallel repos was the binding constraint, and ROS2 / DDS are the same problem shape (typed pub/sub graph introspection) under the same `MiddlewareAdapter` superset.

### Added

- **DDS module — 3 new MCP tools.** `list_participants(domain_id)`, `detect_qos_mismatches(topic)`, `peek_dds_samples(topic, count)`. All read-only ; surface DDS-layer introspection distinct from the ROS2 graph tools. `peek_dds_samples` is deliberately separate from `sample_messages` — different layer, different semantics, distinct tool description so an LLM picks the right one in a mixed setup.
- **`MiddlewareAdapter` protocol** in `adapters/base.py` — superset of the historical `RosAdapter`. Covers both ROS2 graph methods and the new DDS methods under one contract. `RosAdapter` retained as a backward-compat alias (`RosAdapter = MiddlewareAdapter`).
- **`CycloneDdsAdapter`** (`adapters/dds_cyclone/`) — lazy-imported only when `TOPICFORGE_DDS_BACKEND=cyclone` and the optional `cyclonedds` extras are installed (`pip install topicforge[dds]`). **v0.2.0 ships a protocol-compliant stub**: the lazy import, `is_available()`, and routing all work ; the 3 DDS methods raise `AdapterError` with a v0.2.x roadmap pointer. The real CycloneDDS discovery (builtin topics, QoS pair extraction, typed reader for samples) lands in a v0.2.x patch. The mock backend (`TOPICFORGE_DDS_BACKEND=mock`, the default) exposes a working DDS surface against deterministic fixtures in the meantime.
- **3 new Pydantic schemas**: `QosProfile` (Reliability / Durability / History / Deadline at MVP), `ParticipantInfo` (GUID, vendor, hostname, domain_id), `MismatchReport` (incompatible_policies + severity). All frozen, `extra="forbid"`.
- **Pure analyzer** `adapters/common/qos_analyzer.detect_mismatches` — module-level pure function, testable against synthesized QoS pairs without any DDS middleware installed.
- **Environment variables**:
  - `TOPICFORGE_DDS_BACKEND` — `mock | cyclone | rti | auto`, default `mock`. The DDS module is opt-in ; existing ROS2-only setups behave unchanged.
  - `TOPICFORGE_DDS_DOMAIN_ID` — DDS domain id observed (0..232), default `0`.
- **Mock fixtures enriched**: 2 deterministic DDS participants, two-topic scenario (`/dds/well_matched` and `/dds/qos_mismatch`) exercising `detect_qos_mismatches` end-to-end.
- **`pyproject.toml` extras**: `[dds]` pulls `cyclonedds>=0.10` ; `[all]` aliases `[dds]`. `pip install topicforge` keeps the core + mock only (zero install impact on ROS2-only users).

### Changed

- **`TopicInfo` schema soft-breaking.** Three additive optional fields (`reader_count: int | None`, `writer_count: int | None`, `qos_profile: QosProfile | None`) — all default `None`. Producer side: code constructing `TopicInfo` directly is unaffected (defaults compile). **Strict MCP clients that validated v0.1.x responses against the `TopicInfo` schema with `additionalProperties: false` will reject v0.2.0 responses unless their schema is regenerated. Standard MCP clients that read tool descriptions dynamically are unaffected.**
- **`HealthReport` schema soft-breaking**, same shape. Three additive optional fields (`dds_backend`, `dds_domain_id`, `middleware_available`) with safe defaults (`"none"`, `None`, `False`).
- **`RosAdapter` renamed to `MiddlewareAdapter`** in `adapters/base.py`. The old name remains as an alias (`RosAdapter = MiddlewareAdapter`) ; existing imports `from topicforge.adapters import RosAdapter` still type-check. The `Ros2CliAdapter.name` value moves from `"live"` to `"ros2_cli"` — internal tag, separate from the MCP-wire `mode_effective` field which keeps its `Literal["mock", "live"]` contract.
- **`Settings`** gains `dds_backend` and `dds_domain_id` fields with safe defaults (`"mock"`, `0`). Existing `Settings(...)` constructors are unaffected.
- **`Ros2CliAdapter` DDS methods raise `AdapterError`** with a clear remediation path (`pip install topicforge[dds]` + `TOPICFORGE_DDS_BACKEND=cyclone`). This is the v0.2.0 MVP limitation D6 (single-adapter-at-a-time) ; a composite adapter that delegates per-tool is a v0.2.x roadmap item.

### Internal

- `parse_topic_info` and `parse_bag_info` parsers : `mode_effective` kwarg typed as `EffectiveMode` (`Literal["mock", "live"]`) rather than the broader `AdapterName`, cleanly separating the wire-facing mode from the implementation tag.
- New pytest marker `requires_cyclonedds` for tests that need the SDK. Auto-skips otherwise via `pytest.importorskip`.

### Notes

- **`cyclonedds` is optional.** Default installs (`pip install topicforge`) are unchanged from v0.1.2 in dependency footprint. Only `pip install topicforge[dds]` pulls the bindings (`cyclonedds>=0.10`).
- **No code change to the 5 ROS2 tools** — `health_check`, `list_topics`, `get_topic_info`, `sample_messages`, `analyze_bag` behave identically to v0.1.2. The wire contract (`mode_effective: Literal["mock", "live"]`) is unchanged.
- **v0.2.0 MVP limitation**: single adapter at a time. Users select ROS2 introspection (default) or DDS observability via `TOPICFORGE_DDS_BACKEND=cyclone`, not both simultaneously. The unselected half raises `AdapterError` with a remediation pointer. A composite adapter delegating per-tool category is a v0.2.x roadmap item.

## [0.1.2] - 2026-05-13

### Fixed

- `sample_messages` now returns real publish-time timestamps in live mode for `Header`-stamped messages. The live adapter previously shelled out to `ros2 topic echo --once`, which does not emit timestamps, so `MessageSample.timestamp_ns` was always `0`. The invocation is now `ros2 topic echo --csv --once`, whose flattened CSV exposes `header.stamp.sec` and `header.stamp.nanosec` as the first two columns for any `Header`-stamped message; the new `parse_csv_echo` parser reconstructs `timestamp_ns = sec * 1_000_000_000 + nanosec` and strips those two columns out of the payload. **Headerless message types** (e.g. `std_msgs/String`, `geometry_msgs/Twist`) still return `timestamp_ns=0` — they carry no embedded timestamp. Surfacing the rmw **receive** timestamp (rather than the publish-time `header.stamp`) for arbitrary message types remains a roadmap item tied to the future `rclpy`-backed adapter.

### Added

- **`mode_effective` on every tool response (schema, soft-breaking additive).** `TopicInfo`, `SampleResult`, and `BagAnalysis` now carry a required `mode_effective: Literal["mock", "live"]` field. A new `effective_mode` property on the `RosAdapter` protocol is the single source of truth; `Ros2CliAdapter` returns `"live"`, `MockAdapter` returns `"mock"`, services thread it through at result construction time. **Producer side**: Python code constructing these models directly must now supply `mode_effective` — models are `frozen=True, extra="forbid"` with no default. **Client side (over MCP)**: additive — an MCP client consuming JSON sees one extra key per response and is unaffected unless it strictly validates against the v0.1.1 schema with a no-extra-keys assumption.
- **DDS-MCP spec** (`docs/projet-file/mcp-02-spec.md`). Strategic draft for MCP 02 at the time: safety-first read-only DDS observability across middleware vendors (CycloneDDS OSS, RTI Connext Pro tier). Five tools, `MiddlewareAdapter` protocol, mock + cyclone + rti + auto modes. Reviewer notes appended (2026-05-13): wrong cross-reference in §11 flagged. (Reframed the next day as a TopicForge module after the mono-MCP pivot — see [0.2.0] Strategic section.)
- **DatasetForge spec** (`docs/projet-file/mcp-03-spec.md`). Vision Dataset Inspector spec, re-slotted to MCP 03 after competitive-landscape audit that surfaced zero non-ROS DDS-MCP projects and made a standalone DDS-MCP the stronger MCP 02 candidate. Reviewer notes appended (2026-05-13): contradictory §11 phrasing and two implicitly-resolved open questions flagged.

### Changed

- **Safety-first read-only repositioning.** README and `docs/product-plan.md §1` now lead with "read-only by architecture, not by configuration" as the primary identity. Pack candidate list updated: MCP 02 reframed to a non-ROS DDS observability MCP (later folded into TopicForge itself by the 2026-05-14 multi-vendor reframe — see v0.3.0 entry); DatasetForge slides to MCP 03. Strategic context in `docs/product-plan.md §4` and §8 (DDS-complete horizon).
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

[Unreleased]: https://github.com/yaniswav/TopicForge/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/yaniswav/TopicForge/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/yaniswav/TopicForge/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/yaniswav/TopicForge/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/yaniswav/TopicForge/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/yaniswav/TopicForge/releases/tag/v0.1.0
