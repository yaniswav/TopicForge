# Changelog

All notable changes to TopicForge are documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.3] - 2026-10-01

Fixes from an independent senior review of the whole repository (five
parallel reviewers, then an adversarial pass that tried to refute each
finding; 14 of 14 medium-or-higher findings held). Nothing here was
validated against a live DDS bus: none is available, which is itself one of
the findings.

### Security

- **Dependency confusion in published extras.** `[dds]`, `[dds-fast]`,
  `[all]` and `[dds-all-oss]` depended on `fastdds`, and `[dds-dust]` on
  `dust-dds-python`. Neither name exists on PyPI. The install failed, and
  because the names are unclaimed anyone could have registered them: users
  following the documented `pip install topicforge[dds]` would then have run
  that code. Those extras are removed; `[dds]` now means Cyclone only. The
  metadata of 0.3.0 to 0.5.2 is immutable on PyPI, which is why those
  releases are being yanked and a security advisory published.
- **Removed the automatic `topicforge_pro` plugin hook.** At startup the
  server imported any installed package named `topicforge_pro` and handed it
  the full MCP server instance, so a third-party package with that
  (unclaimed) name could have registered write tools and bypassed the
  read-only-by-architecture guarantee. The hook never registered a single
  tool. `TOPICFORGE_LICENSE_KEY` is no longer read by the core.

### Fixed

- **`health_check` reported the configured mode, not the real one.** With
  `TOPICFORGE_MODE=live` and no `ros2` CLI, the server fell back to mock
  fixtures while `health_check` (and telemetry) still said `live`. The
  health service now reads the adapter that was actually built.
- **An explicit DDS backend was silently ignored without ROS 2.** In the
  default `auto` mode, `TOPICFORGE_DDS_BACKEND=cyclone` on a host without
  the `ros2` CLI produced mock fixtures, which is exactly the DDS-only user
  the module targets. An explicit backend (`cyclone`, `fast` or `auto`) is
  now honoured with or without ROS 2; only `TOPICFORGE_MODE=mock` forces
  mock. A missing binding falls back with an explicit warning.
- **`auto` could pick a stub and lose the real backend.** `pyopendds` exists
  on PyPI; when importable, `auto` chose the OpenDDS stub, which always
  reports unavailable, and the server ended on mock even with Cyclone
  installed. The auto chain now only tries `fast` then `cyclone`.
- **A broken DDS environment crashed startup.** A failing
  `DomainParticipant` (for example an invalid `CYCLONEDDS_URI`) or a native
  library that fails to load raised past `build_adapter`, which documents
  that it never raises. Both now degrade with a logged cause.
- **Latent infinite loop in Cyclone reads.** cyclonedds `read_iter` resets
  its timeout on every received sample, so `list(reader.read_iter(...))`
  never returned on a topic publishing faster than the timeout. It was only
  unreachable because of the decoding bug below; fixing that bug alone would
  have frozen the server on any active topic. Every `read_iter` is now
  bounded with `islice`.
- **`topic_metrics` counted samples that were never received.** On Fast DDS
  each `peek_dds_samples` on a user topic fabricated a placeholder sample and
  recorded it, so the reported frequency was the user's call rate. Fallback
  paths no longer record anything.

### Changed

- **User-topic payload decoding is disabled.** It never worked: on Cyclone
  the call to `get_types_for_typeid` used the wrong arity and its tuple
  result was not unpacked, both errors swallowed at DEBUG level; on Fast the
  decoder returned `None` unconditionally. Rather than ship a repair that has
  never run, `peek_dds_samples` now states plainly that it reports topic
  presence without decoding the payload. The three builtin DCPS topics are
  unaffected. Re-enabling it needs a real bus to validate against.
- **`topic_metrics` scope stated honestly.** It only has data for the
  builtin discovery topics, its observed frequency reflects how often
  `peek_dds_samples` is called rather than the publish rate, and
  `frequency_hz_declared` is not populated. The tool description says so.
- **Python 3.10 is supported** (was 3.11+). ROS 2 Humble on Ubuntu 22.04
  ships Python 3.10, so the documented Humble setup paths could not install
  the package. No 3.11-only syntax or dependency was in use.
- **Tool descriptions served to the LLM** no longer describe Cyclone as a
  stub, no longer list RTI as a selectable backend, and the DDS tools'
  `topic` parameter now documents that builtin topics such as
  `DCPSParticipant` carry no leading `/` (the previous schema said every
  name must start with `/`, contradicting the validator).
- **Integration tests are deselected by default** (`-m "not integration"` in
  `addopts`). The scenarios runner is still a dispatch shell that spawns no
  publisher and validates nothing; its test is now a strict `xfail` instead
  of failing every run on any machine with `cyclonedds` installed.
- **CI** tests Python 3.10 too, enforces the 85 percent coverage floor that
  was configured but never applied, and resolves every extra against PyPI on
  each change so an orphan package name cannot ship again. The weekly
  upstream watch also resolves the documented extras.

### Removed

- Extras `[dds-fast]`, `[dds-opendds]`, `[dds-dust]`, `[dds-all-oss]`. Fast
  DDS remains usable with `TOPICFORGE_DDS_BACKEND=fast` once its Python
  binding is built from eProsima's sources.
- `TOPICFORGE_DDS_BACKEND` values `rti`, `opensplice`, `coredx`, `intercom`.
  They now fail with an explicit configuration error. RTI, CoreDX and other
  vendors are still observed on the bus through standard RTPS discovery, and
  `rti` remains a valid observed-participant vendor tag.

### Correction to the 0.5.1 notes

- 0.5.1 stated that sequence gaps are now "counted per writer". The buffer
  supports it, but no adapter ever passes a writer GUID, so in practice all
  samples of a topic still share one counter. The statement was wrong.

## [0.5.2] - 2026-08-22

Documentation and metadata only. No code, no schema, and no dependency
changes. `pip install topicforge` behaves identically to 0.5.1.

### Added

