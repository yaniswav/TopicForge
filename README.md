# TopicForge

> Stop asking Claude to invent topic names. TopicForge is the MCP server that grounds AI agents in your real ROS2 stack - or a faithful mock when you have no robot at hand.

TopicForge is a production-minded MCP (Model Context Protocol) server that lets AI agents - such as Claude - inspect ROS2 topics and analyze ROS bag files through a clean, structured tool interface. It is designed for robotics developers, ML/CV engineers working with robot data, and teams that want their AI tooling to *understand* their robotics stack instead of guessing at it.

## Why it exists

LLM agents are good at reasoning over text, but ROS2 introspection lives in a CLI + DDS world they cannot directly reach. Without grounding, an LLM will hallucinate topic names, message types, and bag contents. TopicForge bridges that gap with a small, well-typed set of MCP tools:

| Tool              | Purpose                                                |
| ----------------- | ------------------------------------------------------ |
| `health_check`    | Environment & mode introspection                       |
| `list_topics`     | Discover the ROS graph                                 |
| `get_topic_info`  | Structured info for a single topic                     |
| `sample_messages` | Peek recent messages on a topic (publish-time timestamps for `Header`-stamped types) |
| `analyze_bag`     | Summarize a `.mcap` / `.db3` / `.bag` recording        |

Outputs are structured, JSON-serializable, and stable across runtime modes - they look the same whether the server is talking to a real robot or to its built-in mock fixtures. Every response carries a `mode_effective` field (`"live"` or `"mock"`) so a downstream LLM can tell a real graph from the demo fixtures without re-reading `health_check`.

## 30-second demo without ROS2

The mock adapter ships deterministic fixtures for a small differential robot (LIDAR + RGB camera). You do not need ROS2 installed to try the full tool surface - a clean Python 3.11 venv is enough.

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

Mode is selected via the `TOPICFORGE_MODE` environment variable.

## Install from source

Requires Python 3.11+.

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

Tests run entirely against the mock adapter and the live adapter's pure parsers - they never require a running ROS graph. The full suite completes in well under a second.

## Lint & format

```bash
make lint     # ruff check
make fmt      # ruff format
make check    # both, plus tests (CI bundle)
```

> **Windows note.** The `Makefile` uses POSIX shell syntax (`VAR=value cmd`,
> `find … -exec`). Run it from Git Bash, WSL, or MSYS2. From a plain
> PowerShell session, invoke the underlying commands directly:
>
> ```powershell
> python -m ruff check src tests
> python -m ruff format src tests
> python -m pytest
> $env:TOPICFORGE_MODE = "mock"; python -m topicforge   # equivalent of `make run-mock`
> ```

## Configuration reference

