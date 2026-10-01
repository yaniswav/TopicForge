# TopicForge

<!-- mcp-name: io.github.yaniswav/topicforge -->

[![PyPI version](https://img.shields.io/pypi/v/topicforge.svg)](https://pypi.org/project/topicforge/)
[![CI](https://github.com/yaniswav/TopicForge/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/yaniswav/TopicForge/actions/workflows/ci.yml)
[![Python versions](https://img.shields.io/pypi/pyversions/topicforge.svg)](https://pypi.org/project/topicforge/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/yaniswav/TopicForge/blob/main/LICENSE)
[![Read-only by architecture](https://img.shields.io/badge/safety-read--only_by_architecture-2563eb)](https://github.com/yaniswav/TopicForge#security-model)

> **The safety-first read-only MCP for ROS2 robotics: graph introspection, bag analysis, and multi-vendor OMG DDS-RTPS observability.** TopicForge lets AI agents inspect your ROS2 graph, recorded bag files, and the raw DDS layer beneath ROS, without ever publishing back to the bus. **Eleven typed read-only tools** (5 ROS2 graph + 3 DDS + 3 observability/bag) share a single Pydantic envelope so an LLM caller reads one schema across the whole stack. It joins a domain through one OSS Python participant (Eclipse CycloneDDS from PyPI, or eProsima Fast DDS with a binding you build from eProsima's sources) and reads the builtin discovery topics the OMG protocol standardizes, so it observes **every conformant vendor on the wire** (RTI Connext, OpenDDS, CoreDX, Dust DDS in Rust) with no proprietary binding. See [`docs/dds-interop-matrix.md`](docs/dds-interop-matrix.md) for the canonical multi-vendor positioning and the OMG May 2025 interop reference.

TopicForge is a production-minded MCP (Model Context Protocol) server that lets AI agents (such as Claude) inspect ROS2 topics, analyze ROS bag files, and (since v0.2.0) observe the raw DDS layer through a clean, structured tool interface. It is read-only by **architecture**, not by configuration: there is no write path to misconfigure, no permission system to audit, no liability conversation to have. The MCP client can see the robot stack; it cannot touch it.

This stance matters because the ROS-MCP space is no longer empty: general-purpose ROS-MCP servers exist that let an LLM publish topics, call services, and command robots. That shape is fine for demos; it is untenable for production fleets, defense systems, automotive AUTOSAR Adaptive surfaces, or anything safety-certified. TopicForge is the read-only alternative for those audiences, plus the robotics developers, ML/CV engineers, and teams that want their AI tooling to *understand* their robotics stack without commanding it.

## Why it exists

LLM agents are good at reasoning over text, but ROS2 introspection lives in a CLI + DDS world they cannot directly reach. Without grounding, an LLM will hallucinate topic names, message types, and bag contents. TopicForge bridges that gap with a small, well-typed set of MCP tools, all read-only, all returning frozen Pydantic schemas that a downstream agent can parse without ambiguity:

| Tool              | Purpose                                                |
| ----------------- | ------------------------------------------------------ |
| `health_check`    | Environment & mode introspection                       |
| `list_topics`     | Discover the ROS graph                                 |
| `get_topic_info`  | Structured info for a single topic                     |
| `sample_messages` | Peek recent messages on a topic (publish-time timestamps for `Header`-stamped types) |
| `analyze_bag`     | Summarize a `.mcap` / `.db3` / `.bag` recording        |

Outputs are structured, JSON-serializable, and stable across runtime modes - they look the same whether the server is talking to a real robot or to its built-in mock fixtures. Every response except `health_check` carries a `mode_effective` field (`"live"` or `"mock"`) so a downstream LLM can tell a real graph from the demo fixtures without re-reading `health_check`. `health_check` itself reports `mode` (the mode of the adapter actually serving requests) next to `requested_mode`.

## 30-second demo without ROS2

The mock adapter ships deterministic fixtures for a small differential robot (LIDAR + RGB camera). You do not need ROS2 installed to try the full tool surface - a clean venv on Python 3.10 or later is enough.

```bash
pip install topicforge
TOPICFORGE_MODE=mock python -m topicforge
# Windows PowerShell: $env:TOPICFORGE_MODE="mock"; python -m topicforge
```

Point any MCP client (Claude Desktop, see below) at this server and ask it to *list the topics* or *analyze `/tmp/demo.mcap`* - every tool returns realistic, typed payloads.

## Quickstart

```bash
pip install topicforge
python -m topicforge --help
TOPICFORGE_MODE=mock python -m topicforge
```

## Architecture

```
+----------------------+
|   MCP client (LLM)   |
+----------+-----------+
           |  (stdio, MCP protocol)
           v
+----------+-----------+
|  topicforge.server   |   FastMCP entrypoint, lifecycle, tool registration
+----------+-----------+
           |
           v
+----------+-----------+
|  topicforge.tools    |   Thin handlers - validate, delegate, serialize
+----------+-----------+
           |
           v
+----------+-----------+
| topicforge.services  |   Inspector / Health - orchestration & validation
+----------+-----------+
           |
           v
+----------+-----------+
| topicforge.adapters  |   ros2_live  - subprocess wrappers over `ros2` CLI
|                      |   ros2_mock  - deterministic fixtures
+----------------------+
```

Layers are strictly separated:

- **`server/`** wires the whole graph and exposes `build_app(settings)`.
- **`tools/`** registers MCP tools on FastMCP. Handlers never call ROS directly.
- **`services/`** validate inputs and orchestrate calls.
- **`adapters/`** are the *only* code that knows how to talk to a specific backend. New backends (e.g. an `rclpy`-based adapter) plug in by implementing the `RosAdapter` protocol.
- **`models/`** holds Pydantic schemas - the contract with MCP clients.
- **`config/`** resolves runtime settings from the environment.

## Runtime modes

| Mode    | When to use                                         | Backend                       |
| ------- | --------------------------------------------------- | ----------------------------- |
| `mock`  | Local development, demos, CI, screencasts           | Deterministic fixtures        |
| `live`  | A machine with ROS2 installed and sourced           | `ros2` CLI wrappers           |
| `auto`  | Detect ROS2; fall back to mock if not present       | Best available (default)      |

Mode is selected via the `TOPICFORGE_MODE` environment variable. `live` and `auto` degrade instead of failing: if `ros2` is not on PATH and no DDS backend comes up, the server serves the mock fixtures, and `health_check` reports `mode: "mock"` next to `requested_mode`. Only `TOPICFORGE_MODE=mock` forces fixtures unconditionally. The DDS backend is selected separately, see [Multi-vendor DDS support](#multi-vendor-dds-support-v030).

## Install from source

Requires Python 3.10+ (ROS 2 Humble on Ubuntu 22.04 ships Python 3.10).

```bash
git clone https://github.com/yaniswav/TopicForge.git
cd TopicForge
python -m venv .venv
source .venv/bin/activate          # Linux / macOS
# .venv\Scripts\Activate.ps1       # Windows PowerShell
pip install -e ".[dev]"
```

Or, if you have `make`:

```bash
make dev
```

## Run

### Mock mode (no ROS2 required)

```bash
TOPICFORGE_MODE=mock python -m topicforge
```

Or:

```bash
make run-mock
```

### Live mode (requires ROS2)

Source your ROS2 distribution first, then:

```bash
source /opt/ros/humble/setup.bash
TOPICFORGE_MODE=live python -m topicforge
```

TopicForge invokes the `ros2` CLI under the hood, so it does **not** require `rclpy` to be importable. This keeps the live adapter portable across ROS2 distros.

### Multi-vendor DDS support (v0.3.0+)

Beyond ROS2 graph introspection, TopicForge observes the raw DDS bus directly via one of two OSS Python adapters (Eclipse CycloneDDS or eProsima Fast DDS) each joining as a **read-only DDS-RTPS participant**. By the OMG-DDS-RTPS protocol guarantee, both adapters see every conformant participant on the domain (RTI Connext, OpenDDS, CoreDX, Dust DDS in Rust, InterCOM, etc.) regardless of host language. The adapters read discovery data; they do not decode the payload of user topics (see the `peek_dds_samples` paragraph below).

**Validation status.** The Cyclone adapter has been run once against a live bus on Windows 11, with Cyclone and Dust DDS participants (see [Multi-vendor demo](#multi-vendor-demo)). The Fast DDS adapter has never been run against a bus, and no RTI, OpenDDS, CoreDX or OpenSplice participant has been observed yet. The unit tests run against the mock backend and against binding-free helpers. The multi-vendor claim therefore rests mostly on the RTPS protocol guarantee, not on a recorded interop run across vendors. For the multi-vendor positioning and its limits, see [`docs/dds-interop-matrix.md`](docs/dds-interop-matrix.md) and the [OMG May 2025 interop reference](docs/projet-file/references/omg-dds-interop-2025-05-08.xlsx).

Useful for non-ROS DDS stacks (defense, aerospace, automotive AUTOSAR Adaptive, industrial integration) and for diagnosing why a ROS2 subscriber isn't receiving when the graph says it should. Same safety-first contract : read-only by **architecture**. The `MiddlewareAdapter` protocol does not expose a write method on any backend.

Install the Cyclone backend from PyPI:

```bash
pip install topicforge[dds-cyclone]   # Eclipse CycloneDDS
pip install topicforge[dds]           # same thing today ([dds] resolves to [dds-cyclone])
```

There is no Fast DDS extra. The `fastdds` Python binding is not published on PyPI, so TopicForge no longer declares it (0.5.3 removed `[dds-fast]`, `[dds-opendds]`, `[dds-dust]` and `[dds-all-oss]`; an unclaimed package name in a published extra is a dependency-confusion risk). The Fast DDS adapter still works if you build eProsima's Python binding from their sources and make it importable in the same environment; see [`docs/DDS_QUICKSTART.md`](docs/DDS_QUICKSTART.md).

Then select a backend (or let auto-detect pick):

```bash
TOPICFORGE_DDS_BACKEND=cyclone python -m topicforge
# or, with a source-built Fast DDS binding:
TOPICFORGE_DDS_BACKEND=fast python -m topicforge
# or let TopicForge probe the importable bindings, in this order:
#   fast > cyclone > mock
TOPICFORGE_DDS_BACKEND=auto python -m topicforge
```

An explicit `TOPICFORGE_DDS_BACKEND` (`cyclone`, `fast` or `auto`) is honoured whether or not `ros2` is on PATH, and in any `TOPICFORGE_MODE` except `mock`: on a host without ROS 2 you get a DDS-only adapter, not fixtures. If the chosen binding is missing or the participant cannot start, the server logs a warning that names the cause and falls back to the ROS2 CLI alone, or to the mock fixtures when that is not available either. The default is `TOPICFORGE_DDS_BACKEND=mock`, which leaves the DDS tools to the mock adapter only.

**What the OSS install covers.** Cyclone and Fast DDS are the two working
backends. `opendds` and `dust` remain selectable identifiers but are permanent
stubs: their adapters always report unavailable, and no install extra exists
for them (`pyopendds` is on PyPI, but the OpenDDS adapter never uses it; there
is no Dust DDS Python binding). That is not a limitation on what you can
observe: because discovery runs over the OMG-standardized wire protocol, a
Cyclone participant already sees RTI Connext, OpenDDS, CoreDX and Fast
endpoints on the same domain. **You do not need a commercial adapter to
observe a commercial bus.**

The values `rti`, `opensplice`, `coredx` and `intercom` were accepted for
`TOPICFORGE_DDS_BACKEND` until 0.5.2 and are now rejected with an explicit
configuration error: the licensed Pro tier is retired and the core no longer
reads `TOPICFORGE_LICENSE_KEY` or loads any `topicforge_pro` package. A native
RTI Connext adapter still exists for the cases where the standard route is not
enough (secure domains needing vendor credentials, shared-memory-only
deployments, vendor-specific extensions); it needs your own RTI binding and
license and is arranged as a support engagement. See
[`docs/pro.md`](docs/pro.md).

Six DDS / observability tools (in addition to the five ROS2 tools above) :

| Tool                    | Since   | Purpose                                                                                                        |
| ----------------------- | ------- | -------------------------------------------------------------------------------------------------------------- |
| `list_participants`     | v0.2.0  | DDS participants discovered on a domain, with vendor, hostname, and (v0.4.0) lifecycle fields                  |
| `detect_qos_mismatches` | v0.2.0  | Reader/writer QoS incompatibilities preventing communication on a topic                                        |
| `peek_dds_samples`      | v0.2.0  | Recent samples on a raw DDS topic: structured on the three builtin DCPS topics; on a user topic it reports presence only, the payload is not decoded (distinct from `sample_messages` on ROS2 graph) |
| `participant_events`    | v0.4.0  | Lifecycle stream: `discovered` / `lost` participant events over a configurable window                         |
| `topic_metrics`         | v0.4.0  | Frequency / sequence-gap / latency schema; data exists only for the builtin discovery topics (see below)      |
| `peek_bag_samples`      | v0.4.0  | Post-mortem inspection: decoded samples from a recorded `.mcap` / `.db3` / `.bag` file (needs `topicforge[bags]`) |

**Composite adapter (v0.4.0 Phase 1+).** When the `ros2` CLI is on PATH and a DDS backend (`cyclone` or `fast`) is selected and starts, TopicForge instantiates **both** a ROS2 CLI adapter and the chosen DDS adapter and routes per tool: the ROS2 graph and bag tools (`list_topics`, `get_topic_info`, `sample_messages`, `analyze_bag`, `peek_bag_samples`) hit the CLI half, the DDS tools (`list_participants`, `detect_qos_mismatches`, `peek_dds_samples`, `participant_events`, `topic_metrics`) hit the DDS backend. ROS2-only or DDS-only setups still work: the missing half is skipped and the present half serves what it can. In a DDS-only process the ROS2 graph and bag tools raise an error that says so. The mock backend exposes all 11 tools against deterministic fixtures for local development.

**`peek_dds_samples` payload shape.** Full-fidelity on the three builtin DCPS topics (`DCPSParticipant`, `DCPSSubscription`, `DCPSPublication`): each sample is a structured discovery payload. **User-topic payloads are not decoded** (since 0.5.3, on both Cyclone and Fast). Asking for a user topic that is announced on the bus returns a single placeholder sample whose payload carries `_decode_status="raw"` and a `_decode_note` saying that decoding is disabled; `_raw_bytes_hex` is empty. That sample means "topic present on the bus", not "message received": no traffic was read, and nothing is fed to `topic_metrics`. A topic nobody announces raises an error. The previous decoding path never worked on either backend (Cyclone called the type lookup with the wrong arity and swallowed the error; Fast returned `None` unconditionally), and re-enabling it needs a real bus to validate against. The `"full"` and `"partial"` statuses stay in the schema, and the mock backend still emits a `"full"` and a `"raw"` example so the wire shape can be tested, but no live adapter produces them today.

**`topic_metrics` scope.** The metrics buffer is filled only when `peek_dds_samples` surfaces samples, and only the builtin discovery topics surface any. On a user topic the tool returns `samples_observed=0`. On a builtin topic, `frequency_hz_observed` reflects how often you call `peek_dds_samples`, not how often anything publishes: it is a statement about the tool's own cadence. Builtin samples carry no application sequence number and no publish timestamp, so `sequence_numbers_available` is `false` and the latency percentiles are `null`. `frequency_hz_declared` is always `null`, nothing populates it. Read this tool as a discovery-layer probe, not as a publish-rate monitor.

**Full 5-minute walkthrough** (backend selection, the canonical QoS-mismatch debugging scenario, troubleshooting) lives in [`docs/DDS_QUICKSTART.md`](docs/DDS_QUICKSTART.md). Migration history : [v0.2 -> v0.3](docs/MIGRATION_v0.2_to_v0.3.md), [v0.3 -> v0.4](docs/MIGRATION_v0.3_to_v0.4.md).

### Multi-vendor demo

`scripts/integration/` holds a demo that puts one read-only TopicForge participant on a DDS domain with programs from several vendors and languages (Cyclone, Dust DDS, Fast DDS, RTI Connext, OpenSplice; fourteen programs in all). The official MCP client asks TopicForge who is on the bus, which reader and writer pairs have incompatible QoS, and who leaves when a participant is stopped. The minimum is a Python / Cyclone and a Rust / Dust participant:

```bash
scripts/integration/launch/setup.sh      # Windows: scripts\integration\launch\setup.ps1
scripts/integration/launch/run_demo.sh   # Windows: scripts\integration\launch\run_demo.ps1
```

Only the Cyclone and Dust participants have been run so far, on Windows. The Fast DDS, RTI and OpenSplice ones are written but unrun, and RTI stays local because of its license terms. Details, the participant table and the known limits are in [`scripts/integration/README.md`](scripts/integration/README.md).

### Configure with Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "topicforge": {
      "command": "python",
      "args": ["-m", "topicforge"],
      "env": { "TOPICFORGE_MODE": "auto" }
    }
  }
}
```

## Test

```bash
pytest
# or
make test
```

Tests run entirely against the mock adapter, the live adapter's pure parsers and the binding-free DDS helpers - they never require a running ROS graph. Tests that need the `cyclonedds` or `fastdds` binding skip themselves when it is absent, and the `integration` marker (real-bus tests) is deselected by default. The multi-vendor demo driver is separate from pytest, see [Multi-vendor demo](#multi-vendor-demo).

## Lint & format

```bash
make lint     # ruff check
make fmt      # ruff format
make check    # both, plus tests (CI bundle)
```

> **Windows note.** The `Makefile` uses POSIX shell syntax (`VAR=value cmd`,
> `find ... -exec`). Run it from Git Bash, WSL, or MSYS2. From a plain
> PowerShell session, invoke the underlying commands directly:
>
> ```powershell
> python -m ruff check src tests
> python -m ruff format src tests
> python -m pytest
> $env:TOPICFORGE_MODE = "mock"; python -m topicforge   # equivalent of `make run-mock`
> ```

## Configuration reference

| Variable                    | Default | Description                                                                   |
| --------------------------- | ------- | ----------------------------------------------------------------------------- |
| `TOPICFORGE_MODE`           | `auto`  | `mock`, `live`, or `auto`                                                     |
| `TOPICFORGE_LOG_LEVEL`      | `INFO`  | `DEBUG`, `INFO`, `WARNING`, `ERROR`                                           |
| `TOPICFORGE_ROS2_BIN`       | `ros2`  | Name (or path) of the ROS2 CLI binary                                         |
| `TOPICFORGE_TELEMETRY`      | `off`   | Opt-in anonymous usage telemetry. An unrecognized value aborts startup. See [Telemetry](#telemetry). |
| `TOPICFORGE_DDS_BACKEND`    | `mock`  | DDS module backend: `mock`, `cyclone`, `fast`, `opendds` (stub), `dust` (stub), or `auto`. `auto` tries `fast` then `cyclone`, whichever binding is importable, else `mock`. An explicit value is honoured without `ros2` on PATH; only `TOPICFORGE_MODE=mock` forces fixtures. `rti`, `opensplice`, `coredx` and `intercom` are rejected with a configuration error (removed in 0.5.3). See [Multi-vendor DDS support](#multi-vendor-dds-support-v030). |
| `TOPICFORGE_DDS_DOMAIN_ID`  | `0`     | DDS domain id observed (0..232) when a DDS backend is active.                 |

See [`.env.example`](.env.example).

## Telemetry

TopicForge ships an **opt-in, anonymous, minimal** telemetry hook. It is **off by default** and the OFF code path performs **zero network calls**: pinned by a unit test (`tests/test_telemetry.py::test_build_app_off_makes_no_transport_calls`).

### How to opt in

```bash
TOPICFORGE_TELEMETRY=on python -m topicforge
# Windows PowerShell: $env:TOPICFORGE_TELEMETRY="on"; python -m topicforge
```

Accepted on-values: `on`, `1`, `true`, `yes`, `enabled` (case-insensitive). Accepted off-values: unset or empty, `off`, `0`, `false`, `no`, `disabled`. **Any other value is a configuration error**, not a silent "off": the server prints `topicforge: configuration error: Invalid TOPICFORGE_TELEMETRY=...` to stderr and exits with code 2, so a typo such as `TOPICFORGE_TELEMETRY=ture` cannot leave you believing telemetry is on, or off, when it is not.

### How to opt out

Unset the variable, set it to `off`, or just don't touch it. Opt-out is the default.

### Exactly what is sent

When telemetry is on, each MCP tool call emits a single event with **only** these six fields:

| Field             | Example          | Notes                                                                  |
| ----------------- | ---------------- | ---------------------------------------------------------------------- |
| `tool_name`       | `"list_topics"`  | One of the eleven MCP tools, never argument values.                   |
| `latency_ms`      | `12.34`          | Wall-clock duration of the handler, rounded to 2 decimals.             |
| `mode`            | `"mock"`         | Mode of the adapter actually serving requests: `mock` or `live`.       |
| `version`         | `"0.5.3"`        | TopicForge server version.                                             |
| `session_id`      | `"a1b2c3..."`      | Random UUID generated per process. Never persisted, never re-used.     |
| `success`         | `true`           | Whether the handler returned (true) or raised (false).                 |

### What is **never** sent

- Topic names, message types, message payloads
- Bag file paths or bag contents
- Hostnames, usernames, IP addresses, ROS distro, environment variables
- Stack traces, error messages, or any free-form text
- Any persistent identifier: `session_id` is regenerated on every server start

The payload shape is fenced by `tests/test_telemetry.py::test_payload_contains_only_whitelisted_keys`. Adding a field there requires a matching change in this section.

### Where the code lives

The complete telemetry implementation is in [`src/topicforge/telemetry/`](src/topicforge/telemetry/). Read it in under five minutes. The default transport is a structured log line (no HTTP endpoint yet); a future S3-backed endpoint will plug into the same `Transport` callable without touching tool handlers.

## Security model

TopicForge is designed for **local trust**: it runs as a subprocess of your MCP client (Claude Desktop, Claude Code) on a machine you control, and inspects your own ROS2 graph or your own bag files. It is not hardened for adversarial inputs.

- `TOPICFORGE_ROS2_BIN` accepts an arbitrary path - if you point it at a malicious binary, TopicForge will execute it. Treat the variable the way you treat `PATH`.
- `analyze_bag` opens whatever path the MCP client passes (no workspace isolation, no symlink restriction). The threat model assumes the client is your trusted agent acting on your behalf.
- All `ros2` CLI invocations use `subprocess.run` with an argument list - never `shell=True`. Topic names are validated against a strict allowlist (`^/[A-Za-z0-9_/]+$`) before being passed to the CLI.
- TopicForge does not load third-party code at startup. Until 0.5.2 the server imported any installed package named `topicforge_pro` and handed it the MCP server instance; that hook never registered a tool, and it was removed in 0.5.3 because it was an opening for a package of that name to add write tools.
- No outbound network calls by default. Since v0.1.1, opt-in anonymous usage telemetry is available behind `TOPICFORGE_TELEMETRY=on`: see [Telemetry](#telemetry) for the exact payload and opt-out instructions. When off (the default), the OFF code path is a verified no-op.

Before exposing TopicForge to *untrusted* MCP clients (hosted endpoints, shared environments), add path isolation and revisit the `TOPICFORGE_ROS2_BIN` policy.

## MVP limitations

- `sample_messages` in live mode uses `ros2 topic echo --csv --once` with a short timeout; topics with no current publisher will return an empty sample. `MessageSample.timestamp_ns` is the message's `header.stamp` (publish time) for `Header`-stamped messages and `0` for headerless types (`std_msgs/String`, `geometry_msgs/Twist`, ...); surfacing the rmw receive timestamp for arbitrary types waits on the future `rclpy`-backed adapter.
- `sample_messages` silently clamps `count` to 50 to keep tool output bounded; requests for more than 50 messages return at most 50 (the `SampleResult.count` field reflects what was actually returned).
- `analyze_bag` in live mode shells out to `ros2 bag info` and parses its text output; it does not use `rosbags`. Deep anomaly detection is mock-only for now. `peek_bag_samples` is the only bag tool that reads the file itself, through `rosbags` (`pip install topicforge[bags]`), and it is served only by the ROS2 CLI adapter (alone or as the ROS half of a composite) or by the mock adapter. In a DDS-only process it raises the "DDS observability only" error.
- `peek_dds_samples` does not decode user-topic payloads, and `topic_metrics` only has data for the builtin discovery topics (details in [Multi-vendor DDS support](#multi-vendor-dds-support-v030)).
- Live-bus validation is thin: only the Cyclone adapter has been run, once, on Windows, against Cyclone and Dust DDS participants. The Fast DDS adapter and every RTI, OpenDDS, CoreDX and OpenSplice participant remain unobserved. DDS Security (authenticated or encrypted domains) is not handled: a participant without credentials sees an empty secure bus.
- `detect_qos_mismatches` covers four QoS policies (Reliability, Durability, History, Deadline). Liveliness, Ownership and Partition are not checked.
- No streaming / push subscriptions in the MVP. Tools are strictly request/response.
- Live adapter is CLI-based, not `rclpy`-based - by design, for portability.

## Roadmap

See [`docs/product-plan.md`](docs/product-plan.md) for the full product trajectory.

Near-term additions on the bench:

- `rclpy`-backed live adapter for faster & richer sampling (per-message rmw receive timestamps, windowed sampling): gated on external user demand
- Extending live-bus validation (Fast DDS, RTI, a Linux and Windows mixed bus) beyond the first Cyclone run, then re-enabling user-topic payload decoding on top of that
- Migration to the MCP SDK 2.x API (the `mcp>=1.0.0,<2` pin is a stopgap)
- Extended QoS coverage (Liveliness, Ownership, Partition, TimeBasedFilter, LatencyBudget)
- URDF inspector / validator and bag anomaly detection (clock jumps, gaps, dropped frames, TF tree health): candidate engagement deliverables, not a shipped product tier (see [`docs/product-plan.md`](docs/product-plan.md))
- Dataset export helpers (rosbag -> COCO / HF Datasets)
- Synthetic data pipeline controller (Blender, Gazebo, Isaac Sim)

The licensed Pro tier is retired. Commercial support and integration work, including the RTI Connext adapter, is handled by direct agreement: see [`docs/pro.md`](docs/pro.md).

## Project layout

```
topicforge-mcp/
├── README.md                  # You are here
├── Makefile                   # Common developer tasks
├── pyproject.toml             # Build & tooling config
├── .env.example               # Example runtime configuration
├── docs/
│   └── product-plan.md        # Product strategy & roadmap
├── src/topicforge/
│   ├── __main__.py            # `python -m topicforge`
│   ├── server/                # MCP bootstrap & lifecycle
│   ├── tools/                 # MCP tool definitions
│   ├── services/              # Domain orchestration
│   ├── adapters/
│   │   ├── ros2_live/         # `ros2` CLI wrappers
│   │   ├── ros2_mock/         # Deterministic fixtures (incl. DDS)
│   │   ├── dds_cyclone/       # Eclipse CycloneDDS adapter (lazy import)
│   │   ├── dds_fast/          # eProsima Fast DDS adapter (lazy import)
│   │   └── common/            # Vendor-neutral helpers + pure QoS analyzer
│   ├── models/                # Pydantic schemas
│   └── config/                # Settings & mode resolution
└── tests/                     # Pytest suite (mock-only, no ROS2/DDS required)
```

## License

MIT - see [LICENSE](LICENSE).
