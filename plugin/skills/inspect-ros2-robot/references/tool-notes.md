# Tool notes for ROS 2 inspection

- `list_topics` raises an MCP error when no `ros2` CLI is available, and returns an empty list when the graph is empty or discovery timed out. An empty list is not proof the graph is empty.
- `sample_messages` waits at most `timeout_s` (1 to 40, default 10) and returns within `timeout_s` plus a few seconds. ROS 2 calls run one at a time, so a long wait delays other ROS 2 calls (they fail with `busy` if they cannot start in time); DDS calls and `health_check` are not delayed. At most 50 messages per call.
- Messages over the size cap (1 MiB by default, `TOPICFORGE_MAX_SAMPLE_BYTES`) are dropped and the `note` says so. `nan` and `inf` floats come back as strings.
- Sim time: on a simulation the header stamp is the simulation clock. Compare `timestamp_ns` values with each other, not with `received_ns`.
- `analyze_bag` per-topic `frequency_hz` is (n - 1) / (last - first message time). A latched topic whose messages all fall within 1 second (for example `/tf_static`) has a null rate. For a large `.mcap` (over 200 MiB) or an unreadable bag the rate falls back to count / bag duration and `note` says why.
- `peek_bag_samples`: bags that embed no message definitions (rosbag2 `.db3` from Humble) are decoded with the definitions of the recorded distro, or Humble if none is recorded; `note` says which. Arrays over 4096 elements are cut and listed in `note`.
- `_decode_status` is `full`, `partial` or `raw`; treat `raw` as undecoded bytes.
- In mock mode every tool returns fixtures for a fictional differential-drive robot. Say so before drawing any conclusion.
