# 01: Discover the ROS2 stack

You are handed a new robotics codebase and want to know what is on the graph
without scrolling through `ros2 topic ...` invocations. This walkthrough uses
`health_check`, `list_topics`, `get_topic_info` and `sample_messages`, against
the mock, so no ROS2 is required.

## Setup

```bash
pip install topicforge
TOPICFORGE_MODE=mock python -m topicforge
```

The mock fixture models a small differential robot with LIDAR + RGB
camera. Five ROS2 topics, deterministic across runs.

## Conversation

You:

> What runtime is this MCP serving, and what topics are on the graph?

Claude calls: `health_check()` -> confirms `mode="mock"`,
`requested_mode="mock"`, `ros_backend="mock"`. Then `list_topics()` ->
returns a `TopicListing` of 5 topics: `/cmd_vel`, `/odom`, `/scan`, `/tf`,
`/camera/image_raw` with publisher/subscriber counts and message types
(`returned` 5, `total` 5, `truncated` false). QoS is not in the listing.

You:

> Show me the structure of `/scan` and what its messages look like.

Claude calls: `get_topic_info(topic="/scan")` -> returns
`message_type="sensor_msgs/msg/LaserScan"`, `publisher_count=1`,
`subscriber_count=1`, `publisher_qos.reliability="best_effort"`,
`publisher_nodes=["/lidar_driver"]`, `subscriber_nodes=["/nav_planner"]`. Then
`sample_messages(topic="/scan", count=2)` -> returns 2 deterministic
`MessageSample` objects (the fixture holds 3, so `count` of 3 or more
returns 3).

You:

> Summarize the stack for me.

Claude answers along these lines:

> This is a small mobile robot. Three sensor topics
> (`/scan` from a 2D LIDAR, `/camera/image_raw` from an RGB camera,
> `/tf` for transforms), one odometry feedback (`/odom`), and one
> command input (`/cmd_vel`). All five are mocked from
> deterministic fixtures: `health_check` reports `mode="mock"` and
> every result carries `mode_effective="mock"`. None of this
> comes from a real graph.

## Going live

Replace the run command with:

```bash
source /opt/ros/humble/setup.bash
TOPICFORGE_MODE=live python -m topicforge
```

The same four tool calls work against any real ROS2 graph. The wire shape is
identical between mock and live; the `mode_effective` field on every response,
and `mode` on `health_check`, are the only hints. If `ros2` is not on PATH the server falls back to
the fixtures and `health_check` says `mode="mock"` while
`requested_mode` stays `"live"`.

## Variants

- No publisher on the topic: `sample_messages` in live mode waits a
  short grace period and returns an empty `samples` list; `count` is 0
  and `note` says no publisher is announced.
- Headerless message types: `samples[i].timestamp_ns` is `0`
  for types without a `Header` (e.g. `std_msgs/String`), and
  `stamp_source` is `none`.
