# TopicForge DDS Module — Specification

> Module spec for the DDS observability layer of the TopicForge umbrella.
> Built on a `MiddlewareAdapter` protocol that generalizes the existing
> `RosAdapter`. Working name internally: **DDS module**. Filename retained
> as `mcp-02-spec.md` for historical continuity with the earlier pack draft.

> **Status.** Reframed twice on 2026-05-14. Authored 2026-05-13 as a
> standalone MCP 02 spec (working name DdsForge) ; reframed the next
> day to a module-internal-to-TopicForge spec after the mono-MCP
> pivot, then reframed again the same day around OMG-DDS-RTPS
> multi-vendor positioning when `docs/dds-interop-matrix.md` was
> published and the v0.3.0 sprint scope locked. Tool surface, protocol
> shape, and architectural decisions are intact ; positioning evolved
> from *"standalone DdsForge"* → *"module of TopicForge umbrella"* →
> *"multi-vendor OMG-DDS-RTPS module of TopicForge umbrella"*. The
> binding constraint of the first reframe was solo-maintenance cost
> of two parallel repos ; the binding constraint of the second was
> the unique strategic angle — no other ROS-MCP project covers
> non-ROS DDS, and the OMG protocol guarantee means one adapter set
> reaches every conformant vendor on the bus.

This is a strategic-internal document (`docs/projet-file/`), not a
user-facing README. Conventions reuse TopicForge verbatim ; only genuine
deltas inside the umbrella are flagged. When a section says *"same as
the ROS2 side"* or *"inherited from TopicForge"*, that is load-bearing —
do not re-invent.

---

## 1. Identity

The TopicForge DDS module — *Safety-first read-only **OMG DDS-RTPS**
observability inside the TopicForge umbrella — multi-vendor by protocol.*

**OMG-DDS-RTPS conformant.** The DDS module joins the bus as a read-only
DDS-RTPS participant via one of two OSS Python bindings — Eclipse
CycloneDDS or eProsima Fast DDS — and observes every other conformant
participant on the domain regardless of vendor (RTI Connext, OpenDDS,
CoreDX, Dust DDS in Rust, etc.) or host language (C, C++11/14/17/20,
Rust, Java, .NET, Python, Ada, anything with a DDS-RTPS binding). This
is the OMG-DDS promise: interoperability at the wire level. See
`topicforge/docs/dds-interop-matrix.md` for the canonical multi-vendor
positioning and `topicforge/docs/projet-file/references/omg-dds-interop-2025-05-08.xlsx`
for the OMG May 2025 interop reference snapshot.

Where DDS users today reach for vendor-specific tools (RTI Admin Console,
Cyclone DDS CLI, `rtiddsspy`) to inspect a live bus, the DDS module
exposes the same observation surface — participants, topics, QoS
profiles, samples, mismatch diagnostics — through three new MCP tools
added to the existing TopicForge tool surface, all driven by an LLM
agent. Read-only by **architecture**, not by configuration: there is no
write path to misconfigure, no permission system to audit, no liability
conversation to have. The `MiddlewareAdapter` protocol does not even
expose a write method.

**Pack position.** Not a separate product. The DDS module is the next
roadmapped module of TopicForge (MCP 01 of a 2-MCP pack). DatasetForge —
the new MCP 02 of the same pack — covers the training-data layer for
the same audience and is a separate repo / separate PyPI name. Full
DatasetForge spec at `mcp-03-spec.md` (filename retained for historical
continuity ; the slot is MCP 02).

Target users — same concentric circles as `topicforge/docs/product-plan.md §3`
but tilted further toward the strategic core:

- **Strategic core.** DDS-native engineering teams in defense, aerospace,
  automotive AUTOSAR Adaptive, naval, and industrial integration. They
  cannot ship a tool that publishes back to the bus or modifies QoS in
  production ; an LLM agent that *only* observes is the only acceptable
  shape. After the mono-MCP pivot these teams install one product
  (`pip install topicforge[dds]`) and get both ROS2 and DDS coverage
  under one license, one telemetry contract, one safety review.
