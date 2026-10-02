# 02 Why can't they talk?

A reader and a writer on the same topic and the same type only connect if
their QoS are compatible. When they are not, DDS connects nothing and raises
no error in your code by default: the reader just receives nothing.

The navigation planner subscribes to the LIDAR scan and never receives a
message. Both programs run, the topic name is right, the type is right.

| Program | Writes | Reads |
|---|---|---|
| `lidar_driver` | `scan` BEST_EFFORT | `odom` BEST_EFFORT |
| `nav_planner` | `odom` RELIABLE | `scan` RELIABLE |

## Run it

```
python run.py            # run and check
python run.py --hold     # keep the programs running, ask your own MCP client
```

## What TopicForge shows

```
[1] Which reader/writer pairs can never talk?
    -> detect_qos_mismatches
    odom: matched (declared QoS): writer nav_planner -> reader lidar_driver
    scan: Reliability (incompatible): writer lidar_driver -> reader nav_planner
        Reliability: reader asks RELIABLE, writer offers BEST_EFFORT

[3] What does nav_planner actually receive on scan?
    -> nav_planner output
    [nav_planner] rx scan: 0 in 1.0 s
    [nav_planner] rx scan: 0 in 1.0 s

[4] What does lidar_driver actually receive on odom?
    -> lidar_driver output
    [lidar_driver] rx odom: 10 in 1.0 s, last seq 50
    [lidar_driver] rx odom: 10 in 1.0 s, last seq 60
```

On `scan`, the planner requests RELIABLE delivery and the driver only
offers BEST_EFFORT. A reader cannot ask for more than the writer offers, so
DDS refuses the match.

On `odom` the QoS differ the other way: the writer offers RELIABLE and the
reader asks for BEST_EFFORT. Offering more than asked is allowed, so
TopicForge does not report a mismatch and lists the pair under `matched`, the
pairs DDS will connect on the declared QoS.

## Fix

Make the driver RELIABLE, or the planner BEST_EFFORT (usual for high-rate
sensor data, where the next scan replaces a lost one). Since a QoS mismatch is
silent in most applications, it is the first thing to check when a subscriber
gets nothing.

Prompt to try:

> nav_planner never receives the scan topic on DDS domain 0. Why?
