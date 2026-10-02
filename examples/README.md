# TopicForge: examples

There are two kinds of example. The numbered walkthroughs below run against the mock adapter (no ROS2 or DDS install) and pair an MCP-client prompt with the expected tool calls and a short diagnosis. The live DDS examples in [`dds/`](dds/README.md) run real participants on a real bus; start them with `python examples/dds/run_all.py`.

| File | Scenario | Tools exercised |
| ---- | -------- | --------------- |
| [`01-discover-ros2-stack.md`](01-discover-ros2-stack.md) | Bring up TopicForge, list the graph, peek a topic | `health_check`, `list_topics`, `get_topic_info`, `sample_messages` |
| [`02-debug-qos-mismatch.md`](02-debug-qos-mismatch.md) | "My subscriber is not receiving": QoS diagnosis | `list_participants`, `detect_qos_mismatches`, `peek_dds_samples` |
| [`03-analyze-recording.md`](03-analyze-recording.md) | Post-mortem inspection of an MCAP / DB3 / BAG recording | `analyze_bag`, `peek_bag_samples` |
| [`04-monitor-topic-frequency.md`](04-monitor-topic-frequency.md) | Topic metrics and participant lifecycle, and where live adapters differ from the mock | `topic_metrics`, `participant_events` |

## Running the mock walkthroughs

```bash
pip install topicforge
TOPICFORGE_MODE=mock python -m topicforge
# Windows PowerShell: $env:TOPICFORGE_MODE="mock"; python -m topicforge
```

Point any MCP client at the server with `"env": { "TOPICFORGE_MODE": "mock" }`. The mock exposes all 12 tools against deterministic fixtures, so every example is reproducible byte for byte.

To run against a real bus, use `TOPICFORGE_MODE=live` and, for the DDS tools, `TOPICFORGE_DDS_BACKEND=cyclone` (see [`docs/DDS_QUICKSTART.md`](../docs/DDS_QUICKSTART.md)). The mock shows the shape of every response, not the behaviour of the live DDS adapters, which differ in two ways that examples 02 and 04 call out: user-topic payloads are not decoded, and `topic_metrics` only has data for the builtin discovery topics.
