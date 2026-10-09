---
name: inspect-ros2-robot
description: Inspect a ROS 2 robot or a recorded bag with TopicForge when the user asks what topics exist, what a topic publishes, or what a bag contains. Read-only.
---

# Inspect a ROS 2 robot or bag

Use the TopicForge tools (output contract 2: check `health_check.contract_version` is 2). The same procedure is available as the MCP prompt `inspect-ros2-robot`. They only read; never suggest publishing, commanding or changing the robot.

## Live graph

1. `health_check`. If `mode` is `mock`, the data is a fictional demo robot, not the user's: say so. If `ros_backend` is `none` there is no ROS 2 CLI; use the `diagnose-dds-bus` skill tools (`list_endpoints`) instead.
2. `list_topics`: names, types, publisher and subscriber counts, in `topics`. QoS is not in the listing: `get_topic_info` gives `publisher_qos`, `subscription_qos` and the node names on each side.
3. `get_topic_info` for one topic: reliability, durability (`transient_local` means latched, as on `/tf_static`).
4. `sample_messages` for content. Keep `count` small and raise `timeout_s` for topics slower than 1 Hz.

## sample_messages options

- Arrays, strings and bytes are cut at 128 elements by default and listed in `_truncated_fields`. Set `max_array_length` (up to 65536, or null for everything) to read a full `LaserScan`; null is large for images and point clouds.
- `arrays_summary_only: true` shows only the non-array fields. Use it for images and point clouds.
- `timestamp_ns` is the message header stamp: sim time on a simulation, 0 when `stamp_source` is `none`. `received_ns` is the wall clock at print time. Do not mix the two.
- A short result carries a `note` saying why. A latched topic usually holds one message: use `count` 1.

## Bags

- `analyze_bag` (`.mcap`, `.db3`, `rosbag2_*` directory): duration, message count, per-topic stats. `frequency_basis` says how the rate was computed (`topic_span` or `bag_duration`); a `latched` topic can have a null rate. Anomaly detection exists in mock mode only.
- `peek_bag_samples` (also reads ROS 1 `.bag`) for decoded messages, with a `_decode_status` per sample. It needs the `rosbags` library; if missing, tell the user.

More detail: `references/tool-notes.md`.

## Answering

Report what the tools returned, name the tool and field, and separate observed facts from guesses. If a topic is silent or a call timed out, say that is all you know.