- **Tactical adjacent.** Mid-size robotics and middleware teams running
  ROS2 alongside non-ROS DDS components (a common pattern in industrial
  multi-stack environments). The DDS module complements the ROS2 tools
  in the same install.
- **Funnel.** DDS-curious developers prototyping with CycloneDDS. Free
  tier captures them and drives discoverability.

The strategic point is not *"another DDS console"*. The point is **an
AI-agent-driven DDS diagnostic assistant** that handles the *"why
doesn't this subscriber receive anything?"* / *"what QoS mismatch is
preventing communication?"* / *"compare observed topology to expected"*
class of questions that current GUI tools require manual interpretation
for.

---

## 2. MVP scope of the DDS module (locked, 3 new tools)

The DDS module adds **exactly 3 new MCP tools** to the TopicForge
surface, plus extends 2 existing tool payloads. The 5 ROS2 tools shipped
in v0.1.2 are unchanged.

| Tool                    | Status      | Purpose                                                                                  |
| ----------------------- | ----------- | ---------------------------------------------------------------------------------------- |
| `health_check`          | Extended    | Adds middleware detection (Cyclone / RTI / mock) and observed domain id to the payload   |
| `list_topics`           | Extended    | Adds optional reader / writer counts and effective QoS profile per endpoint              |
| `list_participants`     | **New**     | Discovered DDS participants on the configured domain, with vendor and hostname           |
| `detect_qos_mismatches` | **New**     | Analyzer: surface reader/writer QoS incompatibilities that prevent communication         |
| `peek_dds_samples`      | **New**     | Recent serialized samples on a DDS topic, with type info and receive timestamps          |

`peek_dds_samples` is deliberately distinct from the existing
`sample_messages` (ROS2 graph) — the two tools serve different layers
(ROS2 message bus vs raw DDS topic) and the LLM-facing description must
make the distinction explicit. No silent overload.

Do not expand. New tools follow the `add-mcp-tool` skill at
`.claude/skills/topicforge/add-mcp-tool/SKILL.md`. The hard ceiling at
the umbrella level is 8 MCP tools (5 ROS2 + 3 DDS) ; a 9th tool requires
an explicit re-scope decision documented in
`topicforge/docs/product-plan.md §11`.

The write path — publishing samples, calling RPCs, modifying QoS at
runtime, controlling domains — is **out of scope by architecture,
permanently**. This is the load-bearing positioning commitment. Defense
and aerospace acceptance depends on it.

Pro-tier candidates already identified (do not ship in module MVP):
RTI Connext live adapter, recorded-bus replay analysis, multi-domain
comparison, QoS-policy-pack heuristics beyond the core four
(Reliability, Durability, History, Deadline → Liveliness, Ownership,
Partition, TimeBasedFilter, LatencyBudget).

---

## 3. Architecture (locked)

Inherited from `topicforge/CLAUDE.md §3` verbatim — same layer
separation, same files, same `server/` / `tools/` / `services/` /
`models/` / `telemetry/` / `config/` layout. The DDS module adds:

- `src/topicforge/adapters/dds_cyclone/` — new sibling to the existing
  `ros2_live/` and `ros2_mock/` directories. Houses
  `CycloneDdsAdapter` (OSS). The DDS mock surface is exposed by
  `MockAdapter` itself (extended fixtures in `ros2_mock/fixtures.py`),
  not by a separate adapter — one mock for the whole tool surface.
- `MiddlewareAdapter` protocol in `src/topicforge/adapters/base.py` —
  generalization of the existing `RosAdapter`. `RosAdapter` becomes
  an alias / sub-shape of `MiddlewareAdapter` for backwards
  compatibility.

The `MiddlewareAdapter` protocol — designed cross-vendor from day one:

