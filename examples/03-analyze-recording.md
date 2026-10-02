# 03: Post-mortem inspection of a bag recording

A field deployment failed at 14:32:17 UTC and the team saved a `.mcap` of the
window around the failure. You want duration, message counts and anything
flagged, then a look at the messages of one topic. This walkthrough uses
`analyze_bag` and `peek_bag_samples`, against the mock's bag fixtures.

## Setup

```bash
pip install topicforge
TOPICFORGE_MODE=mock python -m topicforge
```

In mock mode no `rosbags` install is needed and no file is opened: the
mock adapter accepts any path ending in `.mcap`, `.db3` or `.bag` (or
with no extension) and returns fixtures. `/tmp/demo.mcap` does not have
to exist. The live run at the end of this page does need
`pip install topicforge[bags]` for `peek_bag_samples`.

The fixture models a 42.5-second recording of the differential robot
from [`01-discover-ros2-stack.md`](01-discover-ros2-stack.md), with four
topics and two canned anomaly strings.

## Conversation

You:

> Summarize the bag at `/tmp/demo.mcap`: how long, how many messages,
> anything suspicious?

Claude calls: `analyze_bag(path="/tmp/demo.mcap")` -> returns a
`BagAnalysis`:

```json
{
  "path": "/tmp/demo.mcap",
  "storage_format": "mcap",
  "duration_seconds": 42.5,
  "message_count": 1287,
  "topics": [
    {"name": "/cmd_vel", "message_type": "geometry_msgs/msg/Twist", "message_count": 425, "frequency_hz": 10.0},
    {"name": "/odom", "message_type": "nav_msgs/msg/Odometry", "message_count": 425, "frequency_hz": 10.0},
    {"name": "/scan", "message_type": "sensor_msgs/msg/LaserScan", "message_count": 425, "frequency_hz": 10.0},
    {"name": "/tf", "message_type": "tf2_msgs/msg/TFMessage", "message_count": 12, "frequency_hz": 0.28}
  ],
  "anomalies": [
    "/scan: 3 frames dropped between t=10.1s and t=10.4s",
    "/tf: static transforms only; no dynamic updates during recording"
  ],
  "mode_effective": "mock",
  "bag_format": "mcap",
  "samples_decoded_count": 0,
  "recording_duration_ns": 42500000000,
  "participants_recorded": []
}
```

`anomalies` is a list of plain strings. They are canned in the fixture:
no live code path detects anomalies today, so a real bag always comes
back with `"anomalies": []`.

You:

> Show me the actual messages on `/cmd_vel`.

Claude calls: `peek_bag_samples(path="/tmp/demo.mcap",
topic="/cmd_vel", count=3)` -> returns a `SampleResult` with 3 samples.
The first looks like this:

```json
{
  "topic": "/cmd_vel",
  "message_type": "geometry_msgs/msg/Twist",
  "timestamp_ns": 1700000000000000000,
  "payload": {
    "_decode_status": "full",
    "linear": {"x": 0.2, "y": 0.0, "z": 0.0},
    "angular": {"x": 0.0, "y": 0.0, "z": 0.0},
    "_msgtype": "geometry_msgs/msg/Twist"
  }
}
```

The mock fixture only holds samples for `/cmd_vel` (5 samples) and
`/odom` (3 samples). Asking for any other topic in the mock, `/scan`
included, returns an empty `samples` list rather than an error. A real
bag raises an error when the topic is not in the file, and lists the
topics it does contain.

You:

> Were there any non-ROS DDS participants recorded in the bag?

Claude responds referencing `BagAnalysis.participants_recorded`. It
is an empty list here, and it is empty on every real bag today: the
reader never populates it. The field exists so that bag participants
can later share the `ParticipantInfo` schema of `list_participants`.

## Going live

```bash
pip install topicforge[bags]
source /opt/ros/humble/setup.bash
TOPICFORGE_MODE=live python -m topicforge
```

The two bag tools behave differently in live mode, and both need the
`ros2` CLI on PATH (they are served by the ROS2 half):

- `analyze_bag` runs `ros2 bag info <path>` and parses its text output.
  It never uses `rosbags`. `storage_format` comes from the CLI output;
  `bag_format` and `recording_duration_ns` stay `null`,
  `samples_decoded_count` stays `0`, `anomalies` is empty. The path
  must exist.
- `peek_bag_samples` reads the file with `rosbags` and decodes
  messages, which is why it needs the `[bags]` extra. There is no
  fallback: without the library it raises an error carrying the
  install command.

With a DDS backend selected but no `ros2` on PATH, both tools raise the
"DDS observability only" error. With neither, the server serves the
mock fixtures: check that `health_check` says `mode="live"` before
trusting what a bag tool returns.

## Formats

`rosbags` (Apache 2.0, pure-Python) reads MCAP, ROS2 `.db3`, and ROS1
`.bag` recordings through a single `AnyReader` API, and
`peek_bag_samples` goes through it, so the same call works on any of the
three containers. The `ros2 bag info` path behind `analyze_bag` depends
on what your ROS2 install can open.
