# TopicForge

<!-- mcp-name: io.github.yaniswav/topicforge -->

[![PyPI version](https://img.shields.io/pypi/v/topicforge.svg)](https://pypi.org/project/topicforge/)
[![CI](https://github.com/yaniswav/TopicForge/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/yaniswav/TopicForge/actions/workflows/ci.yml)
[![Python versions](https://img.shields.io/pypi/pyversions/topicforge.svg)](https://pypi.org/project/topicforge/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/yaniswav/TopicForge/blob/main/LICENSE)
[![Read-only](https://img.shields.io/badge/safety-read--only-2563eb)](https://github.com/yaniswav/TopicForge#security-model)

A read-only MCP (Model Context Protocol) server that lets an AI agent inspect a ROS2 graph, recorded bag files and the DDS layer underneath ROS. The code has no write path: it cannot publish to the bus or command a robot, and there is no permission system to configure.

It gives the agent twelve typed tools that return frozen Pydantic schemas, identical whether the server talks to a real robot or to its built-in mock fixtures. Ask why `nav_planner` gets no scan, and the agent reads the bus, finds the BEST_EFFORT writer facing a RELIABLE reader and names the incompatible policy (see [`examples/02-debug-qos-mismatch.md`](examples/02-debug-qos-mismatch.md)). It is meant for ROS2 developers, robotics ML/CV engineers and teams that cannot accept a write path into a production stack.

For DDS, TopicForge joins a domain as a read-only participant through one open-source binding (Eclipse CycloneDDS from PyPI) and reads the builtin discovery topics that the OMG DDS-RTPS protocol standardizes. So far the author has observed Cyclone DDS and Dust DDS participants on a live bus. RTI Connext, OpenDDS, CoreDX and Fast DDS announce themselves through the same standard discovery, but none of them has been observed yet. This covers discovery only: participants, readers, writers and their QoS. See [`docs/dds-interop-matrix.md`](docs/dds-interop-matrix.md).

## Quickstart

No ROS2 is needed; the mock adapter serves deterministic fixtures for a small differential robot (LIDAR + RGB camera). Python 3.10 to 3.13.

```bash
pip install topicforge
TOPICFORGE_MODE=mock python -m topicforge
# Windows PowerShell: $env:TOPICFORGE_MODE="mock"; python -m topicforge
```

The server speaks MCP over stdio and waits for a client, so wire it into one. For Claude Desktop, add to `claude_desktop_config.json`:

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

Then ask it to list the topics or to analyze `/tmp/demo.mcap`. For Claude Code: `claude mcp add topicforge -- topicforge`. Setup for a real ROS2 environment (WSL2, Linux, Docker, native Windows) is in [`docs/TESTING.md`](docs/TESTING.md); recurring monitoring prompts and the privacy contract are in [`docs/TUTORIEL.md`](docs/TUTORIEL.md).

## Tools

Every response except `health_check` carries `mode_effective` (`"live"` or `"mock"`), so a caller can tell a real graph from fixtures.

| Tool                    | Purpose                                                                                          |
| ----------------------- | ------------------------------------------------------------------------------------------------ |
| `health_check`          | Environment and mode introspection. Always succeeds; reports `mode` next to `requested_mode`      |
| `list_topics`           | Discover the ROS2 graph                                                                          |
| `get_topic_info`        | Message type, publisher/subscriber counts and QoS for one topic                                  |
| `sample_messages`       | Peek recent messages on a ROS2 topic (count capped at 50)                                        |
| `analyze_bag`           | Summarize a `.mcap` / `.db3` recording or `rosbag2_*` directory (via `ros2 bag info`)            |
| `list_participants`     | DDS participants on the domain: vendor, `name` (EntityName QoS, Cyclone) and `hostname`          |
| `detect_qos_mismatches` | Incompatible QoS pairs between DDS readers and writers                                           |
| `peek_dds_samples`      | Raw DDS samples; structured on the three builtin discovery topics, presence-only on user topics   |
| `participant_events`    | Timeline of participant `discovered` / `lost` events                                             |
| `topic_metrics`         | Frequency, sequence-gap and latency schema; data only for builtin discovery topics               |
| `peek_bag_samples`      | Decoded samples from a recorded bag, including ROS 1 `.bag` (needs `pip install topicforge[bags]`) |
| `list_endpoints`        | DDS writers and readers with structured QoS, per-topic roll-up that flags orphans (writer with no reader, reader with no writer) |

Walkthroughs against the mock, each with the exact tool calls and payloads, are in [`examples/`](examples/README.md). To run the DDS tools against a real bus with several programs and vendors, see [`examples/dds/README.md`](examples/dds/README.md) (`python examples/dds/run_all.py`).

## Modes

| Mode   | When to use                                      | Backend                    |
| ------ | ------------------------------------------------ | -------------------------- |
| `mock` | Development, demos, CI, screencasts              | Deterministic fixtures     |
| `live` | ROS2 sourced and on PATH, and/or a DDS backend   | `ros2` CLI, DDS participant |
| `auto` | Detect what is available, else mock (default)    | Best available             |

`live` and `auto` degrade instead of failing: if neither `ros2` nor a DDS backend comes up, the server serves the mock fixtures and `health_check` reports `mode: "mock"` next to `requested_mode`. Only `TOPICFORGE_MODE=mock` forces fixtures unconditionally. The live ROS2 adapter shells out to the `ros2` CLI, so `rclpy` does not need to be importable.

## DDS backends

```bash
pip install topicforge[dds]                      # Eclipse CycloneDDS ([dds-cyclone] is the same thing)
TOPICFORGE_DDS_BACKEND=cyclone python -m topicforge
```

`TOPICFORGE_DDS_BACKEND` accepts `mock` (default), `cyclone`, `fast` and `auto` (`fast`, then `cyclone`, then `mock`, whichever binding imports). The default `mock` selects no DDS backend: installing the Cyclone binding is not enough, you must also set `TOPICFORGE_DDS_BACKEND=cyclone`. With `TOPICFORGE_MODE=live` and no backend selected, the DDS tools raise `DDS module is not active: ...` with the actual cause (backend not selected, binding not installed, or binding installed but the adapter failed to start), and `health_check` reports `dds_backend: "none"` plus `dds_inactive_reason`. An explicit value is honoured with or without `ros2` on PATH, in any mode except `mock`. If the binding is missing or the participant cannot start, the server logs a warning naming the cause and falls back to the ROS2 CLI alone, or to the mock fixtures. When both `ros2` and a DDS backend are up, a composite adapter routes the five ROS2 graph and bag tools to the CLI and the seven DDS tools to the DDS backend.

A Fast DDS adapter exists but has never run against a bus, and its `fastdds` Python binding is not on PyPI: build it from eProsima's sources and install it next to TopicForge. There is no `[dds-fast]` extra. `opendds` and `dust` are permanent stubs that never serve. `rti`, `opensplice`, `coredx` and `intercom` are rejected with a configuration error,; Cyclone already sees those vendors' participants through standard discovery. Full backend selection, the routing table and the QoS mismatch scenario are in [`docs/DDS_QUICKSTART.md`](docs/DDS_QUICKSTART.md); error messages are in [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md).

## Configuration reference

| Variable                   | Default | Description                                                                                       |
| -------------------------- | ------- | ------------------------------------------------------------------------------------------------- |
| `TOPICFORGE_MODE`          | `auto`  | `mock`, `live` or `auto`                                                                          |
| `TOPICFORGE_LOG_LEVEL`     | `INFO`  | `DEBUG`, `INFO`, `WARNING`, `ERROR`                                                               |
| `TOPICFORGE_ROS2_BIN`      | `ros2`  | Name or path of the ROS2 CLI binary                                                               |
| `TOPICFORGE_TELEMETRY`     | `off`   | Opt-in anonymous telemetry; an unrecognized value aborts startup. See [Telemetry](#telemetry)     |
| `TOPICFORGE_DDS_BACKEND`   | `mock`  | `mock` (no DDS backend), `cyclone`, `fast`, `auto` (`opendds` and `dust` are stubs)                |
| `TOPICFORGE_DDS_DOMAIN_ID` | `0`     | DDS domain observed (0..232). Joined at startup; changing it needs a restart                      |
| `TOPICFORGE_MAX_SAMPLE_BYTES` | `1048576` | Size cap for one sampled message (1 KiB..64 MiB); a call returns at most 4 times that. Over-cap messages are dropped with a note |

Samples with comments are in [`.env.example`](.env.example). Any invalid value stops the server with `topicforge: configuration error: ...` and exit code 2, so a typo cannot silently change behaviour.

## Limitations

External validation against a simulated robot's ground truth: [docs/VALIDATION.md](docs/VALIDATION.md).

- DDS validation is partial. The Cyclone adapter has run against a real bus, with Cyclone and Dust DDS participants, on Windows and in CI on Ubuntu and Windows (`.github/workflows/demo.yml`). The Fast DDS adapter has never run against a bus, and no RTI, OpenDDS, CoreDX or OpenSplice participant has been observed by this project. The multi-vendor claim rests on the RTPS protocol guarantee, not on a recorded cross-vendor run.
- User-topic payloads are not decoded. `peek_dds_samples` on a user topic returns count 0 and a note that the topic is announced on the bus; no traffic is read. `topic_metrics` therefore has data only for the builtin discovery topics and says so in its `status`. It is a discovery-layer probe, not a publish-rate monitor.
- Liveliness at runtime is not observed. A writer that is alive but silent (a hung process whose lease is still renewed) looks healthy, because TopicForge reads discovery, not data. An opt-in data probe is planned. A crash and a clean leave cannot be told apart, and `lost_ns` is an upper bound of the death.
- Cyclone vendor ids: participants that do not follow the RTPS vendor-id convention in their GUID prefix (Dust DDS, and RTI by default) are reported with vendor `unknown`.
- Single domain: the server observes the domain it joined at startup; changing it needs a restart.
- DDS Security is not handled. A participant without credentials sees an empty secure bus. `detect_qos_mismatches` checks Partition, type name, Reliability, Durability, Deadline, Liveliness, LatencyBudget, Ownership (kind), DestinationOrder and DataRepresentation (History as a risk); Presentation, XTypes assignability and runtime behavior are not checked, and the result lists them in `policies_unchecked`. It returns a `MismatchScan` envelope: read `reports` for the mismatches.
- Fast DDS serves no `list_endpoints`.
- `sample_messages` (live) streams `ros2 topic echo` until `count` messages arrive or `timeout_s` (1..45, default 10, for the whole call) runs out, and returns what arrived with a `note` (`N of M messages within T s`, saying whether a publisher exists). QoS is matched to the publishers, so latched topics work. The payload has nested named fields (`payload.header.stamp.sec`); `timestamp_ns` is `header.stamp` (the publisher's clock, sim time on a simulation) or 0 for headerless types, with `stamp_source` and `received_ns` (wall clock when the CLI printed it). Arrays are cut at 128 elements by default; `max_array_length` (1..65536, or null for no cut) and `arrays_summary_only` change that, and a cut is listed under `_truncated_fields`. `nan` and `inf` come back as strings. `count` above 50 is capped with a note.
- `analyze_bag` (live) parses `ros2 bag info` text for the totals and counts; anomaly detection is mock-only. Per-topic times, rates (`(n - 1) / span`) and `latched` are added when the bag can be read locally (`.db3` with the standard library, `.mcap` with `rosbags`), else rates fall back to count / bag duration (`frequency_basis`). `peek_bag_samples` reads the file itself, through `rosbags`, returns the first `count` messages in recording order (not the last), and is served only by the ROS2 CLI adapter or the mock; its `timestamp_ns` is the message's `header.stamp` (`stamp_source` `header`) or, without a top-level header, the bag record time (`stamp_source` `recorded`), and `recorded_ns` is always the bag record time; bags that embed no message definitions (Humble `.db3`) are decoded with the Humble definitions, or the distro the bag records, and `note` says so. Without `ros2`, bag tools return fixtures: check `health_check` for `mode: "mock"` before trusting bag output. `health_check` also reports `sim_clock_published` (live only): true when `/clock` has a publisher, a hint that header stamps may be simulation time.
- Synchronous handlers: the tools run on the MCP event loop; on Windows a hung `ros2` launcher can block the server.
- No streaming or push subscriptions: tools are strictly request/response.

Next: an opt-in probe to tell a hung writer from a healthy one, wider real-bus validation (Fast DDS, RTI, OpenDDS), and DDS Security. Open work is tracked in [issues](https://github.com/yaniswav/TopicForge/issues).

## Telemetry

Opt-in, anonymous and off by default. When off, instrumentation returns the handler unchanged: no event is built, no transport is constructed, no network code runs (pinned by `tests/test_telemetry.py::test_build_app_off_makes_no_transport_calls`).

```bash
TOPICFORGE_TELEMETRY=on python -m topicforge
```

On-values: `on`, `1`, `true`, `yes`, `enabled`. Off-values: unset, `off`, `0`, `false`, `no`, `disabled`. Anything else is a configuration error rather than a silent "off".

When on, each tool call emits one event with exactly six fields:

| Field        | Example         | Notes                                                       |
| ------------ | --------------- | ----------------------------------------------------------- |
| `tool_name`  | `"list_topics"` | One of the twelve tools, never argument values              |
| `latency_ms` | `12.34`         | Handler wall-clock duration, 2 decimals                     |
| `mode`       | `"mock"`        | Mode of the adapter actually serving: `mock` or `live`      |
| `version`    | `"0.6.0"`       | TopicForge server version                                   |
| `session_id` | `"a1b2c3..."`   | Random UUID per process, never persisted                    |
| `success`    | `true`          | Whether the handler returned or raised                      |

Never sent: topic names, message types or payloads, bag paths or contents, hostnames, usernames, IP addresses, environment variables, error messages. The field set is fenced by `tests/test_telemetry.py::test_payload_contains_only_whitelisted_keys`; adding a field requires updating this section. The default transport is a structured log line; there is no HTTP endpoint yet. The implementation is in [`src/topicforge/telemetry/`](src/topicforge/telemetry/).

## Security model

TopicForge is designed for local trust: it runs as a subprocess of your MCP client on a machine you control and inspects your own ROS2 graph, DDS domain and bag files. It is not hardened for adversarial inputs.

- `TOPICFORGE_ROS2_BIN` accepts an arbitrary path; treat it the way you treat `PATH`.
- `analyze_bag` and `peek_bag_samples` open whatever path the client passes (no workspace isolation, no symlink restriction).
- All `ros2` invocations use `subprocess.run` with an argument list, never `shell=True`. ROS2 topic names are validated against `^/[A-Za-z0-9_/]+$` first.
- The server loads no third-party code at startup.
- No outbound network calls unless telemetry is turned on.

Before exposing TopicForge to untrusted MCP clients (hosted endpoints, shared environments), add path isolation and revisit the `TOPICFORGE_ROS2_BIN` policy. Vulnerability reports: see [`SECURITY.md`](SECURITY.md).

## Development

```bash
git clone https://github.com/yaniswav/TopicForge.git && cd TopicForge
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m ruff check src tests
python -m pytest
```

Tests run against the mock adapter, the live adapter's pure parsers and the binding-free DDS helpers; they never need a running ROS graph. Tests needing the `cyclonedds` or `fastdds` binding skip themselves when it is absent, and the `integration` marker (real-bus tests) is deselected by default. The `Makefile` (`make check`) uses POSIX shell syntax; on plain PowerShell run the commands above. See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Upgrading

TopicForge is pre-1.0; [`CHANGELOG.md`](CHANGELOG.md) lists every change, including the yanked releases and removed extras.

## Layout

```
src/topicforge/
  server/      MCP bootstrap, build_app(settings)
  tools/       thin FastMCP handlers, no backend logic
  services/    input validation, orchestration, adapter factory
  adapters/    ros2_live, ros2_mock, dds_cyclone, dds_fast, common/ (binding-free logic)
  models/      frozen Pydantic schemas, the contract with MCP clients
  config/      settings and mode resolution
  telemetry/   opt-in, off by default
examples/      mock walkthroughs (*.md) and runnable live DDS examples (dds/)
scripts/       real-bus interop checks
docs/          guides, product plan
tests/         pytest suite, mock-only, no ROS2 or DDS required
```

Layers are strictly separated: handlers never call `subprocess`, adapters are the only code that talks to a backend, and new backends implement the `MiddlewareAdapter` protocol in `adapters/base.py`.

## License

MIT, see [LICENSE](LICENSE). Integration or support work for a specific ROS 2 / DDS setup: ethvignot.yanis@gmail.com.
