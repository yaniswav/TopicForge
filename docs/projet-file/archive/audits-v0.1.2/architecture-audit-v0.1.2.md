# Architecture audit — TopicForge v0.1.2

## TL;DR

- The 5-layer pipeline (server → tools → services → adapters → models) is genuinely clean: every cross-layer import respects the contract, no adapter is imported outside `services/factory.py`, and `tools/handlers.py` never sees `subprocess` or `shutil`.
- Naming around runtime mode is the single biggest piece of drift: five distinct names (`Settings.mode`, `Settings.effective_mode`, `RosAdapter.effective_mode`, `RosAdapter.name`, `HealthReport.mode` vs `requested_mode`, response field `mode_effective`) coexist with three closed-set `Literal` types that all collapse to `"mock" | "live"`. Coherent today, soft-breakable the day a DDS adapter lands.
- DDS-module extensibility is mostly cheap (protocol generalization, factory rewire, new tools registration), but the closed `AdapterName = Literal["mock", "live"]` and the closed `mode_effective: Literal["mock", "live"]` on every response model are wire-visible bottlenecks that conflict with the spec's intent that `name` go to `"cyclone" | "rti"` while `effective_mode` stays binary.
- Real cross-service coupling: `services/health.py` imports `MAX_SAMPLE_COUNT` from `services/inspector.py`. Minor today, a smell to clean up before DDS adds its own per-tool caps.
- Test surface is healthy (114 test functions across 8 files, all mock-only). No fixture or test path requires `ros2` on PATH. Pure parsers are isolated in `adapters/ros2_live/adapter.py` from line 173 onward and tested independently.

## ✅ Strengths

- **Layer separation is enforced by imports, not just by docstring.** `tools/handlers.py:21-35` imports only `models`, `services`, `telemetry`; `services/inspector.py:12-13` imports `adapters.base` (the protocol) but never a concrete adapter. The only place `Ros2CliAdapter` and `MockAdapter` co-occur is `services/factory.py:12-13`.
- **`build_app` is a textbook composition root.** `server/app.py:23-65` wires settings → adapter → services → telemetry → tools in 40 lines, with `_try_register_pro` isolated as an `ImportError`-tolerant side path (`server/app.py:68-87`).
- **Pure parsers live at module level with kwarg-only injection of policy values.** `parse_topic_list`, `parse_pub_sub_counts`, `parse_topic_info`, `parse_csv_echo`, `parse_bag_info` (`adapters/ros2_live/adapter.py:189-380`) take `mode_effective` and `fallback_*` as kwargs — testable without a ROS2 install.
- **`extra="forbid"` + `frozen=True` is centralized.** A single `_CONFIG = ConfigDict(extra="forbid", frozen=True)` at `models/schemas.py:15` is applied to all six schemas — no drift possible.
- **Telemetry OFF path is structurally a no-op.** `telemetry/client.py:132-133` returns `lambda fn: fn` when disabled — zero network code path, the wire contract pin lives in `tests/test_telemetry.py`.
- **Input validation is concentrated at the gate.** `services/inspector.py:67-98` owns topic-name regex and path validation; adapters can assume well-formed inputs (`adapters/ros2_live/adapter.py:68-122`).
- **Mock fixture mirrors the live adapter's failure shape.** `adapters/ros2_mock/adapter.py:54-72` rejects non-bag extensions explicitly so mock demos surface the same UX failure as a real ROS2 install.
- **Cross-platform subprocess hygiene.** `adapters/ros2_live/adapter.py:140-143` resolves the executable with `shutil.which` so Windows `.cmd`/`.bat` shims work without `shell=True`.

## ⚠️ Refactor opportunities (post-v0.2, by impact, descending)