```python
@runtime_checkable
class MiddlewareAdapter(Protocol):
    name: AdapterName  # "mock" | "ros2_cli" | "cyclone" | "rti"

    @property
    def effective_mode(self) -> Literal["mock", "live"]: ...

    def is_available(self) -> bool: ...

    # ROS2 side (existing — kept on the umbrella)
    def list_topics(self) -> list[TopicInfo]: ...
    def get_topic_info(self, topic: str) -> TopicInfo: ...
    def sample_messages(self, topic: str, count: int) -> SampleResult: ...
    def analyze_bag(self, path: str) -> BagAnalysis: ...

    # DDS side (new — added by this module)
    def list_participants(self) -> list[ParticipantInfo]: ...
    def detect_qos_mismatches(self, topic: str | None = None) -> list[MismatchReport]: ...
    def peek_dds_samples(self, topic: str, count: int) -> list[SampleResult]: ...
```

The `mode_effective` propagation through every tool response is the
existing TopicForge v0.1.2 contract — `mode_effective` is a required
field on every response carrier and applies to DDS payloads identically.

Adapter implementations at module MVP:

- **`MockMiddlewareAdapter`** — deterministic fixtures modeling a
  multi-participant scenario across vendors (`cyclone`, `fast`, plus
  one fallback entry to exercise the `"unknown"` enum tag). One
  well-matched topic and one mismatched topic exercise
  `detect_qos_mismatches`. Always available. The mock fixture is the
  demo asset and the test substrate.
- **`CycloneDdsAdapter`** (OSS, Eclipse CycloneDDS) — built on the
  `cyclonedds` Python bindings (BSD-licensed). Joins the bus as a
  read-only DDS-RTPS participant. Polling-style discovery via
  `cyclonedds.builtin.BuiltinDataReader` on the DCPS builtin topics.
  Pulled in by `pip install topicforge[dds-cyclone]` or the union
  `pip install topicforge[dds]`.
- **`FastDdsAdapter`** (OSS, eProsima Fast DDS) — built on the
  `fastdds` Python bindings (BSD-licensed). Joins the bus as a
  read-only DDS-RTPS participant. Listener-driven discovery via
  `DomainParticipantListener` callbacks with RLock-protected state.
  Pulled in by `pip install topicforge[dds-fast]` or the union
  `pip install topicforge[dds]`.
- **`RtiConnextAdapter`** (Pro tier, v0.4.0+ roadmap) — uses RTI's
  `rti.connextdds` bindings. Auto-detected at runtime via the
  existing `_try_register_pro(mcp)` in `server/app.py` and gated by
  the existing `TOPICFORGE_LICENSE_KEY` (the same key that unlocks
  ROS2 Pro features will unlock RTI ; one key, one umbrella).

OpenSplice is EOL and explicitly not pursued. The two OSS Python
participants (CycloneDDS, Fast DDS) and one Pro participant (RTI
Connext) cover the practical multi-vendor footprint TopicForge needs ;
they observe every other DDS-RTPS conformant vendor on the bus via the
OMG protocol guarantee — adding direct adapter support for additional
vendors requires explicit demand documented in
`topicforge/docs/product-plan.md §11`.

**Pure analyzers live module-level.** `detect_mismatches(reader_qos,
writer_qos) -> list[MismatchReason]` is a pure function called by
adapters after they have collected the QoS profiles. This mirrors
TopicForge's *"pure parsers"* convention (see
`.claude/skills/topicforge/write-pure-parser/SKILL.md`) — testable
against synthesized QoS pairs without any DDS middleware installed.

---

## 4. Runtime modes (DDS module knobs)

The umbrella keeps **one runtime mode knob** — `TOPICFORGE_MODE` — for
the overall server. The DDS module adds **DDS-side modifiers** under
the `TOPICFORGE_DDS_*` namespace to avoid polluting the main mode
selector with vendor-specific values.

| DDS knob                        | When to use                                            | Backend                                          |
| ------------------------------- | ------------------------------------------------------ | ------------------------------------------------ |
| `TOPICFORGE_DDS_BACKEND=mock`   | Development, demos, CI, screencasts (default)          | Deterministic fixtures (two-participant scenario)|
| `TOPICFORGE_DDS_BACKEND=cyclone`| Force CycloneDDS (skip RTI even if Pro is installed)   | `cyclonedds` Python bindings                     |
| `TOPICFORGE_DDS_BACKEND=rti`    | Force RTI (Pro tier, license required)                 | RTI Python bindings (license-gated)              |
| `TOPICFORGE_DDS_BACKEND=auto`   | Detect best available ; fall back to mock              | RTI > Cyclone > mock                             |

