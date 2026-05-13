# TopicForge — MCPize Listing

## One-liner (3 variants)

1. Stop asking Claude to invent topic names. TopicForge grounds AI agents in your real ROS2 stack — or a faithful mock when no robot is around.
2. The MCP server that turns Claude into a ROS2 power-user: typed topic introspection and bag analysis, zero hallucination.
3. Inspect ROS2 topics and analyze bag files from any MCP client. Five read-only tools, structured outputs, no robot required.

## Problem

LLM agents reason well over text, but ROS2 introspection lives in a CLI and DDS world they cannot reach. Without grounding, they hallucinate topic names, message types, and bag contents — confidently and often. Robotics developers end up copy-pasting `ros2 topic` output into prompts just to keep their AI assistant useful.

## Solution

TopicForge is a Python MCP server that exposes the ROS2 graph and bag files through a small, well-typed tool surface. Every output is a JSON-serializable Pydantic payload, stable across runtime modes, with strict schemas the LLM can rely on. A built-in mock adapter ships deterministic fixtures so the full tool surface works without a robot, ROS2 install, or DDS daemon.

## Quickstart

```bash
pip install topicforge
export TOPICFORGE_MODE=mock          # or "live" with ROS2 sourced, or "auto"
python -m topicforge
```

## Tools exposed

| Tool              | Description                                                  |
| ----------------- | ------------------------------------------------------------ |
| `health_check`    | Environment and mode introspection — always succeeds         |
| `list_topics`     | Discover the active ROS graph                                |
| `get_topic_info`  | Structured info for a single topic                           |
| `sample_messages` | Peek recent messages on a topic (clamped to 50)              |
| `analyze_bag`     | Summarize a `.mcap` / `.db3` / `.bag` recording              |

## Demo

<!-- Loom embed: https://loom.com/share/TBD -->

## Free tier (v0.1.0)

The current release is fully open-source under MIT and ships the complete tool surface above. No quotas, no API keys, no telemetry. Runs locally as a subprocess of your MCP client (Claude Desktop, Claude Code, Cursor). The only limits are the documented MVP scope: read-only inspection, CLI-based live adapter, `sample_messages` capped at 50 messages, mock-only deep bag anomaly detection. See the [MVP limitations section of the README](https://github.com/yaniswav/TopicForge#mvp-limitations) for the full list.

## Pro tier — Coming July 2026

A paid tier is planned with advanced bag anomaly detection, URDF tools, multi-bag diffs, and dataset export helpers. Scope and pricing are not finalized — track progress in [`docs/product-plan.md`](https://github.com/yaniswav/TopicForge/blob/main/docs/product-plan.md).

## Links

- GitHub: https://github.com/yaniswav/TopicForge
- README and install guide: https://github.com/yaniswav/TopicForge/blob/main/README.md
- Product plan and roadmap: https://github.com/yaniswav/TopicForge/blob/main/docs/product-plan.md
- PyPI: https://pypi.org/project/topicforge/