1. **`AdapterName = Literal["mock", "live"]` will not survive DDS.** `adapters/base.py:15` defines a single closed set used for both `adapter.name` and `adapter.effective_mode`. The spec at `mcp-02-spec.md:130-148` expects `name: "mock" | "ros2_cli" | "cyclone" | "rti"` while `effective_mode: "mock" | "live"`. These need to split into two separate `Literal`s before v0.2.0 — and the producer-side `mode_effective: Literal["mock", "live"]` on `TopicInfo`/`SampleResult`/`BagAnalysis` (`models/schemas.py:38,131,153`) is what blocks a future `effective_mode == "live_dds"` if you ever need that. Today's docstring at `adapters/base.py:37-46` already calls this out — the type doesn't follow.
2. **Drop the `MAX_SAMPLE_COUNT` import from `services.inspector` into `services.health`.** `services/health.py:11` reaches sideways for a constant that conceptually belongs to a shared `services.constants` module (or a `Settings` field). When DDS adds per-tool caps (`peek_dds_samples` count?), this pattern duplicates.
3. **`Mode` / `ResolvedMode` / `AdapterName` could collapse to a single `RuntimeMode = Literal["mock", "live"]` alias plus `RequestedMode = Literal["mock", "live", "auto"]`.** Three modules each declare their own. `config/settings.py:15-16` has `Mode`/`ResolvedMode`; `adapters/base.py:15` has `AdapterName`. They are semantically the same closed set.
4. **`HealthReport.mode` and `HealthReport.requested_mode` are typed `str`, not `Literal`.** `models/schemas.py:161-162`. The five other modes in the codebase are `Literal`-typed; `HealthReport` is the outlier. Tighten before DDS extends the payload (`mcp-02-spec.md:81-86`).
5. **Topic-name regex enforces ROS2 conventions but DDS topic names can carry `::` and other separators.** `services/inspector.py:24` `_TOPIC_NAME_RE` will reject valid DDS topics. The `MiddlewareAdapter` generalization needs the validator at the service layer to either dispatch per backend or relax the rule — neither obvious from the current shape.
6. **`mode_effective` is duplicated on three response models (`TopicInfo`, `SampleResult`, `BagAnalysis`) but not on `HealthReport` or `MessageSample`.** `models/schemas.py:38,131,153` vs absent on `HealthReport` (`models/schemas.py:156-178`) and `MessageSample` (`models/schemas.py:41-79`). The asymmetry is defensible (Health reports mode in `mode`/`requested_mode`; samples nest inside `SampleResult`) but is not documented anywhere — worth a one-line note on `_MODE_EFFECTIVE_DESC`.
7. **`Inspector.list_topics` is a 1-line pass-through with no validation; `Inspector.analyze_bag` validates then calls.** `services/inspector.py:43-44,63-64`. The "symmetric gate" docstring (`services/inspector.py:27-34`) justifies it, but the asymmetry between `get_topic_info` (validates) and `list_topics` (does not) will read as inconsistent when new tools land.
8. **`parse_echo_yaml` at `adapters/ros2_live/adapter.py:246-270` is dead code in v0.1.2.** `sample_messages` now uses `parse_csv_echo` exclusively (`adapters/ros2_live/adapter.py:103`). The `TODO(roadmap)` at line 244-245 talks about `rclpy` obsoleting it, but it is already obsoleted by `parse_csv_echo`. Either remove it or document why it stays.

## 🔴 Anti-patterns urgents

Aucun.

## 📈 Extensibility analysis — DDS module readiness

**Already good.**

- The adapter package layout (`adapters/ros2_live/`, `adapters/ros2_mock/`) is sibling-ready: adding `adapters/dds_live/` and `adapters/dds_mock/` is mechanical.
- `RosAdapter` is a `@runtime_checkable` `Protocol` (`adapters/base.py:26-56`) — extending it to `MiddlewareAdapter` superset with optional DDS-side methods will work; existing callers stay type-correct.
- `services/factory.py:19-39` is small enough that adding a `TOPICFORGE_DDS_BACKEND` branch is a 10-line patch.
- `_try_register_pro(mcp)` (`server/app.py:68-87`) is already the extension point the spec assumes for `RtiConnextAdapter`.
- Pure parser convention generalizes verbatim to pure DDS analyzers (`detect_mismatches(reader_qos, writer_qos)`); `mcp-02-spec.md:174-180` already plans on this.

**What blocks.**

- `AdapterName = Literal["mock", "live"]` (`adapters/base.py:15`) is the same literal used for both adapter identity and effective mode. The spec at `mcp-02-spec.md:131` wants `name: AdapterName  # "mock" | "ros2_cli" | "cyclone" | "rti"`. Either rename current `name="live"` to `"ros2_cli"` (a wire-visible change anywhere this surfaces — `server/app.py:62` logs `adapter.name`) or split the literal into two types. Decide before v0.2.0.
- Closed `Literal["mock", "live"]` on every response model's `mode_effective` (`models/schemas.py:38,131,153`) means even a producer-side broadening (e.g. `"live_dds"`) breaks Pydantic validation. Wire-stable if you keep the binary collapse intent — but document it as a load-bearing decision in `models/schemas.py`.
- `HealthReport.mode: str` (`models/schemas.py:161`) becomes the place to expose middleware info per `mcp-02-spec.md:84`. The `str` field is too loose; without a stricter type, Pydantic gives no guard against an adapter inventing a free-form value.