| Variable                 | Default | Description                                                       |
| ------------------------ | ------- | ----------------------------------------------------------------- |
| `TOPICFORGE_MODE`        | `auto`  | `mock`, `live`, or `auto`                                         |
| `TOPICFORGE_LOG_LEVEL`   | `INFO`  | `DEBUG`, `INFO`, `WARNING`, `ERROR`                               |
| `TOPICFORGE_ROS2_BIN`    | `ros2`  | Name (or path) of the ROS2 CLI binary                             |
| `TOPICFORGE_TELEMETRY`   | `off`   | Opt-in anonymous usage telemetry. See [Telemetry](#telemetry).    |

See [`.env.example`](.env.example).

## Telemetry

TopicForge ships an **opt-in, anonymous, minimal** telemetry hook. It is **off by default** and the OFF code path performs **zero network calls** — pinned by a unit test (`tests/test_telemetry.py::test_build_app_off_makes_no_transport_calls`).

### How to opt in

```bash
TOPICFORGE_TELEMETRY=on python -m topicforge
# Windows PowerShell: $env:TOPICFORGE_TELEMETRY="on"; python -m topicforge
```

Accepted on-values: `on`, `1`, `true`, `yes`, `enabled` (case-insensitive). Anything else — including unset — keeps telemetry off.

### How to opt out

Unset the variable, set it to `off`, or just don't touch it. Opt-out is the default.

### Exactly what is sent

When telemetry is on, each MCP tool call emits a single event with **only** these six fields:

| Field             | Example          | Notes                                                                  |
| ----------------- | ---------------- | ---------------------------------------------------------------------- |
| `tool_name`       | `"list_topics"`  | One of the five MVP tools — never argument values.                     |
| `latency_ms`      | `12.34`          | Wall-clock duration of the handler, rounded to 2 decimals.             |
| `mode`            | `"mock"`         | Effective runtime mode: `mock` or `live`.                              |
| `version`         | `"0.1.2"`        | TopicForge server version.                                             |
| `session_id`      | `"a1b2c3…"`      | Random UUID generated per process. Never persisted, never re-used.     |
| `success`         | `true`           | Whether the handler returned (true) or raised (false).                 |

### What is **never** sent

- Topic names, message types, message payloads
- Bag file paths or bag contents
- Hostnames, usernames, IP addresses, ROS distro, environment variables
- Stack traces, error messages, or any free-form text
- Any persistent identifier — `session_id` is regenerated on every server start

The payload shape is fenced by `tests/test_telemetry.py::test_payload_contains_only_whitelisted_keys`. Adding a field there requires a matching change in this section.

### Where the code lives

The complete telemetry implementation is in [`src/topicforge/telemetry/`](src/topicforge/telemetry/) — read it in under five minutes. The default transport is a structured log line (no HTTP endpoint yet); a future S3-backed endpoint will plug into the same `Transport` callable without touching tool handlers.

## Security model

TopicForge is designed for **local trust**: it runs as a subprocess of your MCP client (Claude Desktop, Claude Code) on a machine you control, and inspects your own ROS2 graph or your own bag files. It is not hardened for adversarial inputs.

- `TOPICFORGE_ROS2_BIN` accepts an arbitrary path - if you point it at a malicious binary, TopicForge will execute it. Treat the variable the way you treat `PATH`.
- `analyze_bag` opens whatever path the MCP client passes (no workspace isolation, no symlink restriction). The threat model assumes the client is your trusted agent acting on your behalf.
- All `ros2` CLI invocations use `subprocess.run` with an argument list - never `shell=True`. Topic names are validated against a strict allowlist (`^/[A-Za-z0-9_/]+$`) before being passed to the CLI.
- No outbound network calls by default. Since v0.1.1, opt-in anonymous usage telemetry is available behind `TOPICFORGE_TELEMETRY=on` — see [Telemetry](#telemetry) for the exact payload and opt-out instructions. When off (the default), the OFF code path is a verified no-op.

Before exposing TopicForge to *untrusted* MCP clients (hosted endpoints, shared environments), add path isolation and revisit the `TOPICFORGE_ROS2_BIN` policy.

## MVP limitations

- `sample_messages` in live mode uses `ros2 topic echo --csv --once` with a short timeout; topics with no current publisher will return an empty sample. `MessageSample.timestamp_ns` is the message's `header.stamp` (publish time) for `Header`-stamped messages and `0` for headerless types (`std_msgs/String`, `geometry_msgs/Twist`, …); surfacing the rmw receive timestamp for arbitrary types waits on the future `rclpy`-backed adapter.
- `sample_messages` silently clamps `count` to 50 to keep tool output bounded; requests for more than 50 messages return at most 50 (the `SampleResult.count` field reflects what was actually returned).
- `analyze_bag` in live mode shells out to `ros2 bag info` and parses its text output. Deep anomaly detection is mock-only for now.
- No streaming / push subscriptions in the MVP. Tools are strictly request/response.
- Live adapter is CLI-based, not `rclpy`-based - by design, for portability.

## Roadmap

See [`docs/product-plan.md`](docs/product-plan.md) for the full product trajectory.

Near-term additions on the bench:

- `rclpy`-backed live adapter for faster & richer sampling
- URDF inspector / validator MCP tools
- Bag anomaly detection (clock jumps, gaps, dropped frames, TF tree health)
- Dataset export helpers (rosbag → COCO / HF Datasets)
- Synthetic data pipeline controller (Blender, Gazebo, Isaac Sim)
- Hosted MCP endpoint with auth

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
│   │   └── ros2_mock/         # Deterministic fixtures
│   ├── models/                # Pydantic schemas
│   └── config/                # Settings & mode resolution
└── tests/                     # Pytest suite (mock-only, no ROS2 required)
```

## License

MIT - see [LICENSE](LICENSE).