DDS backend resolution lives in `Settings.effective_middleware`
(`config/settings.py`), parallel to the existing `Settings.effective_mode`
for ROS2. Final operational fallback (when an adapter instantiates but
`is_available()` returns False) lives in `services/factory.py`. **One
place each** — same constraint as the ROS2 side.

DDS module env vars:

- `TOPICFORGE_DDS_BACKEND` — `mock | cyclone | rti | auto`, default `mock`
  (i.e. DDS module is opt-in ; the ROS2 side keeps its existing default).
- `TOPICFORGE_DDS_DOMAIN_ID` — DDS domain id to observe, default `0`.
  Validated as `0 <= int <= 232`.
- `TOPICFORGE_TELEMETRY` — **shared with the ROS2 side**, same six-field
  payload contract as the existing telemetry. OFF means verified no-op
  via the existing unit test. No DDS-specific telemetry env var — one
  umbrella, one telemetry switch.
- `TOPICFORGE_LICENSE_KEY` — **shared with the ROS2 Pro side**. The same
  `tfp_*`-prefixed key that unlocks ROS2 Pro features unlocks the
  `RtiConnextAdapter`. One key for the umbrella. The OSS core ignores
  this variable.

Naming pattern: `TOPICFORGE_<SUBSYSTEM>_<KNOB>` for module-scoped knobs.
DatasetForge will use its own `DATASETFORGE_*` namespace because it is
a separate product.

---

## 5. Stack (locked)

Same as `topicforge/CLAUDE.md §5`:

- **Python 3.11+**
- **`mcp >= 1.0.0`** (FastMCP)
- **`pydantic >= 2.6`** — `extra="forbid"` and `frozen=True` via the
  shared `_CONFIG` in `models/schemas.py`
- **`pytest`** for tests ; mock-mode tests never require any DDS
  middleware installed
- **`ruff`** for lint and format
- **Hatchling** for build

DDS-specific additions (declared as optional extras in `pyproject.toml`,
not hard deps):

- **`cyclonedds`** — Python bindings, BSD-licensed, Eclipse Foundation,
  installable via `pip install topicforge[dds-cyclone]`. Lazy-imported
  by `CycloneDdsAdapter` ; absence keeps the umbrella green in
  mock-only or Fast-only installs.
- **`fastdds`** — Python bindings, BSD-licensed, eProsima, installable
  via `pip install topicforge[dds-fast]`. Lazy-imported by
  `FastDdsAdapter`. SWIG-generated bindings ; pinned as
  `fastdds>=2.6.1,<3` to absorb the upstream 2.x ABI line.
- **`pip install topicforge[dds]`** — union alias pulling both
  CycloneDDS and Fast DDS, for users who want either-or-both at runtime.
- **`rti.connextdds`** — pinned by the Pro package only, never by the
  OSS core. Absence does not break the install.

---

## 6. Engineering principles

Inherit verbatim from `topicforge/CLAUDE.md §6` (clean architecture,
type hints, structured outputs, graceful degradation, mock mode is
mandatory, no giant files, no premature abstraction). Three module-
specific deltas:

- All middleware access goes through adapters. Handlers never import
  `cyclonedds` or `rti` ; services never construct DDS entities. The
  `MiddlewareAdapter` protocol is the only cross-vendor abstraction
  that earns its keep at MVP.