**Soft breaks on the wire.**

- Renaming `adapter.name` from `"live"` to `"ros2_cli"` (per spec) will show up in any client that logged or persisted the value. No tool response carries `adapter.name` today — it surfaces only in `server/app.py:62` log line — so the break is operationally invisible if you decide to keep `"live"` as a backwards-compat alias.
- Extending `TopicInfo` and `SampleResult` with DDS-side fields per `mcp-02-spec.md:84-86` (reader/writer counts, effective QoS profile per endpoint) requires Pydantic models with `extra="forbid"` to be additive only — current `qos_reliability: str | None` at `models/schemas.py:34-37` is the only QoS knob, and the planned DDS additions need to coexist without renaming it.

**Cost estimate.** Phase 1 of v0.2.0 (protocol prep, no new tools): ~1 day. The literal-type split is the actual work; the rest is naming hygiene.

## 🧭 Long-term direction signals

1. The naming triple — `Settings.mode` / `Settings.effective_mode` / response `mode_effective` / adapter `effective_mode` / `HealthReport.mode` + `requested_mode` — is internally coherent today but a future maintainer hitting it cold will not infer the contract. A short paragraph in `adapters/base.py` or `models/schemas.py` explaining "five different names, one closed set, here is why" is overdue.
2. `Settings` is a `@dataclass(frozen=True, slots=True)` (`config/settings.py:27-28`) while every other contract object is a Pydantic model. The pivot to a Pydantic `BaseSettings` (or pydantic-settings) becomes attractive when DDS adds 2-3 more env vars (`TOPICFORGE_DDS_BACKEND`, `TOPICFORGE_DDS_DOMAIN_ID`, `TOPICFORGE_LICENSE_KEY`).
3. `_BAG_EXTENSIONS = frozenset({".mcap", ".db3", ".bag"})` lives in `adapters/ros2_mock/adapter.py:19` but is conceptually a domain constant (the live adapter relies on `ros2 bag info` failing instead — a deliberate asymmetry per `adapters/ros2_live/adapter.py:116-122`). When `analyze_bag` grows a native MCAP reader (Phase 1 roadmap, `product-plan.md:65`), this set will need to be shared.
4. The pass-through `Inspector.list_topics` (`services/inspector.py:43-44`) is defended as a "symmetric gate". Fine. But once DDS adds `list_participants` and `detect_qos_mismatches`, the Inspector will either own all of them (mounting domain concerns it currently delegates) or split into `Ros2Inspector` / `DdsInspector`. The latter is the cleaner path and worth deciding now.
5. The closed v0.1.2 contract is essentially production-ready for OSS — the deprecation risk is concentrated in the two `Literal` types in `models/schemas.py` and `adapters/base.py`. Once external users land, those become wire-breaking changes.

## Conventions audit

- Pure parsers: **PASS** — all parsers module-level in `adapters/ros2_live/adapter.py:189-380`, kwarg-only policy injection, no I/O.
- Pydantic discipline: **PASS** — `_CONFIG = ConfigDict(extra="forbid", frozen=True)` at `models/schemas.py:15` shared by all six schemas; `model_copy(update=...)` used at `adapters/ros2_mock/adapter.py:51`.
- Layer separation: **PASS** — verified by import scan; only `services/factory.py:12-13` imports concrete adapters.
- Type hints (no lazy `Any`): **PASS** — the only `Any` usage is in `telemetry/client.py:28,32,53,64,137` for the `Transport = Callable[[dict[str, Any]], None]` contract and the generic decorator `F = TypeVar("F", bound=Callable[..., Any])`. Both are justified.
- Cross-platform safety: **PASS** — `shutil.which` for executable resolution (`adapters/ros2_live/adapter.py:140`), `PurePosixPath`/`PureWindowsPath` for path suffix detection (`adapters/ros2_mock/adapter.py:64-66`), `subprocess.run` with argument list (no `shell=True`).
- `TODO(roadmap)` hygiene: **WARN** — markers across `handlers.py`, `adapter.py` (live), and `fixtures.py` match `product-plan.md §5` and §8, but `parse_echo_yaml`'s `TODO(roadmap)` at `adapters/ros2_live/adapter.py:244` is stale (the function is already unreferenced after v0.1.2's switch to `parse_csv_echo` at line 103).
