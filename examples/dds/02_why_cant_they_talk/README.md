# 02 Why can't they talk?

**Concept:** QoS compatibility, Reliability. A reader and a writer on the
same topic and the same type only connect if their QoS are compatible. When
they are not, DDS connects nothing and raises no error in your code by
default: the reader just receives nothing.

## The story

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
    scan: Reliability (incompatible): writer lidar_driver -> reader nav_planner
```

- On `scan`, the planner **requests** RELIABLE delivery and the driver only
  **offers** BEST_EFFORT. A reader cannot ask for more than the writer
  offers, so DDS refuses the match.
- On `odom` the QoS differ the other way: the writer offers RELIABLE and the
  reader asks for BEST_EFFORT. That is allowed (offering more than asked is
  fine), so TopicForge does not report it.

## Ask your agent

> nav_planner never receives the scan topic on DDS domain 0. Why?

## Remember

- DDS QoS follow a request/offered rule: the writer must offer at least what
  the reader requests.
- A QoS mismatch is silent in most applications. Look for it first when a
  subscriber gets nothing.
- Fix: make the driver RELIABLE, or the planner BEST_EFFORT (usual for
  high-rate sensor data, where the next scan replaces a lost one).
