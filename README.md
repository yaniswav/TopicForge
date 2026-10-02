# TopicForge

<!-- mcp-name: io.github.yaniswav/topicforge -->

[![PyPI version](https://img.shields.io/pypi/v/topicforge.svg)](https://pypi.org/project/topicforge/)
[![CI](https://github.com/yaniswav/TopicForge/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/yaniswav/TopicForge/actions/workflows/ci.yml)
[![Python versions](https://img.shields.io/pypi/pyversions/topicforge.svg)](https://pypi.org/project/topicforge/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/yaniswav/TopicForge/blob/main/LICENSE)
[![Read-only by architecture](https://img.shields.io/badge/safety-read--only_by_architecture-2563eb)](https://github.com/yaniswav/TopicForge#security-model)

A read-only MCP (Model Context Protocol) server that lets an AI agent inspect a ROS2 graph, recorded bag files and the DDS layer underneath ROS, without being able to publish to the bus or command a robot. It is read-only by **architecture**, not by configuration: there is no write path to misconfigure and no permission system to audit.

Without grounding, an LLM asked about a robot will invent topic names, message types and bag contents. TopicForge gives it **eleven typed tools** that return frozen Pydantic schemas, identical whether the server talks to a real robot or to its built-in mock fixtures. It is aimed at ROS2 developers, robotics ML/CV engineers and teams that cannot accept a write path into a production stack.

For DDS, TopicForge joins a domain as a read-only participant through one open-source binding (Eclipse CycloneDDS from PyPI) and reads the builtin discovery topics that the OMG DDS-RTPS protocol standardizes. Every conformant vendor announces itself there, so a Cyclone participant also sees RTI Connext, OpenDDS, CoreDX and Dust DDS endpoints without any proprietary binding. This covers discovery only: participants, readers, writers and their QoS. See [`docs/dds-interop-matrix.md`](docs/dds-interop-matrix.md).

## Quickstart

No ROS2 needed; the mock adapter serves deterministic fixtures for a small differential robot (LIDAR + RGB camera). Python 3.10 to 3.13.

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

All eleven tools are read-only. Every response except `health_check` carries `mode_effective` (`"live"` or `"mock"`), so a caller can tell a real graph from fixtures.

| Tool                    | Purpose                                                                                          |
| ----------------------- | ------------------------------------------------------------------------------------------------ |
| `health_check`          | Environment and mode introspection. Always succeeds; reports `mode` next to `requested_mode`      |
| `list_topics`           | Discover the ROS2 graph                                                                          |
| `get_topic_info`        | Message type, publisher/subscriber counts and QoS for one topic                                  |
| `sample_messages`       | Peek recent messages on a ROS2 topic (count clamped to 50)                                       |
| `analyze_bag`           | Summarize a `.mcap` / `.db3` / `.bag` recording                                                  |
| `list_participants`     | DDS participants on the domain: vendor, `name` (EntityName QoS, Cyclone) and `hostname`          |
| `detect_qos_mismatches` | Incompatible QoS pairs between DDS readers and writers                                           |
| `peek_dds_samples`      | Raw DDS samples; structured on the three builtin discovery topics, presence-only on user topics   |
| `participant_events`    | Timeline of participant `discovered` / `lost` events                                             |
| `topic_metrics`         | Frequency, sequence-gap and latency schema; data only for builtin discovery topics               |
| `peek_bag_samples`      | Decoded samples from a recorded bag (needs `pip install topicforge[bags]`)                       |

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

`TOPICFORGE_DDS_BACKEND` accepts `mock` (default), `cyclone`, `fast` and `auto` (`fast`, then `cyclone`, then `mock`, whichever binding imports). An explicit value is honoured with or without `ros2` on PATH, in any mode except `mock`. If the binding is missing or the participant cannot start, the server logs a warning naming the cause and falls back to the ROS2 CLI alone, or to the mock fixtures. When both `ros2` and a DDS backend are up, a composite adapter routes the five ROS2 graph and bag tools to the CLI and the five DDS tools to the DDS backend.

A Fast DDS adapter exists but has never run against a bus, and its `fastdds` Python binding is not on PyPI: build it from eProsima's sources and install it next to TopicForge. There is no `[dds-fast]` extra. `opendds` and `dust` are permanent stubs that never serve. `rti`, `opensplice`, `coredx` and `intercom` are rejected with a configuration error, since the Pro tier is retired (see [`docs/pro.md`](docs/pro.md)). Full backend selection, the routing table and the QoS mismatch scenario are in [`docs/DDS_QUICKSTART.md`](docs/DDS_QUICKSTART.md); error messages are in [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md).

## Configuration reference

| Variable                   | Default | Description                                                                                       |
| -------------------------- | ------- | ------------------------------------------------------------------------------------------------- |
| `TOPICFORGE_MODE`          | `auto`  | `mock`, `live` or `auto`                                                                          |
| `TOPICFORGE_LOG_LEVEL`     | `INFO`  | `DEBUG`, `INFO`, `WARNING`, `ERROR`                                                               |
| `TOPICFORGE_ROS2_BIN`      | `ros2`  | Name or path of the ROS2 CLI binary                                                               |
| `TOPICFORGE_TELEMETRY`     | `off`   | Opt-in anonymous telemetry; an unrecognized value aborts startup. See [Telemetry](#telemetry)     |
| `TOPICFORGE_DDS_BACKEND`   | `mock`  | `mock`, `cyclone`, `fast`, `auto` (`opendds` and `dust` are stubs)                                 |
| `TOPICFORGE_DDS_DOMAIN_ID` | `0`     | DDS domain observed (0..232). Joined at startup; changing it needs a restart                      |

Samples with comments are in [`.env.example`](.env.example). Any invalid value stops the server with `topicforge: configuration error: ...` and exit code 2, so a typo cannot silently change behaviour.

## Limitations

- **DDS validation is partial.** The Cyclone adapter has run against a real bus, with Cyclone and Dust DDS participants, on Windows and in CI on Ubuntu and Windows (`.github/workflows/demo.yml`). The Fast DDS adapter has never run against a bus, and no RTI, OpenDDS, CoreDX or OpenSplice participant has been observed by this project. The multi-vendor claim rests on the RTPS protocol guarantee, not on a recorded cross-vendor run.
- **User-topic payloads are not decoded.** `peek_dds_samples` on a user topic reports that the topic is announced on the bus and returns one placeholder sample (`_decode_status="raw"`, empty `_raw_bytes_hex`); no traffic is read. Consequently `topic_metrics` only has data for the builtin discovery topics, its observed frequency is the cadence of your own `peek_dds_samples` calls, and latency and sequence gaps are `null`. It is a discovery-layer probe, not a publish-rate monitor.
- **Cyclone vendor ids.** Participants that do not follow the RTPS vendor-id convention in their GUID prefix (Dust DDS, and RTI by default) are reported with vendor `unknown`.
- **DDS Security is not handled.** A participant without credentials sees an empty secure bus. `detect_qos_mismatches` covers Reliability, Durability, History and Deadline; Liveliness, Ownership and Partition are not checked.
- **`sample_messages` (live)** runs `ros2 topic echo --csv --once` with a short timeout; a topic with no current publisher returns an empty sample. `timestamp_ns` is the message `header.stamp` for `Header`-stamped types and `0` for headerless ones.
- **`analyze_bag` (live)** parses `ros2 bag info` text and does not use `rosbags`; anomaly detection is mock-only. `peek_bag_samples` is the only tool that reads the file itself, through `rosbags`, and is served only by the ROS2 CLI adapter or the mock. Without `ros2`, bag tools return fixtures: check `health_check` for `mode: "mock"` before trusting bag output.
- **Synchronous handlers.** The tools run on the MCP event loop; on Windows a hung `ros2` launcher can block the server.
- No streaming or push subscriptions: tools are strictly request/response.

The roadmap and the open work behind these limits are in [`docs/product-plan.md`](docs/product-plan.md).

## Telemetry

Opt-in, anonymous and **off by default**. When off, instrumentation returns the handler unchanged: no event is built, no transport is constructed, no network code runs (pinned by `tests/test_telemetry.py::test_build_app_off_makes_no_transport_calls`).

```bash
TOPICFORGE_TELEMETRY=on python -m topicforge
```

On-values: `on`, `1`, `true`, `yes`, `enabled`. Off-values: unset, `off`, `0`, `false`, `no`, `disabled`. Anything else is a configuration error, not a silent "off".

When on, each tool call emits one event with exactly six fields:

| Field        | Example         | Notes                                                       |
| ------------ | --------------- | ----------------------------------------------------------- |
| `tool_name`  | `"list_topics"` | One of the eleven tools, never argument values              |
| `latency_ms` | `12.34`         | Handler wall-clock duration, 2 decimals                     |
| `mode`       | `"mock"`        | Mode of the adapter actually serving: `mock` or `live`      |
| `version`    | `"0.5.3"`       | TopicForge server version                                   |
| `session_id` | `"a1b2c3..."`   | Random UUID per process, never persisted                    |
| `success`    | `true`          | Whether the handler returned or raised                      |

Never sent: topic names, message types or payloads, bag paths or contents, hostnames, usernames, IP addresses, environment variables, error messages. The field set is fenced by `tests/test_telemetry.py::test_payload_contains_only_whitelisted_keys`; adding a field requires updating this section. The default transport is a structured log line, there is no HTTP endpoint yet. The implementation is in [`src/topicforge/telemetry/`](src/topicforge/telemetry/).

## Security model

TopicForge is designed for **local trust**: it runs as a subprocess of your MCP client on a machine you control and inspects your own ROS2 graph, DDS domain and bag files. It is not hardened for adversarial inputs.

- `TOPICFORGE_ROS2_BIN` accepts an arbitrary path; treat it the way you treat `PATH`.
- `analyze_bag` and `peek_bag_samples` open whatever path the client passes (no workspace isolation, no symlink restriction).
- All `ros2` invocations use `subprocess.run` with an argument list, never `shell=True`. ROS2 topic names are validated against `^/[A-Za-z0-9_/]+$` first.
- The server loads no third-party code at startup. Until 0.5.2 it imported any installed `topicforge_pro` package; that hook was removed in 0.5.3 because it was an opening for a package of that name to add write tools.
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

TopicForge is pre-1.0 and the 0.x releases changed things freely; [`CHANGELOG.md`](CHANGELOG.md) is the record. Two points matter if you are coming from an old install. Releases 0.3.0 to 0.5.2 are yanked, so `pip install -U topicforge` resolves to 0.5.3 or later. And since 0.5.3 the `[dds-fast]`, `[dds-opendds]`, `[dds-dust]` and `[dds-all-oss]` extras no longer exist, the DDS backend values `rti`, `opensplice`, `coredx` and `intercom` are rejected, and `[dds]` and `[all]` resolve to Cyclone only. Schema changes across 0.x were additive optional fields; a client that pins a JSON Schema with `additionalProperties: false` needs to regenerate it.

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

MIT, see [LICENSE](LICENSE). Commercial support and integration work: [`docs/pro.md`](docs/pro.md).
