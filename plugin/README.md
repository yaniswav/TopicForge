# TopicForge plugin for Claude

TopicForge is a read-only MCP server for ROS 2 and DDS. This plugin bundles the server with two short skills so Claude can inspect a robot stack and diagnose a DDS bus. It cannot publish, command a robot, or change QoS: the server has no write path.

## What is included

- MCP server `topicforge`, started with `uvx` from the `topicforge` package on PyPI (version pinned to 0.6.1). It exposes twelve read-only tools: `health_check`, `list_topics`, `get_topic_info`, `sample_messages`, `analyze_bag`, `peek_bag_samples`, `list_participants`, `list_endpoints`, `detect_qos_mismatches`, `participant_events`, `peek_dds_samples`, `topic_metrics`.
- Skill `diagnose-dds-bus`: what to call, and in which order, when nodes do not talk, a topic gets no data, or a node crashed or restarts.
- Skill `inspect-ros2-robot`: listing topics, sampling messages and reading bags, including large arrays and simulation time.

## Requirements

- [`uv`](https://docs.astral.sh/uv/) on PATH, which provides `uvx`. The first start downloads the package and the Cyclone DDS binding, so it can take a little while.
- A local MCP server: it runs on your machine, in Claude Code and in Cowork sessions that run on your computer. It does not run in claude.ai chat.
- ROS 2 on PATH is optional. Without it the ROS 2 tools fall back to fixtures (a fictional demo robot) and `health_check` reports `mode: mock`. The DDS tools work without ROS 2.

## Install

From a marketplace, once the plugin is listed:

```
/plugin install topicforge
```

Meanwhile, from this repository (Claude Code):

```
claude plugin marketplace add yaniswav/TopicForge
claude plugin install topicforge@topicforge
```

Or load the folder for one session: `claude --plugin-dir ./plugin`.

## Configuration

Claude Code asks for two options when the plugin is enabled:

| Option | Default | Meaning |
| --- | --- | --- |
| `dds_backend` | `cyclone` | `cyclone` joins the DDS bus as a read-only participant. `mock` serves fixtures. `auto` picks the best available. |
| `dds_domain_id` | `0` | The one DDS domain observed (0 to 232). Fixed at startup. |

The server runs with `TOPICFORGE_MODE=auto`: live ROS 2 when `ros2` is on PATH, fixtures otherwise. Cowork does not prompt for options and uses the defaults. To use other settings there, edit `.mcp.json`.

Without DDS, set `dds_backend` to `mock`, or use the standalone install `uvx topicforge`.

## Privacy

The server reads the local ROS 2 graph and the DDS discovery traffic on the machine and network it runs on, and returns the result to the Claude session. It makes no outbound network calls. Anonymous telemetry exists but is off by default and is not enabled by this plugin. See the main [README](https://github.com/yaniswav/TopicForge#telemetry) for the telemetry contract and [SECURITY.md](https://github.com/yaniswav/TopicForge/blob/main/SECURITY.md).

## Limits

TopicForge sees what DDS discovery announces. It cannot see whether data flows, a writer that is alive but silent, other DDS domains, or secured (DDS Security) endpoints. The skills tell Claude to say so.

## Links

- Project: https://github.com/yaniswav/TopicForge
- Site: https://topicforge.horizonvista.xyz
- Issues: https://github.com/yaniswav/TopicForge/issues
- License: MIT