- **`server.json`: MCP Registry metadata.** Declares the server as
  `io.github.yaniswav/topicforge` (the `io.github.<user>/` prefix is
  required by the registry's GitHub-namespace authentication) and points
  at the PyPI package, with the four user-facing environment variables
  described so MCP clients can render configuration hints. Validated
  against the registry's published JSON schema.
- **PyPI ownership marker in `README.md`.** The registry verifies
  ownership of a PyPI package by looking for an `mcp-name: <server name>`
  string in the package description, which is generated from the README
  at build time. The marker is an HTML comment, so it does not render.

### Why this release exists

The marker only reaches PyPI when a distribution is built and published.
0.5.1 shipped before the marker was added, so its published description
does not carry it and registry validation would reject the submission.
Cutting this version is the mechanical prerequisite for listing
TopicForge in the official MCP Registry. There is no functional change
to install.

## [0.5.1] - 2026-08-22

### Fixed (hotfix; MCP SDK 2.0 incompatibility)

- **Hard-pinned `mcp < 2`: TopicForge was uninstallable from 2026-07-28
  to 2026-08-22.** The MCP Python SDK released `2.0.0` on 2026-07-28
  alongside the `2026-07-28` protocol revision. That release removes the
  `mcp.server.fastmcp` module entirely (`mcp/server/` now ships `auth`,
  `lowlevel`, and `mcpserver`), so `server/app.py`'s
  `from mcp.server.fastmcp import FastMCP` raises `ImportError` against
  it. The dependency was declared as an unbounded `mcp>=1.0.0`, so every
  fresh `pip install topicforge` resolved to `2.0.0` and produced a
  server that could not start. Local development and CI both masked the
  break: the dev environment had `mcp 1.27.1` already installed, and
  the last CI run predates the SDK release. Pinning `<2` restores
  installability on the 1.x line; migrating to the 2.x API
  (`FastMCP` -> `MCPServer`, transport options moved from the
  constructor to `.run()`, stateless protocol) is tracked separately and
  is deliberately **not** bundled into this hotfix.

### Note on scope

- This release also publishes the external-audit work (Lots 0-5,
  2026-07-08) that had accumulated under `[Unreleased]`. The Cyclone
  `take_iter` -> `read_iter` change documented below is still **validated
  by static analysis only** (`ruff` + `py_compile`); it has not been
  exercised against a real multi-vendor DDS bus. It ships here because
  leaving the package uninstallable was the larger harm: a broken
  install affects every user, while this change can only affect users
  running the Cyclone backend against a live bus. Real-bus validation
  remains open.

### Changed

- **DDS pure logic extracted for testability (Lot 0, external audit
  2026-07-08).** The QoS-profile normalizers (`_cyclone_qos_to_profile` /
  `_fast_qos_to_profile`) and the discovery-sample field extractors
  (`_extract_guid` / `_extract_vendor_id` / `_extract_hostname` /
  `_extract_topic_name` / `_is_removal`) were moved out of
  `adapters/dds_cyclone/adapter.py` and `adapters/dds_fast/adapter.py`
  (which import their vendor binding at module top level and were therefore
  never exercised by the test suite) into the binding-free
  `adapters/common/qos_normalize.py` and `adapters/common/dds_introspection.py`.
  The adapters import them back under their original private names, so every
  call site is unchanged (verified by `ruff check` static analysis, since the
  adapters are not importable without their SDKs). `fast_qos_to_profile`
  takes the binding's int->str enum maps as parameters so it stays
  import-free. This is the highest-value item from the audit: the QoS
  normalization feeds `detect_qos_mismatches` (the flagship DDS diagnostic)
  and was previously untestable and untested.

### Added

- `tests/test_dds_qos_normalization.py` and `tests/test_dds_introspection.py`
  drive the extracted logic with synthetic duck-typed objects (no
  `cyclonedds` / `fastdds` needed), including regression guards for the
  "renamed policy key silently yields no QoS profile -> no mismatch ever
  reported" failure mode. The extracted modules are now ~91-92% covered.
- `pytest-cov` and `rosbags` added to the `[dev]` extra, plus `[tool.coverage]`
  config with a `fail_under = 85` floor (the two binding-only adapter shells
  are `omit`ted as structurally unreachable without their SDKs). Adding
  `rosbags` un-skips the real `.db3` bag-analysis I/O test.

### Fixed

- `tests/test_bag_service.py` bag-generation helper updated for the current
  `rosbags` API (`Writer(..., version=Writer.VERSION_LATEST)`; typestore keyed
  by `std_msgs/msg/String`, not `std_msgs__msg__String`). The test was
  previously auto-skipped and had gone silently stale. Un-skipping it in CI
  surfaced the drift.
- **`topic_metrics` frequency was wrong (Lot 2, audit C5).** It divided the
  sample count by `(now - oldest_sample)` (folding in idle time since the
  last peek) and counted N intervals instead of N-1. Now measured as
  `(N-1) / (newest - oldest)` over the samples' own arrival span; samples
  surfaced by a single opportunistic peek share one timestamp (span 0) and
  correctly yield `frequency_hz_observed = null` instead of a fabricated rate.
- **`topic_metrics` sequence-gap count exploded on multi-writer topics,
  publisher restarts, and counter wrap (Lot 2, audit C6).** Gaps are now
  counted per writer (new best-effort `MetricsSample.writer_guid`), so
  independent writers' counter offsets are not read as phantom gaps, and a
  single jump wider than 10 000 is treated as a reset/wrap discontinuity
  rather than that many losses.
- **QoS Deadline false negative (Lot 2, audit C3/P1-3).**
  `detect_qos_mismatches` now flags a reader that requests a finite Deadline
  against a writer that offers none: an absent deadline is the infinite
  (loosest) period and cannot satisfy a finite request. The previous rule
  required both sides non-null and silently missed this incompatibility.
- **`LifecycleBuffer` participant map was unbounded (Lot 3, audit P1-4 / M1 /
  P1).** Only the event ring was capped; the participant dict grew one entry
  per GUID ever seen (a churny bus mints a fresh RTPS GUID on each node
  restart), and `list_participants` returned every tombstone forever. Now
  capped at `MAX_PARTICIPANTS = 4096`, evicting `"left"` tombstones first then
  the oldest-inserted entry. The docstring's "Bounded" claim is now true.
- **`MetricsBuffer` topic map was unbounded (Lot 3, audit P2-5).** Per-topic
  rings were capped but the number of topic keys was not; now capped at
  `MAX_TOPICS = 4096`, oldest-inserted topic evicted on overflow.
- **OpenDDS stub `is_available()` always returns False (Lot 4, audit S1).** A
  stub that advertised availability (when a `pyopendds` module happened to be
  importable) could be auto-selected by the factory, after which every tool
  call raised. Now consistent with the Dust stub.
- **`iter_field_names` mis-decoded a string `__slots__` (Lot 4, audit C2).**
  `__slots__ = "value"` was exploded into `['v','a','l','u','e']`; a bare
  string slot is now treated as a single field name.
- **`decode_field_value` recursion is depth-capped at 32 (Lot 4, audit M6).**
  A pathologically deep decoded object graph collapses to `repr()` instead of
  risking `RecursionError`.
- **`_encode_raw_bytes` slices before hex-encoding (Lot 4, audit M5).** A large
  raw payload no longer allocates its full 2x-size hex string only to truncate
  it to the 4096-char preview.

### Added (Lot 4; test hardening)

- End-to-end test that a failing tool call surfaces as an MCP error
  (`ToolError`) rather than being masked as a success: pins the thin-handler
  contract (CLAUDE.md section 8, audit test-gap #4).
- Test pinning that every canonical vendor tag is a valid
  `ParticipantInfo`/`ParticipantEvent.vendor` Literal (guards against
  vendor-map <-> schema drift, audit P2-3). Scenario allowlist `_KNOWN_TOOLS`
  now includes the 11th tool `peek_bag_samples`.

### Removed (Lot 4)

- Dead `annotate_full` / `annotate_partial` imports and the `_ = (...)`
  unused-suppressor from `services/bag_service.py`.

### Documentation (Lot 1; reconcile the strategic source of truth)

- **`docs/product-plan.md` realigned on the shipped 11-tool surface (audit
  M6/P1-6).** Sections 1 and 4 said "five typed tools today" / DDS "roadmapped"
  while six DDS/observability tools had shipped across v0.2.0-v0.4.0. Section 11's
  risk register carried a self-imposed governance gate ("any 9th tool needs
  an explicit re-scope discussion documented in this register before code
  lands") that was crossed during v0.4.0 without the discussion being
  recorded. Added a retroactive re-scope decision closing that gap: the three
  ceiling-breaking tools are accepted, the new ceiling is 11 tools, a 12th
  needs a documented re-scope.
- **User-topic `raw` decode honesty (audit C1).** README and the
  `peek_dds_samples` tool description no longer imply the `raw` fallback
  preserves the payload in `_raw_bytes_hex`: on the current user-topic raw
  path that field is empty (a `raw` status means "present but not decoded").
  Capturing the on-wire CDR bytes is stated as roadmapped rather than done.

### Changed (Lot 5; DDS adapter deduplication)

- **QoS-mismatch endpoint pairing deduplicated (audit D1/M7/P2).** The ~40
  identical lines in each adapter's `detect_qos_mismatches` (group endpoints by
  topic, pair reader x writer, build `MismatchReport`) moved to the binding-free
  `common/qos_endpoints.detect_mismatches_across_endpoints`, unit-tested without
  a binding. Each writer's QoS profile is now parsed once per topic instead of
  once per reader (fixes the O(readers x writers) re-parse). Both adapters
  delegate to it.
- **Shared `validate_domain_id` (`common/dds_helpers`).** The identical 0..232
  bound check in all four DDS adapter constructors (Cyclone, Fast, OpenDDS,
  Dust) is now defined once.
- **Cyclone discovery/sample reads switched from `take_iter` to `read_iter`
  (audit A1/P1-5).** Destructive `take` drained the builtin discovery cache,
  risking spurious lost / re-discovered participant flapping across polls;
  `read` is non-destructive: the correct choice for read-only observability.
  **Warning: Requires real-bus validation on `scripts/integration/` before release**:
  the read-vs-take semantics cannot be exercised without `cyclonedds` installed
  (the adapter is not importable in the unit environment; these edits are
  validated only by `ruff` static analysis + `py_compile`).

Baseline: 399 -> 485 passed, 24 -> 23 skipped, ruff clean, coverage 89.12%.

## [0.5.0] - 2026-05-21

### Sprint v0.5.0: Polish + validation (pre-marketing-publication)

> Branch `feat/v0.5.0-polish-and-validation`. Three sub-milestones
> (5.1 real validation, 5.2 audit closure + error polish, 5.3 docs +
> examples + repo polish). No new MCP tool, no schema change. Last
> sprint before marketing publication ; the next version bump is
> the maintainer's manual `release` commit + tag.

#### Changed (sub-milestone 5.1; Real validation)

- **CI matrix expanded** (`.github/workflows/ci.yml`) from
  `ubuntu-latest x {3.11, 3.12}` to `{ubuntu-latest, windows-latest} x
  {3.11, 3.12, 3.13}` (6 cells, `fail-fast: false`). Windows-latest
  coverage closes the largest unverified surface: TopicForge's primary
  dev environment is Windows per `CLAUDE.md` section 7 and was previously only
  hand-validated. Python 3.13 added now that wheels are stable across
  the dependency footprint.
- **`pyproject.toml` classifiers** widened with `Programming Language ::
  Python :: 3.13`.

#### Changed (sub-milestone 5.2; AdapterError polish)

- **`DDS_ONLY_ERROR_MSG`** (`adapters/common/dds_helpers.py`) rewritten
  with the v0.4.0 `CompositeAdapter` remediation path explicit, and
  with every affected ROS2 tool name listed inline. Substring
  `"DDS observability only"`, `"TOPICFORGE_DDS_BACKEND"`,
  `"TOPICFORGE_MODE"` preserved: existing `pytest.raises(match=...)`
  contracts honored. New token assertions added :
  `"CompositeAdapter"`, the 4 affected tool names.
- **CycloneDDS adapter errors** (`adapters/dds_cyclone/adapter.py`)
  enriched with the underlying exception type, the active domain id,
  the topic name (where relevant), and common-cause diagnostic hints
  (`CYCLONEDDS_URI` misconfiguration, multicast firewall, domain
  mismatch). Three sites : participant discovery, endpoint discovery,
  sample peek.
- **Fast DDS participant-init error** (`adapters/dds_fast/adapter.py`)
  enriched with an ABI-mismatch diagnostic: Fast DDS 2.6.x Python
  binding wheels frequently desynchronize with system-installed Fast
  DDS native libraries, and the v0.4.0 wording masked the cause behind
  a bare `"returned None"`. New message points at the pyproject pin
  (`fastdds>=2.6.1,<3`) and the `FastDDS_DEFAULT_PROFILES_FILE` env
  var as the two likely culprits.
- **`BagService` IO errors** (`services/bag_service.py`) wrap the
  rosbags-side exception class name into the AdapterError message so
  the LLM caller can pivot on `PermissionError` / `IsADirectoryError`
  / etc. without inspecting `__cause__`.
- **`_ROSBAGS_REQUIRED_MSG`** rewording: clarifies that `analyze_bag`
  has a v0.3.0 text-parse fallback while `peek_bag_samples` does not.

#### Closed (sub-milestone 5.2; audit triage)

- **`docs/projet-file/audit-followup-triage-v0.2.0.md` refreshed**
  against the current tree (was last touched pre-v0.3.0). Strict
  B-class : 3 items now CLOSED (B6 in v0.2.0, B9 in v0.3.0, B10 in
  v0.5.0). 6 items remain DEFER (B1-B5 hosted-context security
  hardening ; B7, B8 wire-contract decisions for v0.6+).
- **`TODO(roadmap, audit-2026-05-14)` at `services/inspector.py:76`
  retired as WONT-FIX-by-design**: the `list_topics` Inspector gate
  is intentionally empty (no MCP-level args to validate). The comment
  block is now a permanent design note rather than a roadmap pointer.

#### Added (sub-milestone 5.2; regression tests)

- **5 new tests** in `tests/test_dds_helpers.py` and
  `tests/test_bag_service.py` (regex-token assertions, not exact
  wording): pin the polished message contract without locking the
  exact prose. Baseline grows from 394 to 399 passed ; 24 skipped
  unchanged ; ruff clean.

#### Changed (sub-milestone 5.3; Documentation cascade)

- **`README.md`**: CI badge added ; tagline refreshed from "v0.3.0"
  to "v0.4.0" framing emphasising observability + bag analysis ; the
  3-row DDS tool mini-table grew into a 6-row table listing every
  DDS / observability tool with its sub-milestone of origin ;
  `peek_dds_samples` scope rewritten around `_decode_status` (full /
  partial / raw) ; telemetry contract field description switched from
  "five MVP tools" to "eleven MCP tools" ; `TOPICFORGE_DDS_BACKEND`
  Literal values listed in the config reference ; Roadmap section
  pruned of items that shipped in v0.4.0 (Composite adapter, XTypes
  Cyclone push).
- **`docs/DDS_QUICKSTART.md`**: header bumped to v0.4.0+ ; section 4
  "Single-adapter limitation (v0.3.0)" replaced by "Composite adapter
  (v0.4.0 Phase 1+)" with the new routing table ; section 5 documents the
  v0.4.0 Phase 1.5 best-effort XTypes story (no more
  `AdapterError` on user topics) ; section 6 "What's next" pruned of shipped
  items ; section 7 Troubleshooting updated.
- **`docs/TESTING.md`**: "five MCP tools" -> "eleven MCP tools" in the
  three documented occurrences ("Pick your path" table row, the lead
  paragraph, Path 1 header). New v0.4.0 tool-surface callout above
  the path picker.

#### Added (sub-milestone 5.3; new docs and examples)

- **`docs/MIGRATION_v0.3_to_v0.4.md`** (new, sibling to the existing
  `MIGRATION_v0.2_to_v0.3.md`). 8 sections : new tools, env vars and
  extras, soft-breaking schema widening (`ParticipantInfo` +4 fields,
  `BagAnalysis` +4 fields, `HealthReport.ros_backend`, `dds_backend`
  widening, `AdapterName` widening, new `TopicMetrics` /
  `ParticipantEvent` schemas), `CompositeAdapter`, `peek_dds_samples`
  user-topic story, protocol expansions, plus a quick checklist.
- **`docs/TROUBLESHOOTING.md`** (new). One section per polished
  AdapterError message, plus the cross-cutting "ROS2 CLI not found on
  PATH" / `auto` fallback to mock case. Each section quotes the
  message and lists the diagnostics in order.
- **`examples/`** (new). 4 mock-mode-runnable walkthroughs covering
  the headline value props :
  - `01-discover-ros2-stack.md`: `health_check` + `list_topics` +
    `get_topic_info` + `sample_messages`
  - `02-debug-qos-mismatch.md`: `list_participants` +
    `detect_qos_mismatches` + `peek_dds_samples` (canonical
    Reliability mismatch story)
  - `03-analyze-recording.md`: `analyze_bag` + `peek_bag_samples`
    post-mortem inspection
  - `04-monitor-topic-frequency.md`: `topic_metrics` +
    `participant_events` with the opportunistic-fill caveat
  Each example pairs an MCP-client prompt with the expected tool
  calls and a short LLM-facing synthesis.

#### Added (sub-milestone 5.3; repo polish)

- **`CONTRIBUTING.md`** (new). What contributions land easily vs hard,
  development setup, the `make check` contract, mock-first
  development convention, layer separation, pure-parser convention,
  commit conventions.
- **`SECURITY.md`** (new). Local-trust threat model, the
  read-only-by-architecture stance, vulnerability disclosure email,
  response SLAs, supported version policy.
- **`.github/ISSUE_TEMPLATE/bug_report.yml`** (new): structured form
  with version, mode, OS, Python, repro, env vars, troubleshooting
  check.
- **`.github/ISSUE_TEMPLATE/feature_request.yml`** (new):
  problem-first framing, tier disambiguation (OSS / Pro), explicit
  read-only-by-architecture acknowledgment.
- **`.github/ISSUE_TEMPLATE/config.yml`** (new): disables blank
  issues, links to `SECURITY.md`, `docs/TROUBLESHOOTING.md`,
  `docs/DDS_QUICKSTART.md`.
- **`.github/PULL_REQUEST_TEMPLATE.md`** (new): summary, test plan,
  backward-compatibility checklist (covers the 11-tool cap, schema
  additive-only invariant, telemetry 6-field contract, env var docs,
  public API removal flag).

#### Notes

- **Backward compat preserved.** Zero schema changes, zero new tools,
  zero env-var renames. The `DDS_ONLY_ERROR_MSG` substring tokens
  pinned by existing tests (`"DDS observability only"`,
  `"TOPICFORGE_DDS_BACKEND"`, `"TOPICFORGE_MODE"`) are preserved
  intact ; v0.4.0 producers and clients keep working byte-for-byte.
- **CHANGELOG entry deferred to release time.** This branch leaves
  `pyproject.toml` at `0.4.0`, `__version__` at `"0.4.0"`, and the
  `## [Unreleased]` heading populated with the polish notes above.
  The version bump and `v0.5.0` tag are the maintainer's manual
  steps after final review.

## [0.4.0] - 2026-05-15

### Sprint v0.4.0: Phase 3 (bag analysis multi-format)

> Branch `feat/v0.4.0-phase3-bag-analysis-mcap-db3-rosbag`. Three
> sub-milestones (3.1 CDR refactor, 3.2 bag_service + enriched
> analyze_bag, 3.3 peek_bag_samples). v0.3.0 stays the live PyPI
> version ; no version bump in this branch: manual maintainer step
> after final review.

#### Refactored (sub-milestone 3.1; CDR decoder commun)

- **6 vendor-agnostic helpers extracted** from `dds_cyclone/adapter.py`
  into `adapters/common/cdr_decoder.py`: `decode_dynamic_sample`,
  `iter_field_names`, `decode_field_value`, `dynamic_type_name`,
  `extract_seq_from_payload`, `extract_publish_ns_from_payload`. The
  Cyclone module keeps `_underscore` aliases pointing at the new
  common functions, so every pre-Phase-3 call site keeps working
  without rewrites.
- **22 new pure-logic tests** (`tests/test_cdr_decoder.py`) pin the
  extracted contract in isolation. The 22 Phase 1.5 XTypes Cyclone
  tests (gated by `requires_cyclonedds`) remain green through the
  full pipeline on hosts with the SDK installed. The refactor is
  transparent.

#### Added (sub-milestone 3.2; BagService + enriched analyze_bag)

- **`BagService`** (`services/bag_service.py`): facade wrapping the
  `rosbags` library (Apache 2.0, pure-Python). Two methods:
  `analyze(path)` (stats + format detection) and `peek_samples(path,
  topic, count)` (decoded samples via the shared cdr_decoder).
  Lazy import of rosbags ; methods raise a clear AdapterError when
  the library is absent (caller can fall back to v0.3.0 text-parse
  behavior on the analyze path ; sample peek requires the library).
- **`BagAnalysis` schema enriched** (`models/schemas.py`) with four
  **additive optional fields**:
  - `bag_format: Literal["mcap","db3","bag","unknown"] | None`:
    container format detected from the file extension
  - `samples_decoded_count: int`: total decoded sample count
    (analyze keeps this at 0 ; peek_bag_samples does the decoding)
  - `recording_duration_ns: int | None`: recording duration in ns
    from the bag's index when readable
  - `participants_recorded: list[ParticipantInfo]`: DDS participants
    embedded in the bag container (MCAP can ; .db3 / .bag generally
    don't; empty list is the common case)
- **`MOCK_BAG_ANALYSIS` updated** with deterministic enriched values.
  New `MOCK_BAG_SAMPLES` dict + `mock_bag_samples_for(topic, count)`
  helper for the upcoming peek_bag_samples tool.
- **`[bags]` pyproject extra** (`rosbags>=0.9`): NOT bundled in
  `[all]` for granular install. New `requires_rosbags` pytest
  marker.

#### Added (sub-milestone 3.3; peek_bag_samples MCP tool)

- **`peek_bag_samples(path, topic, count) -> SampleResult`**: the
  11th MCP tool, third explicit ceiling break. Returns up to
  `count` decoded samples for `topic` from a recorded bag file.
  Distinct from `peek_dds_samples` (live bus) and `sample_messages`
  (ROS2 graph live peek): this tool is **post-mortem inspection**.
  Same `SampleResult` shape across all three tools so LLM
  consumers read one envelope. Each sample's `_decode_status`
  annotation carries over from the shared cdr_decoder.
- **`MiddlewareAdapter` protocol** gains
  `peek_bag_samples(path, topic, count) -> SampleResult`. All
  existing adapters implement it: Mock via fixtures, Ros2CliAdapter
  via `BagService`, Cyclone / Fast / OpenDDS / Dust raise their
  existing roadmap errors (DDS-only adapters don't do bag analysis).
- **CompositeAdapter** delegates `peek_bag_samples` to the ROS half
  (bag analysis is ROS-native; MCAP is the canonical ROS2
  recording format).
- **10 new tests** in `tests/test_peek_bag_samples.py` (tool-level
  via Inspector + MockAdapter). Plus extensions to
  `test_composite_adapter`, `test_factory`, `test_opendds_adapter`,
  `test_dust_adapter`, `test_tools_integration` (MVP_TOOLS grows
  from 10 -> 11).
- **`docs/projet-file/mcp-02-spec.md` section 2**: ceiling note updated
  to 11 tools.

#### Notes (Phase 3)

- **Bag analysis is offline-only.** `analyze_bag` and
  `peek_bag_samples` read files ; they do not introspect the live
  bus. For live introspection use the existing tool set
  (`list_topics`, `peek_dds_samples`, etc.).
- **rosbags requirement.** `BagService.peek_samples` strictly requires
  `pip install topicforge[bags]`: no silent fallback for sample
  peek. `analyze_bag` retains the v0.3.0 `ros2 bag info` text-parse
  fallback on Ros2CliAdapter when rosbags is absent ; the enriched
  fields populate at their safe defaults in that path.
- **Backward compat.** Every v0.3.0 / Phase 1 / 1.5 / 2 test passes
  unchanged. BagAnalysis additive fields preserve the wire contract
  for v0.3.0 consumers ignoring them.
- **The version bump and tag are deliberate manual maintainer
  steps** after Phase 3 review. This branch leaves `pyproject.toml`
  at `0.3.0`, `__version__` at `"0.3.0"`, and the
  `## [Unreleased]` heading intact.

### Sprint v0.4.0: Phase 2 (temporal metrics + real-bus rig)

> Branch `feat/v0.4.0-phase2-metrics-and-realbus-testing`. Two
> sub-milestones (2.1 metrics, 2.2 real-bus rig). v0.3.0 stays the
> live PyPI version ; no version bump.

#### Added (sub-milestone 2.1; topic_metrics)

- **`topic_metrics(topic, window_seconds, domain_id)` MCP tool**
  (the 10th; second explicit ceiling break after `participant_events`
  in Phase 1). Returns a `TopicMetrics` payload with
  `samples_observed`, `frequency_hz_observed` (and `_declared` from
  QoS Deadline when known), `sequence_gaps_count`, `latency_ns_p50` /
  `_p95` / `_p99`, and boolean availability flags for each
  conditional metric. Window range: 1..3600 seconds, default 60.
- **`MetricsBuffer`** (`adapters/common/metrics_buffer.py`): pure-
  Python logic, RLock-protected per-topic deque with
  `MAX_SAMPLES_PER_TOPIC=1000` drop-oldest cap. Pure-Python percentile
  computation (no NumPy dependency added). Sequence gap counting
  tolerates out-of-order arrivals and dedupes duplicates.
- **`TopicMetrics`** Pydantic schema (`models/schemas.py`): frozen,
  `extra="forbid"`, every field documented with the
  None/zero-on-unavailable semantics that surfaces partial data
  cleanly to LLM callers.
- **Cyclone + Fast adapter integration**: `_peek_builtin` and
  `_peek_user_topic` paths now call `self._metrics.record(...)` for
  each sample they surface. Sequence number and publish timestamp
  extracted best-effort from the decoded payload via two new helpers
  in `dds_cyclone/adapter.py`. **Opportunistic fill caveat**: neither
  `cyclonedds` nor `fastdds` 2.6.x Python bindings expose
  at-sample-receive callbacks, so the metrics buffer accumulates
  ONLY as `peek_dds_samples` is exercised. Tool description surfaces
  this to the LLM.
- **Mock fixture** (`/dds/heartbeat_10hz`, 100 samples spaced 100 ms
  apart with synthetic 50 ms latency and sequence 0..99) plus a
  singleton topic and a cross-domain topic for filter testing.
- **~30 new tests**: `tests/test_metrics_buffer.py` (~22 pure-logic
  tests covering the helpers, ring overflow, multi-topic isolation,
  domain filtering, thread-safety smoke) + `tests/test_topic_metrics.py`
  (~12 tool-level tests via Inspector + MockAdapter).

#### Changed (sub-milestone 2.1)

- **`MiddlewareAdapter` protocol** gains `topic_metrics(topic,
  window_seconds, domain_id)`. All existing adapters implement it:
  Cyclone + Fast via the new buffer, Mock via fixtures, Ros2CliAdapter
  / OpenDDS stub / Dust stub raise their existing roadmap errors.
- **`AdapterName` Literal** unchanged (no new vendor) ; `MVP_TOOLS`
  test set grows from 9 to 10.

#### Added (sub-milestone 2.2; real-bus rig)

- **`tests/integration/`**: six scenario JSON files exercising
  the multi-vendor OMG-DDS-RTPS claim against real publishers:
  `multi_vendor_basic`, `lifecycle_tracking`, `qos_mismatch_detection`,
  `xtypes_decode`, `topic_metrics_frequency`,
  `topic_metrics_sequence_gaps`. Each scenario declares
  `required_vendors` so the runner can skip cleanly when a binding
  is missing.
- **`tests/integration/test_scenarios_schema.py`**: pure-Python
  validation of every scenario's structure. Runs in default
  `make check` (8 tests). No SDK, no Docker required.
- **`tests/integration/test_real_bus.py`**: parametrized integration
  tests, marked `@pytest.mark.integration` ; **gated out of the
  default pytest invocation**. Runs only with `pytest -m integration`
  or the labeled CI workflow.
- **`scripts/integration/scenarios_runner.py`**: Python entry point
  that probes locally installed DDS bindings, dispatches scenarios,
  and reports pass/fail per assertion. Pragmatic partial-run: missing
  vendors are skipped with a clear `[skipped]` log rather than failing
  the whole batch (D6).
- **`scripts/integration/publishers/`**: per-vendor minimal publishers
  (`cyclone_publisher.py`, `fast_publisher.py`, `opendds_publisher.py`).
  Symmetric CLI surface: `--topic`, `--rate-hz`, `--duration-s`,
  `--domain`, `--gap-at-seq`. OpenDDS publisher is a documented
  scaffold (no PyPI binding yet).
- **`scripts/integration/run-local.{sh,ps1}`**: standalone entry
  points for Linux/macOS and Windows. No Docker required.
- **`scripts/integration/docker-compose.yml` + per-vendor Dockerfiles**:
  full multi-vendor rig for CI runs and maintainer validation.
  Cyclone + Fast images build against the OSS PyPI bindings ; OpenDDS
  image documents the BYO path. `topicforge` observer image installs
  from repo source plus `[dds]` extra.
- **`.github/workflows/integration.yml`**: manual label trigger
  (`integration-tests`). Runs schema validation, builds the compose
  stack, sleeps 30 s for discovery, runs `pytest -m integration`,
  tears down. Default `ci.yml` is untouched.
- **New pytest marker** `integration`: added to `pyproject.toml`
  alongside the existing `requires_*` markers.

#### Notes (sub-milestone 2.2)

- **Validation reality.** The OSS-CI default pipeline lint-validates
  scenario JSON structure, YAML/PowerShell/bash syntax, and the
  runner's dispatch logic. It does NOT pull / build / run Docker
  images. The maintainer validates the live publisher path locally
  before merging Phase 2.2 ; the integration CI workflow is the
  shared validation surface once an SDK-rich runner host is
  available.
- **OpenDDS publisher is a scaffold.** `pyopendds` is not on PyPI as
  of 2026-05-14: the publisher script exits with an actionable
  error message, and the scenarios runner skips OpenDDS scenarios
  cleanly. Scenarios requiring OpenDDS (`multi_vendor_basic`) still
  run partial coverage of the other vendors.
- **Real-bus assertion evaluation is structural at Phase 2.2.** The
  runner dispatches scenarios and reports per-assertion status, but
  the full per-assertion verification logic (parsing TopicForge tool
  outputs, comparing against scenario `expect` clauses) is the
  maintainer's follow-up. The scaffold is ready to receive that
  wiring without re-touching the surrounding files.

### Sprint v0.4.0: Phase 1 (DDS observability core)

> Internal-only ; the branch `feat/v0.4.0-phase1-observability-core` is
> merging to `main` between v0.3.0 and v0.4.0. **No version bump in
> this section**: `pyproject.toml`/`__version__` stay at `0.3.0` until
> Phase 3 closes.

#### Added

- **`CompositeAdapter` (`adapters/composite.py`).** New wrapper that
  routes the 4 ROS2 protocol methods to a `Ros2CliAdapter` and the 3
  DDS methods (+ `participant_events`) to a DDS adapter, so a single
  process serves all 9 tools when `TOPICFORGE_MODE=live` and
  `TOPICFORGE_DDS_BACKEND=cyclone|fast` are configured together. The
  `name` collapses to `"ros2_cli+cyclone"` or `"ros2_cli+fast"` ;
  `effective_mode` reports `"live"` when either half is live.
- **Participant lifecycle tracking.** `ParticipantInfo` gains four
  additive optional fields: `first_seen_ns`, `last_seen_ns`, `status`
  (`active`/`left`/`unknown`), `seen_count`. v0.3.0 producers and
  fixtures remain valid because every new field has a safe default.
- **`LifecycleBuffer`** (`adapters/common/lifecycle.py`): RLock-protected
  ring buffer (cap 200 events, drop-oldest) shared by Cyclone (polling
  reconciliation) and Fast DDS (listener callbacks). Pure logic, no DDS
  dependency ; testable without any SDK installed.
- **`participant_events` MCP tool (the 9th).** New read-only tool that
  returns DDS participant `discovered`/`lost` events over a configurable
  window (default 300s, range 1..86400, hard cap 200 events, newest
  first). Breaks the 8-tool ceiling documented in
  `docs/projet-file/mcp-02-spec.md`, section 2 ; acknowledged in this Phase 1
  scope.
- **`HealthReport.ros_backend`** (`Literal["mock","ros2_cli","none"]`,
  default `"none"`). Symmetric to the existing `dds_backend` ; lets
  clients distinguish the ROS and DDS halves of a composed runtime.

#### Changed

- **Factory decision tree** (`services/factory.py`). Live mode now
  attempts to build both a ROS2 CLI adapter and a DDS adapter and
  wraps them in a `CompositeAdapter` when both succeed. Graceful
  degradation paths preserved: DDS missing -> ROS2 CLI alone (v0.3.0
  behavior) ; ROS2 CLI missing -> DDS-only adapter ; neither
  available -> MockAdapter.
- **`MiddlewareAdapter` protocol** (`adapters/base.py`). Gains
  `participant_events(domain_id, lookback_seconds)`. All adapters
  implement it: Mock returns deterministic fixtures, Cyclone and Fast
  read from their `LifecycleBuffer`, Ros2CliAdapter raises
  `AdapterError(_DDS_MODULE_INACTIVE_MSG)`. `AdapterName` Literal
  widened with the two composite tags.
- **`CycloneDdsAdapter.list_participants`** now feeds the
  `LifecycleBuffer` (polling delta reconciliation per call) and
  returns enriched `ParticipantInfo` snapshots with lifecycle fields.
  Discovery-sample mapping unchanged ; the returned shape is a
  superset of v0.3.0.
- **`FastDdsAdapter._DiscoveryListener`** now feeds the
  `LifecycleBuffer` from `on_participant_discovery` callbacks
  (arrival AND removal events captured natively, no polling
  reconciliation needed).

#### Notes

- **Cyclone lifecycle caveat.** Cyclone's lifecycle log is updated only
  when `list_participants` (or any internal poll of the
  `DCPSParticipant` builtin reader) is called. A participant that
  joined and left between two tool calls is invisible. The
  `participant_events` tool description makes this explicit.
- **Backward compatibility.** Zero v0.3.0 tests regress. Pydantic
  `extra="forbid"` is preserved on every model ; the four new
  `ParticipantInfo` fields have safe defaults so producers built
  against v0.3.0 schemas keep working. `ParticipantEvent` is a new
  model. Clients ignoring it continue to work.

#### Added (continued; sub-milestone 1.3)

- **`peek_dds_samples` on user-defined topics.** v0.3.0 raised an
  `AdapterError` pointing at the v0.3.x roadmap for any non-builtin
  topic ; v0.4.0 Phase 1 returns best-effort decoded samples. The
  payload may carry three reserved keys:
  - `_decode_status`: `"full"` (every IDL field decoded) /
    `"partial"` (some fields decoded, others opaque) /
    `"raw"` (binding could not resolve the dynamic XTypes; bytes
    preserved as hex).
  - `_decode_note`: short diagnostic explaining the non-`full` status.
  - `_raw_bytes_hex`: hex-encoded serialized payload preview (capped
    at 4096 hex chars ; `_raw_bytes_truncated=True` flags clipping).
- **`adapters/common/xtypes.py`**: adapter-agnostic helpers
  (`annotate_full`, `annotate_partial`, `annotate_raw`) so Cyclone and
  Fast DDS produce identical wire output regardless of binding
  capabilities. Pure logic, testable without any SDK.
- **Mock fixtures**: two new user-topic exemplars
  (`/dds/ddsforge/example` returns `_decode_status="full"` ;
  `/dds/ddsforge/opaque` returns `_decode_status="raw"`) so the
  payload shape can be exercised end-to-end without a DDS bus.

#### Changed (continued; sub-milestone 1.3)

- **`CycloneDdsAdapter.peek_dds_samples`**: user topics now go
  through `_peek_user_topic`. The path probes builtin DCPS
  Subscription / Publication readers to confirm the topic is on the
  bus (raises `AdapterError` if not), then attempts
  `cyclonedds.dynamic` dynamic decode ; on failure (most cases at
  v0.4.0 Phase 1; full decode lands in a Phase 1+ patch) returns a
  single annotated raw-bytes sample with `_decode_status="raw"`.
- **`FastDdsAdapter.peek_dds_samples`**: symmetric shape via
  `_peek_user_topic`. Fast DDS 2.6.x dynamic XTypes is partial, so
  the raw-bytes fallback is the common path ; the wire shape is
  identical to Cyclone's. Same `AdapterError` on unknown topic.
- **`peek_dds_samples` tool description** updated to remove the stale
  `"v0.2.0 stub"` wording and document the user-topic decoding story
  (full / partial / raw status, `_raw_bytes_hex` shape, binding
  caveats per backend).

### Sprint v0.4.0: Phase 1.5 (auto-detect + OSS expansion + Pro framing)

> Same `[Unreleased]` line. Phase 1.5 is the bridge between Phase 1 (DDS
> observability core) and Phase 2 (Pro tier launch). Branch
> `feat/v0.4.0-phase15-auto-detect-and-oss-expansion`. No version bump.

#### Added (sub-milestone 1.5.1; auto-detect + XTypes push)

- **8-vendor DDS auto-detect chain** in `Settings.effective_dds_backend`.
  Priority order: `rti > opensplice > coredx > intercom (Pro) > opendds
  > fast > cyclone > dust (OSS) > mock`. The chain probes each vendor's
  Python module via `importlib.util.find_spec` and returns the first
  hit. Pro vendors are probed against `topicforge_pro.adapters.<vendor>`
  rather than the upstream SDK directly: the OSS core never imports a
  commercial vendor binding.
- **`DdsBackend` / `ResolvedDdsBackend` Literals widened** with 5 new
  vendor values: `opensplice`, `coredx`, `intercom`, `opendds`, `dust`.
- **`HealthReport.dds_backend` Literal widened** symmetrically. Soft-
  breaking on the producer side ; strict JSON-schema clients pinned to
  v0.3.0 will reject the new values unless their schema regenerates.
- **`AdapterName` Literal widened** with the 5 new vendor tags and 6
  new composite tags (`ros2_cli+rti`, `ros2_cli+opensplice`, etc.).
- **Cyclone XTypes pipeline**. `_try_dynamic_decode_cyclone` no longer
  always returns None: it probes `cyclonedds.dynamic` entry points,
  resolves a type id via `DCPSPublication`, builds a typed reader, and
  decodes samples field-by-field with per-construct granularity.
  Fallback to `annotate_raw` when any step fails. Real-bus validation
  awaits user feedback ; the v0.4.0 Phase 1 plumbing is now a
  best-effort path rather than always-fallback.
- **Fast DDS `TypeObjectFactory` probe** in `_try_dynamic_decode_fast`.
  The Fast DDS 2.6.x dynamic XTypes Python surface is incomplete, so
  the probe currently returns None and the raw-bytes fallback fires ;
  the structural change makes the v0.5 decode patch a small follow-up.

#### Added (sub-milestone 1.5.2; OSS adapter expansion)

- **`OpenDdsAdapter`** (`adapters/dds_opendds/`): stub. Probes
  `pyopendds` via `find_spec` ; `is_available()` reports False when the
  binding is absent (always, in 2026-05-14, since the package is not on
  PyPI). All 8 protocol methods raise `AdapterError(_OPENDDS_ROADMAP_MSG)`.
- **`DustDdsAdapter`** (`adapters/dds_dust/`): even thinner stub.
  `is_available()` always False ; Dust DDS is Rust-native with no
  maintained Python binding.
- **`[dds-opendds]` and `[dds-dust]` pyproject extras** as placeholder
  pins (`pyopendds>=0.1`, `dust-dds-python>=0.1`). Neither package is
  on PyPI today ; the extras anchor the auto-detect probe and will
  resolve cleanly when upstream releases.
- **`[dds-all-oss]` union extra**: `topicforge[dds] + dds-opendds +
  dds-dust` for users explicitly opting into the stubs.
- **New pytest markers** `requires_opendds`, `requires_dust`: auto-skip
  when bindings are absent (same convention as `requires_cyclonedds`).

#### Added (sub-milestone 1.5.3; pro/ scaffold + docs)

- **`tests/test_pro_hook.py`**: exercises `_try_register_pro` with a
  fake `topicforge_pro` module injected via `sys.modules`. Three test
  cases: package absent -> False, package present + register succeeds ->
  True + side-effect captured, register raises -> False + error logged.
  Locks in the OSS-side contract that the Pro plugin must implement.
- **`docs/pro.md` rewritten** with the corrected tier framing: OSS =
  community DDS adapters (Cyclone, Fast; OpenDDS / Dust stubs) + base
  ROS2 introspection ; Pro = commercial DDS adapters (RTI Connext,
  OpenSplice [legacy], CoreDX, InterCOM) + URDF / Bag Anomaly /
  Multi-bag Diff diagnostics. Pricing terms preserved at $12/$19.

#### Changed (sub-milestone 1.5.3)

- **README.md install section** enriched. Documents the 8-vendor auto-
  detect chain and the OSS/Pro tier split. Removed the stale "v0.3.0
  limitation: single adapter at a time" since Phase 1 shipped the
  composite adapter ; the README now describes the actual behavior.

#### Notes

- **Pro tier scaffold lives in `pro/` (gitignored).** The Phase 1.5
  branch ships RtiConnextAdapter + OpenSplice stub + license skeleton
  + register.py in the working tree under `pro/`, but `.gitignore`
  excludes the folder entirely: none of those files are committed.
  The maintainer copies them to a private backup repo before launching
  the `topicforge-pro` PyPI package.
- **Backward compatibility**: zero v0.3.0 + Phase 1 test regressions.
  `TOPICFORGE_DDS_BACKEND=mock | cyclone | fast | rti | auto` continue
  to resolve as before ; the 5 new vendor values are additive.
- **No 10th MCP tool.** Phase 1.5 is structural / docs only: the
  9-tool surface (8 v0.3.0 + `participant_events` from Phase 1) is
  preserved exactly.
- **Pyopendds and dust-dds-python pins are placeholders.** A user
  running `pip install topicforge[dds-opendds]` today will see an
  install failure ; this is expected and the extras exist to anchor
  the auto-detect framework for the day the upstream packages ship.

## [0.3.0] - 2026-05-14

### Strategic

- **OMG DDS-RTPS multi-vendor positioning.** TopicForge is now framed as a read-only DDS-RTPS observer that joins the bus via one of two OSS Python participants (Eclipse CycloneDDS or eProsima Fast DDS) and observes every conformant vendor on the domain (RTI Connext, OpenDDS, CoreDX, Dust DDS in Rust, etc.) regardless of host language. See `docs/dds-interop-matrix.md` for the canonical statement and `docs/projet-file/references/omg-dds-interop-2025-05-08.xlsx` for the OMG May 2025 interop reference. The earlier v0.2.0/v0.3.0 phasing (Cyclone-only at v0.3.0, RTI at v0.3.0+) is collapsed: multi-vendor OSS lands together at v0.3.0 ; RTI Pro defers to v0.4.0+.

### Added

- **Real `CycloneDdsAdapter`**: replaces the v0.2.0 stub with actual CycloneDDS discovery via `cyclonedds.builtin.BuiltinDataReader` on the DCPS participant/subscription/publication builtin topics. QoS extracted via `Policy.*` class-name introspection. `take_iter(timeout=...)` for bounded discovery. Lazy-imported via `services.factory`.
- **`FastDdsAdapter`** (`adapters/dds_fast/`): new parallel OSS adapter built on the `fastdds` Python bindings (BSD-licensed, eProsima). Duck-typed listener subclass aggregates discovery callbacks under an RLock. Bounded `discovery_wait_ms=1500` warm-up after participant creation. `close()` releases the participant via `factory.delete_participant`.
- **`adapters/common/dds_helpers.py`** contains vendor-neutral helpers: `canonicalize_vendor_id` (OMG vendor_id -> canonical tag), `format_guid` (16-byte GUID -> `xxxxxxxx.xxxxxxxx.xxxxxxxx.xxxxxxxx` canonical text), `DDS_ONLY_ERROR_MSG` (shared remediation message). Pure functions, no DDS dependency.
- **Pyproject extras refactor**: `[dds-cyclone]` (Cyclone only), `[dds-fast]` (Fast only), `[dds]` (both; union of v0.2.0 `[dds]` behavior plus fastdds).
- **`TOPICFORGE_DDS_BACKEND=fast`**: new accepted value alongside `mock | cyclone | rti | auto`.
- **3rd mock participant** with `vendor="fast"` exercises the multi-vendor positioning in mock mode.
- **New pytest marker** `requires_fastdds`: auto-skips without the binding.
- **35+ new tests**: `tests/test_dds_helpers.py`, `tests/test_fast_adapter.py`, `tests/test_dds_cross_vendor.py` (parametrized on both adapters), 6 analyzer edge cases in `tests/test_qos_analyzer.py`, 4 new health tests for DDS field population.

### Changed

- **`ParticipantInfo.vendor` Literal widened** to include `"fast"`. **Strict JSON-schema clients pinned to v0.2.0 will reject `vendor:fast` unless their schema is regenerated.** Standard MCP clients reading tool descriptions dynamically are unaffected.
- **`HealthReport.dds_backend` Literal widened** to include `"fast"`. Same soft-breaking caveat.
- **`AdapterName` Literal widened** to include `"fast"` (internal type; no wire impact).
- **`DdsBackend` / `ResolvedDdsBackend`** widened to include `"fast"`.
- **`Settings.effective_dds_backend` auto resolution** now prefers Fast DDS > Cyclone DDS > Mock (was Cyclone > Mock in v0.2.0). v0.2.0 users with only `cyclonedds` installed see no change: Fast is unimportable on their host. Users with both SDKs installed will see Fast selected. Reflects the OMG May 2025 interop matrix.
- **`HealthService.report()`** now populates `dds_backend`, `dds_domain_id`, `middleware_available`: previously returned schema defaults regardless of configuration (v0.2.0 latent bug). `middleware_available` is checked via `importlib.util.find_spec` on the active backend's Python module.
- **`Ros2CliAdapter._DDS_MODULE_INACTIVE_MSG`** updated to mention both `pip install topicforge[dds-cyclone]` and `pip install topicforge[dds-fast]` remediation paths.
- **`Inspector` DDS topic validator relaxed**: `detect_qos_mismatches` and `peek_dds_samples` now accept DDS-native topic names (no leading `/` required, `::` separators allowed) via a new `_validate_topic_name_dds`. The strict ROS2 validator stays in place for the 5 ROS2 graph methods. Resolves audit-2026-05-14 "Refactor opportunities" #5.

### Removed

- **`CycloneDdsAdapter` v0.2.0 stub**: `_NOT_IMPLEMENTED_MSG` and the corresponding `test_dds_surface_raises_stub_error_in_v020` test removed. The 3 DDS methods now serve real results when cyclonedds is installed.

### Notes

- **OMG-DDS-RTPS interoperability** is the protocol guarantee that makes multi-vendor observation work: see `docs/dds-interop-matrix.md` and `docs/projet-file/references/omg-dds-interop-2025-05-08.xlsx`.
- **v0.3.0 `peek_dds_samples` limitation**: full-fidelity on the 4 builtin DCPS topics (`DCPSParticipant`, `DCPSSubscription`, `DCPSPublication`) ; arbitrary user topics raise an `AdapterError` with a v0.3.x roadmap pointer (XTypes/IDL discovery is the missing piece, both for Cyclone via `cyclonedds.dynamic.get_types_for_typeid` and for Fast DDS via XTypes remote type lookup).
- **`pip install topicforge[dds]` in v0.3.0** now pulls BOTH `cyclonedds` and `fastdds` (was Cyclone only in v0.2.0). Use `[dds-cyclone]` or `[dds-fast]` for single-vendor installs. See `docs/MIGRATION_v0.2_to_v0.3.md`.
- **Fast DDS pin**: `fastdds>=2.6.1,<3`. Fast DDS 3.x binding wheels for Python 3.11+ on Windows / Linux are not yet stable. Bump when upstream cuts stable 3.x wheels.
- **No code change to the 5 ROS2 tools**: `health_check`, `list_topics`, `get_topic_info`, `sample_messages`, `analyze_bag` behave identically to v0.2.0. The `mode_effective` wire contract is unchanged ; `health_check` now populates DDS fields correctly.
- **Full migration guide**: `docs/MIGRATION_v0.2_to_v0.3.md`.

## [0.2.0] - 2026-05-14

### Strategic

- **Mono-MCP pivot (2026-05-14).** The 3-to-5-MCP pack draft is collapsed into a 2-product strategy: TopicForge umbrella (this product; covers ROS2 today and grows a DDS observability module starting with v0.2.0), and **DatasetForge** (Vision Dataset Inspector, the standalone second product). The previously-planned standalone DDS-MCP product is cancelled: its spec is reframed as the TopicForge DDS module spec at `docs/projet-file/mcp-02-spec.md`. Motif: solo-maintenance cost of two parallel repos was the binding constraint, and ROS2 / DDS are the same problem shape (typed pub/sub graph introspection) under the same `MiddlewareAdapter` superset.

### Added

- **DDS module: 3 new MCP tools.** `list_participants(domain_id)`, `detect_qos_mismatches(topic)`, `peek_dds_samples(topic, count)`. All read-only ; surface DDS-layer introspection distinct from the ROS2 graph tools. `peek_dds_samples` is deliberately separate from `sample_messages`: different layer, different semantics, distinct tool description so an LLM picks the right one in a mixed setup.
- **`MiddlewareAdapter` protocol** in `adapters/base.py`: superset of the historical `RosAdapter`. Covers both ROS2 graph methods and the new DDS methods under one contract. `RosAdapter` retained as a backward-compat alias (`RosAdapter = MiddlewareAdapter`).
- **`CycloneDdsAdapter`** (`adapters/dds_cyclone/`): lazy-imported only when `TOPICFORGE_DDS_BACKEND=cyclone` and the optional `cyclonedds` extras are installed (`pip install topicforge[dds]`). **v0.2.0 ships a protocol-compliant stub**: the lazy import, `is_available()`, and routing all work ; the 3 DDS methods raise `AdapterError` with a v0.2.x roadmap pointer. The real CycloneDDS discovery (builtin topics, QoS pair extraction, typed reader for samples) lands in a v0.2.x patch. The mock backend (`TOPICFORGE_DDS_BACKEND=mock`, the default) exposes a working DDS surface against deterministic fixtures in the meantime.
- **3 new Pydantic schemas**: `QosProfile` (Reliability / Durability / History / Deadline at MVP), `ParticipantInfo` (GUID, vendor, hostname, domain_id), `MismatchReport` (incompatible_policies + severity). All frozen, `extra="forbid"`.
- **Pure analyzer** `adapters/common/qos_analyzer.detect_mismatches`: module-level pure function, testable against synthesized QoS pairs without any DDS middleware installed.
- **Environment variables**:
  - `TOPICFORGE_DDS_BACKEND`: `mock | cyclone | rti | auto`, default `mock`. The DDS module is opt-in ; existing ROS2-only setups behave unchanged.
  - `TOPICFORGE_DDS_DOMAIN_ID`: DDS domain id observed (0..232), default `0`.
- **Mock fixtures enriched**: 2 deterministic DDS participants, two-topic scenario (`/dds/well_matched` and `/dds/qos_mismatch`) exercising `detect_qos_mismatches` end-to-end.
- **`pyproject.toml` extras**: `[dds]` pulls `cyclonedds>=0.10` ; `[all]` aliases `[dds]`. `pip install topicforge` keeps the core + mock only (zero install impact on ROS2-only users).

### Changed

- **`TopicInfo` schema soft-breaking.** Three additive optional fields (`reader_count: int | None`, `writer_count: int | None`, `qos_profile: QosProfile | None`): all default `None`. Producer side: code constructing `TopicInfo` directly is unaffected (defaults compile). **Strict MCP clients that validated v0.1.x responses against the `TopicInfo` schema with `additionalProperties: false` will reject v0.2.0 responses unless their schema is regenerated. Standard MCP clients that read tool descriptions dynamically are unaffected.**
- **`HealthReport` schema soft-breaking**, same shape. Three additive optional fields (`dds_backend`, `dds_domain_id`, `middleware_available`) with safe defaults (`"none"`, `None`, `False`).
- **`RosAdapter` renamed to `MiddlewareAdapter`** in `adapters/base.py`. The old name remains as an alias (`RosAdapter = MiddlewareAdapter`) ; existing imports `from topicforge.adapters import RosAdapter` still type-check. The `Ros2CliAdapter.name` value moves from `"live"` to `"ros2_cli"`: internal tag, separate from the MCP-wire `mode_effective` field which keeps its `Literal["mock", "live"]` contract.
- **`Settings`** gains `dds_backend` and `dds_domain_id` fields with safe defaults (`"mock"`, `0`). Existing `Settings(...)` constructors are unaffected.
- **`Ros2CliAdapter` DDS methods raise `AdapterError`** with a clear remediation path (`pip install topicforge[dds]` + `TOPICFORGE_DDS_BACKEND=cyclone`). This is the v0.2.0 MVP limitation D6 (single-adapter-at-a-time) ; a composite adapter that delegates per-tool is a v0.2.x roadmap item.

### Internal

- `parse_topic_info` and `parse_bag_info` parsers : `mode_effective` kwarg typed as `EffectiveMode` (`Literal["mock", "live"]`) rather than the broader `AdapterName`, cleanly separating the wire-facing mode from the implementation tag.
- New pytest marker `requires_cyclonedds` for tests that need the SDK. Auto-skips otherwise via `pytest.importorskip`.

### Notes

- **`cyclonedds` is optional.** Default installs (`pip install topicforge`) are unchanged from v0.1.2 in dependency footprint. Only `pip install topicforge[dds]` pulls the bindings (`cyclonedds>=0.10`).
- **No code change to the 5 ROS2 tools**: `health_check`, `list_topics`, `get_topic_info`, `sample_messages`, `analyze_bag` behave identically to v0.1.2. The wire contract (`mode_effective: Literal["mock", "live"]`) is unchanged.
- **v0.2.0 MVP limitation**: single adapter at a time. Users select ROS2 introspection (default) or DDS observability via `TOPICFORGE_DDS_BACKEND=cyclone`, not both simultaneously. The unselected half raises `AdapterError` with a remediation pointer. A composite adapter delegating per-tool category is a v0.2.x roadmap item.

## [0.1.2] - 2026-05-13

### Fixed

- `sample_messages` now returns real publish-time timestamps in live mode for `Header`-stamped messages. The live adapter previously shelled out to `ros2 topic echo --once`, which does not emit timestamps, so `MessageSample.timestamp_ns` was always `0`. The invocation is now `ros2 topic echo --csv --once`, whose flattened CSV exposes `header.stamp.sec` and `header.stamp.nanosec` as the first two columns for any `Header`-stamped message; the new `parse_csv_echo` parser reconstructs `timestamp_ns = sec * 1_000_000_000 + nanosec` and strips those two columns out of the payload. **Headerless message types** (e.g. `std_msgs/String`, `geometry_msgs/Twist`) still return `timestamp_ns=0`: they carry no embedded timestamp. Surfacing the rmw **receive** timestamp (rather than the publish-time `header.stamp`) for arbitrary message types remains a roadmap item tied to the future `rclpy`-backed adapter.

### Added

- **`mode_effective` on every tool response (schema, soft-breaking additive).** `TopicInfo`, `SampleResult`, and `BagAnalysis` now carry a required `mode_effective: Literal["mock", "live"]` field. A new `effective_mode` property on the `RosAdapter` protocol is the single source of truth; `Ros2CliAdapter` returns `"live"`, `MockAdapter` returns `"mock"`, services thread it through at result construction time. **Producer side**: Python code constructing these models directly must now supply `mode_effective`: models are `frozen=True, extra="forbid"` with no default. **Client side (over MCP)**: additive. An MCP client consuming JSON sees one extra key per response and is unaffected unless it strictly validates against the v0.1.1 schema with a no-extra-keys assumption.
- **DDS-MCP spec** (`docs/projet-file/mcp-02-spec.md`). Strategic draft for MCP 02 at the time: safety-first read-only DDS observability across middleware vendors (CycloneDDS OSS, RTI Connext Pro tier). Five tools, `MiddlewareAdapter` protocol, mock + cyclone + rti + auto modes. Reviewer notes appended (2026-05-13): wrong cross-reference in section 11 flagged. (Reframed the next day as a TopicForge module after the mono-MCP pivot; see [0.2.0] Strategic section.)
- **DatasetForge spec** (`docs/projet-file/mcp-03-spec.md`). Vision Dataset Inspector spec, re-slotted to MCP 03 after competitive-landscape audit that surfaced zero non-ROS DDS-MCP projects and made a standalone DDS-MCP the stronger MCP 02 candidate. Reviewer notes appended (2026-05-13): contradictory section 11 phrasing and two implicitly-resolved open questions flagged.

### Changed

- **Safety-first read-only repositioning.** README and `docs/product-plan.md`, section 1, now lead with "read-only by architecture, not by configuration" as the primary identity. Pack candidate list updated: MCP 02 reframed to a non-ROS DDS observability MCP (later folded into TopicForge itself by the 2026-05-14 multi-vendor reframe; see v0.3.0 entry); DatasetForge slides to MCP 03. Strategic context in `docs/product-plan.md`, sections 4 and 8 (DDS-complete horizon).
- **Internal API.** `Inspector.sample_messages` now returns a `SampleResult` envelope (previously a `list[MessageSample]`). The MCP-facing tool handler is reduced to a thin pass-through. No effect on the tool's wire-level response shape (handlers already wrapped the list into `SampleResult`), but flagged here for anyone importing `Inspector` directly outside this repo.

### Internal

- Docstring fix in `parse_csv_echo`: the example output now shows post-strip payload keys as `col_0`, `col_1` (the parser re-indexes from `col_0` after dropping the two timestamp columns), matching the existing test in `tests/test_live_adapter_parse.py`.

## [0.1.1] - 2026-05-13

### Added

- **Opt-in anonymous usage telemetry** behind `TOPICFORGE_TELEMETRY=on` (default: off). When enabled, each MCP tool call emits a single event with six fields only: `tool_name`, `latency_ms`, `mode`, `version`, `session_id` (random UUID per process, never persisted), and `success`. No topic names, message bodies, bag paths, hostnames, or environment data ever leave the process. See the README "Telemetry" section for the full payload contract and opt-out instructions.
- `src/topicforge/telemetry/` module with `TelemetryClient`, `TelemetryEvent`, and an `instrument()` decorator that wraps tool handlers with timing + emit. When telemetry is off, `instrument()` is the identity function: zero overhead and zero possibility of a network call in the OFF code path.
- Pluggable `Transport` callable; v0.1.1 ships a structured-log transport. A future S3-backed HTTP endpoint will plug in without touching tool handlers.
- 29 telemetry tests covering: default-off behaviour, env var parsing (`on`/`1`/`true`/`yes`/`enabled` vs anything else), payload shape and key allowlist, payload privacy (user input never leaks), session id stability and per-process uniqueness, transport-exception isolation, decorator signature preservation, and end-to-end verification that the OFF code path never invokes the transport.

### Changed

- `Settings` gained a `telemetry_enabled: bool` field.
- `build_app(...)` accepts optional `telemetry` and `telemetry_transport` parameters for test injection.
- `register_tools(...)` now takes a `TelemetryClient`.
- `.env.example` documents `TOPICFORGE_TELEMETRY`.
- README adds a `Telemetry` section and updates the Security model note to reflect opt-in telemetry availability.

## [0.1.0] - 2026-05-12

Initial MVP release of TopicForge: ROS Topic Inspector & Bag Analyzer MCP server.

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

[Unreleased]: https://github.com/yaniswav/TopicForge/compare/v0.5.3...HEAD
[0.5.3]: https://github.com/yaniswav/TopicForge/compare/v0.5.2...v0.5.3
[0.5.2]: https://github.com/yaniswav/TopicForge/compare/v0.5.1...v0.5.2
[0.5.1]: https://github.com/yaniswav/TopicForge/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/yaniswav/TopicForge/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/yaniswav/TopicForge/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/yaniswav/TopicForge/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/yaniswav/TopicForge/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/yaniswav/TopicForge/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/yaniswav/TopicForge/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/yaniswav/TopicForge/releases/tag/v0.1.0