- Pure analyzers separate from middleware I/O. `detect_mismatches` is a
  pure function over `(reader_qos, writer_qos)` ; the adapter collects
  the QoS pairs and calls it. Tests pin the analyzer against
  synthesized QoS pairs without any middleware installed (same pattern
  as TopicForge's `parse_topic_list` etc.).
- Read-only by architecture, not by configuration. No method on
  `MiddlewareAdapter` ever takes a *"publish"*, *"modify"*, or *"write"*
  parameter. The protocol shape itself enforces the safety promise.

---

## 7. Phase 1 targets — DDS module versioning maps to TopicForge releases

Module versioning is **not independent** of TopicForge. The DDS module
ships in TopicForge releases, in this order:

- **TopicForge v0.2.0 — protocol generalization + DDS stub + 3 tools.**
  ✓ Shipped 2026-05-14. `RosAdapter` generalized into `MiddlewareAdapter`
  in `adapters/base.py` with `RosAdapter` retained as a backward-compat
  alias. `MockAdapter` extended with DDS fixtures, `CycloneDdsAdapter`
  as a protocol-compliant stub (lazy import + is_available + DDS
  methods raise with a roadmap pointer), 3 new MCP tools
  (`list_participants`, `detect_qos_mismatches`, `peek_dds_samples`),
  `pyproject.toml` extra `[dds]` pulling `cyclonedds>=0.10`.
  Soft-breaking schema changes to `TopicInfo` and `HealthReport`
  (additive optional fields). The 8-tool ceiling activates.
- **TopicForge v0.3.0 — real Cyclone + real Fast DDS + multi-vendor
  framing (current sprint).** Replace the v0.2.0 Cyclone stub with the
  actual CycloneDDS discovery (builtin DCPS readers for participants
  and endpoints, QoS pair extraction, typed reader for samples on
  builtin DCPS topics first ; arbitrary user-topic peek defers to a
  v0.3.x patch). Ship `FastDdsAdapter` as a parallel OSS backend on
  `fastdds` (listener-driven discovery via
  `DomainParticipantListener`, RLock-protected state, bounded
  discovery startup delay). Pyproject extras refactor:
  `dds-cyclone`, `dds-fast`, `dds` (union). Reframe DDS module
  positioning around OMG-DDS-RTPS multi-vendor — see
  `topicforge/docs/dds-interop-matrix.md` for the canonical
  statement and the OMG May 2025 interop reference. Same 3 MCP
  tools ; vendor-neutral wire contract. Soft-breaking
  `ParticipantInfo.vendor` Literal expansion to include `"fast"`.
- **TopicForge v0.3.x patches.** IDL/XTypes discovery to extend
  `peek_dds_samples` to arbitrary user topics (today: builtin DCPS
  topics only). Extended QoS coverage (Liveliness, Ownership,
  Partition, TimeBasedFilter, LatencyBudget). Composite adapter
  delegating per-tool category so users can run ROS2 + DDS surfaces
  simultaneously.
- **TopicForge v0.4.0+ — `RtiConnextAdapter` in Pro tier.** Same
  `_try_register_pro(mcp)` pattern. BYO RTI license. No new MCP
  tools ; same 3 DDS tools, new backend.

The earlier v0.2.0 / v0.3.0 split (proposed before the mono-MCP pivot
of 2026-05-14 and further refined by the multi-vendor framing when
`docs/dds-interop-matrix.md` was published) is collapsed into the
phasing above: stub ships at v0.2.0, multi-vendor OSS ships at v0.3.0
together, RTI Pro defers to v0.4.0+.

When a Phase 1 item ships, retire the matching `# TODO(roadmap):` tag
in code and the corresponding entry in
`topicforge/docs/product-plan.md §5`.

---

## 8. Risk register — DDS module specifics

Pack-wide risks (MCP churn, time dilution, competitive landscape,
scope creep within the umbrella) inherit from
`topicforge/docs/product-plan.md §11`. DDS-module-specific risks:

- **Cyclone binding stability.** `cyclonedds` Python bindings have
  evolved since 2023 ; pin a major version and run smoke tests on
  every release. Mitigation: keep Cyclone-specific code behind the
  `MiddlewareAdapter` protocol so a binding rev is an adapter patch,
  not a TopicForge rewrite.
- **Fast DDS binding stability.** `fastdds` Python bindings are
  SWIG-generated and version-coupled to the C++ Fast DDS major line.
  Pin `fastdds>=2.6.1,<3` ; defer Fast DDS 3.x bindings until upstream
  cuts stable wheels for Python 3.11+ on Windows / Linux. Same
  mitigation as Cyclone: vendor-specific code stays behind the
  `MiddlewareAdapter` protocol.
- **Cross-vendor wire interop assumption.** The multi-vendor framing
  relies on the OMG DDS-RTPS interoperability promise (see
  `docs/dds-interop-matrix.md` and the May 2025 OMG matrix). Edge
  cases at the application layer between specific implementations
  (e.g. Dust DDS ↔ OpenDDS partial interop in the OMG report) do
  not affect what TopicForge sees — its participant is conformant
  — but the user-facing language must not claim TopicForge can
  bridge inter-vendor application-level traffic problems.
- **RTI licensing complexity.** Pro tier customers must bring their
  own RTI Connext license. This is a procurement conversation, not a
  self-serve flow. Mitigation: Pro tier is intentionally low-volume /
  high-value ; do not optimize for self-serve until volume warrants.
- **QoS mismatch false positives.** The analyzer must err on the side
  of completeness without flooding output. Mitigation: classify
  mismatches as `incompatible` (definitely blocks comm) vs `risky`
  (may degrade but not block). Tests pin both categories against
  fixture QoS pairs.
- **Tool-surface bloat inside the umbrella.** Adding 3 DDS tools to
  the existing 5 ROS2 tools must not push the LLM-facing surface past
  the point where Claude / Cursor / Cline tool selection becomes
  noisy. Mitigation: the 8-tool ceiling is documented in §2 and in
  `topicforge/docs/product-plan.md §11` ; tool descriptions are
  written to make ROS2-vs-DDS scope explicit so the LLM picks the
  right one without ambiguity.
- **No write path == no demo virality.** Unlike `robotmcp/ros-mcp-server`
  which can demo Claude piloting an Unitree dog, the DDS module
  cannot do a viral *"AI controls the X"* video. Mitigation: lean
  into the diagnostic story — a screencast of an LLM identifying a
  QoS mismatch in 10 seconds versus a human reading vendor docs is a
  different kind of compelling, and the right kind for the audience.
- **Defense / aerospace require enterprise sales motion.** RFPs,
  security reviews, export controls. Mitigation: enterprise tier is
  gated by the pack-wide G3 trigger (three open-source logos +
  inbound-with-budget) — do not pursue outbound enterprise until OSS
  validation lands.

---

## 9. Monetization — folded into TopicForge Pro

Same three-tier model as `topicforge/docs/product-plan.md §9`. The
DDS module folds into the existing tiers without introducing new
billing :

- **Free (MIT).** `MockMiddlewareAdapter` + `CycloneDdsAdapter`
  (CycloneDDS is BSD-licensed, redistribution is clean). Shipped
  via `pip install topicforge[dds]`. Single line at the README bottom
  pointing to Pro.
- **Pro (commercial license).** `RtiConnextAdapter` and Pro-only DDS
  analyzers live in the existing `topicforge_pro` package. **Same
  license key, same pricing** as the existing ROS2-side Pro :
  `TOPICFORGE_LICENSE_KEY` unlocks both. $12/month for the first ten
  early-access customers, $19/month after, annual invoice for
  procurement-bound customers. No Pro feature ships until 10
  early-access slots are reserved (pack-wide rule).
- **Enterprise (future, Phase 3+).** Hosted endpoint with auth,
  multi-vendor BYO license, security-review-friendly deployment
  artifacts, support SLA. Defense / aerospace enterprise motion.
  Gated by pack-wide G3 trigger.

The DDS audience has higher willingness to pay than ROS2 hobbyists,
but the absolute volume is smaller. The umbrella decision means one
funnel feeds both — no separate Pro page for DDS.

---

## 10. What to avoid

Inherit verbatim from `topicforge/CLAUDE.md §11` (no generic
framework, no UI, no hardcoded paths, tests have no external deps,
no vague exceptions, respect layer separation, never break mock
mode). DDS-module-specific additions :

- Do not publish anything onto the bus. Ever. There is no debug
  mode, no test mode, no override. The protocol does not even
  expose a write method. This is the safety contract.
- Do not depend on RTI being installable in the OSS test suite.
  RTI bindings are commercial and license-gated ; CI runs without
  them. The OSS `cyclone` and `mock` modes must both stay green
  forever.
- Do not silently coerce QoS policies between vendors' enums.
  Mismatch detection compares against canonical DDS spec values,
  not RTI-specific or Cyclone-specific extensions. Vendor extensions
  get explicit handling with `# vendor: rti` / `# vendor: cyclone`
  comments.
- Do not chase the GUI consoles' feature parity. The wedge is
  MCP-native LLM grounding for diagnostic conversations, not a
  richer Python API or a richer UI.
- Do not split the DDS module into a separate PyPI package. The
  mono-MCP pivot of 2026-05-14 is the binding decision — one product,
  one install, one license key.

---

## 11. Open questions (resolve before v0.2.0 / v0.3.0 implementation)

Deliberate gaps for the maintainer — not roadmap items, but pre-kickoff
decisions.

- **`cyclonedds` version pin.** Determined at v0.3.0 implementation
  kickoff after a smoke test against the current bindings on
  Windows + Linux.
- **QoS mismatch taxonomy.** Lock the policies checked at v0.3.0
  (recommended: Reliability, Durability, History, Deadline — the four
  that explain > 80 % of real-world *"subscriber doesn't receive"*
  cases). Defer Liveliness, Ownership, Partition, TimeBasedFilter,
  LatencyBudget to v0.3.x patches.
- **`peek_dds_samples` vs `sample_messages` description ergonomics.**
  Verify in a Claude session that the LLM picks the right tool when
  asked *"show me recent samples on topic /foo"* in a mixed ROS2+DDS
  setup. The tool descriptions must make the layer distinction
  unambiguous without the user explicitly disambiguating.

Resolved by the mono-MCP pivot of 2026-05-14 (no longer open
questions) :

- ~~Package name.~~ The DDS module ships inside `topicforge` ;
  no separate `ddsforge` package.
- ~~Repo layout.~~ Same repo as TopicForge ; no separate
  `github.com/yaniswav/DdsForge`.
- ~~Pack-shared infrastructure.~~ Non-decision at 2 products ;
  fork-and-tweak to DatasetForge is acceptable.

Resolved by the multi-vendor framing of 2026-05-14
(`docs/dds-interop-matrix.md`) :

- ~~OSS vendor count.~~ Two OSS Python adapters ship at v0.3.0
  (CycloneDDS, Fast DDS). RTI is Pro v0.4.0+. OpenSplice (EOL),
  CoreDX (closed-source), Dust DDS (Rust, no Python binding) are
  out of scope for the OSS core but are observed via the OMG
  protocol guarantee like every other conformant vendor on the bus.
- ~~Auto-resolution order.~~ Fast > Cyclone > Mock — Fast DDS is
  OMG May 2025 interop-validated on 47/47 pairs ; Cyclone is the
  long-standing default. v0.2.0 users with only `cyclonedds`
  installed keep the same effective behavior since Fast is not
  importable on their host ; the new ordering only matters when
  both SDKs are installed.

Newly open (resolve before v0.3.x patches) :

- ~~**XTypes / IDL discovery feasibility.**~~ **Resolved in v0.4.0
  Phase 1 (2026-05-14).** `peek_dds_samples` on user-defined topics no
  longer raises ; it returns best-effort decoded samples with
  `_decode_status` annotation (`"full"` / `"partial"` / `"raw"`). The
  raw fallback path is the common one at Phase 1 because dynamic
  XTypes resolution in both `cyclonedds.dynamic` and `fastdds` 2.6.x
  is partial ; a Phase 1+ patch will extend the dynamic decode branch
  once we collect bus feedback. The four builtin DCPS topics keep the
  v0.3.0 structured-payload shape unchanged for backward compat.
- **Fast DDS discovery startup delay.** Listener-driven discovery in
  Fast DDS needs a bounded warm-up before queries return useful
  data. v0.3.0 uses a hardcoded `discovery_wait_ms=1500`. Promote
  to a constructor argument or env var if real-world deployments
  need to tune this.

---

## 12. Why this is now a TopicForge module (decision log)

For the strategic record, dated entries:

**2026-05-13 — DdsForge as standalone MCP 02.**

- The ROS-MCP category became crowded between mid-2025 and early 2026 —
  at least six projects (`robotmcp/ros-mcp-server` 1.2k stars, ROSBag
  MCP arXiv 2511.03497, `araitaiga/rosout_mcp`, `kakimochi`'s ROS 2
  MCP, `lpigeon/ros_mcp_server`, `TakanariShimbo/rosbridge-mcp-server`).
  TopicForge needs an axis that is not *"another ROS-MCP"*.
- The non-ROS DDS-MCP category is empty as of 2026-05-13. None of the
  six known ROS-MCP projects target non-ROS DDS users. Existing DDS
  tooling is vendor-specific GUI / CLI, not MCP-driven.
- The maintainer's background (Thales C++/DDS) is materially relevant —
  credibility on this axis is structural, not invented.
- Hook B (safety-first read-only — see
  `topicforge/docs/product-plan.md §1`) generalizes trivially from
  *"ROS2 introspection"* to *"DDS observability"*. The architectural
  commitment is identical.
- The `RosAdapter` protocol generalizes to `MiddlewareAdapter` with
  zero rework — TopicForge's methods (`list_topics`, `get_topic_info`,
  `sample_messages`) are DDS-native concepts that ROS2 happens to
  expose.

