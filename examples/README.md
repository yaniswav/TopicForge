# TopicForge: examples

Four end-to-end walkthroughs, each runnable against the **mock adapter**
(no ROS2 / DDS install required). Each example pairs an MCP-client
prompt with the expected tool calls and a short LLM-facing diagnosis.

| File | Scenario | Tools exercised |
| ---- | -------- | --------------- |
| [`01-discover-ros2-stack.md`](01-discover-ros2-stack.md) | Bring up TopicForge, list the graph, peek a topic | `health_check`, `list_topics`, `get_topic_info`, `sample_messages` |
| [`02-debug-qos-mismatch.md`](02-debug-qos-mismatch.md) | "My subscriber is not receiving": multi-vendor QoS diagnosis | `list_participants`, `detect_qos_mismatches`, `peek_dds_samples` |
| [`03-analyze-recording.md`](03-analyze-recording.md) | Post-mortem inspection of an MCAP / DB3 / BAG recording | `analyze_bag`, `peek_bag_samples` |
| [`04-monitor-topic-frequency.md`](04-monitor-topic-frequency.md) | Topic metrics and participant lifecycle, and where live adapters differ from the mock | `topic_metrics`, `participant_events` |

## How to run any example

```bash
pip install topicforge
TOPICFORGE_MODE=mock python -m topicforge
# Windows PowerShell: $env:TOPICFORGE_MODE="mock"; python -m topicforge
```

Point any MCP client (Claude Desktop / Claude Code / Cursor / Cline)
at this server with `"env": { "TOPICFORGE_MODE": "mock" }` in the
config. The mock adapter exposes all 11 tools against deterministic
fixtures, so every example is reproducible byte-for-byte.

To run against a real bus, swap `TOPICFORGE_MODE=mock` for
`TOPICFORGE_MODE=live` and (for the DDS examples) set
`TOPICFORGE_DDS_BACKEND=cyclone` (installable from PyPI) or `=fast` (needs
a Python binding built from eProsima's sources). See
[`docs/DDS_QUICKSTART.md`](../docs/DDS_QUICKSTART.md) for backend setup.

The mock fixtures show the shape of every response, not the behaviour of
the live DDS adapters, which are not validated against a real bus yet and
differ from the mock in two ways that examples 02 and 04 call out:
user-topic payloads are not decoded, and `topic_metrics` only has data for
the builtin discovery topics.