**2026-05-14 — Mono-MCP pivot: DDS becomes a TopicForge module.**

- The 3-to-5-MCP pack draft underestimated solo-maintenance cost.
  Running two parallel repos (TopicForge + DdsForge) with their own
  CI, release workflows, PyPI listings, marketplace fiches, and
  marketing was the binding constraint.
- ROS2 and DDS are the same problem shape — typed pub/sub graph
  introspection — and the `MiddlewareAdapter` superset already
  generalizes cleanly. There is no architectural reason for two
  products.
- The umbrella keeps the differentiator: TopicForge is now the only
  MCP that covers both ROS2 and DDS under one read-only-by-architecture
  contract. Competitors who add ROS2 features individually do not
  reach the DDS audience ; competitors who add DDS support do not
  exist yet.
- Cost of the pivot: zero published code change (DdsForge was never
  shipped). Cost is documentary only — this spec, the product plan,
  the strategic docs, the launch posts. All edited in one branch
  (`docs/mono-mcp-pivot`, 2026-05-14).

The right MCP 02 is **not** a separate product — it is the second
product of a 2-product pack, and that product is DatasetForge. DDS
support is the next module of TopicForge itself.

---

## 13. References

- `topicforge/CLAUDE.md` — operating manual ; §3 / §5 / §6 / §11
  inherit verbatim.
- `topicforge/docs/product-plan.md` — pack vision (§4), DDS module
  roadmap (§8), monetization (§9), risk register (§11), decision
  gates G2 / G3 (§12).
- `topicforge/docs/dds-interop-matrix.md` — canonical multi-vendor
  positioning, derived from the OMG May 2025 interop matrix. This
  is the source-of-truth doc for the OMG-DDS-RTPS framing of the
  module ; user-facing copy in README / launch posts cites it
  rather than reinventing the language.
- `topicforge/docs/projet-file/references/omg-dds-interop-2025-05-08.xlsx`
  — OMG May 2025 interop snapshot (in-repo reference copy ; see
  `topicforge/docs/projet-file/references/README.md` for the
  convention).
- `topicforge/docs/pro.md` — pricing terms reused for the Pro tier.
- `topicforge/.claude/skills/topicforge/` — `add-mcp-tool`,
  `write-pure-parser`, `update-mock-fixtures`, `release-checklist`
  apply directly to the DDS module work.
- `docs/projet-file/mcp-03-spec.md` — DatasetForge spec (the new
  MCP 02 of the 2-MCP pack).
