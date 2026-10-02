# 13 The deadline is not offered

**Real use case.** A reader requires a minimum update rate; the new writer
does not promise one. The reader gets nothing at all, not even late data.

## The story

| Program | Writes | Reads |
|---|---|---|
| `nav_planner` | | `scan` deadline 100 ms, `imu` deadline 100 ms |
| `lidar_driver` | `scan`, no deadline | |
| `imu_driver` | `imu` deadline 10 ms | |

The planner receives the IMU and never the scan.

## Run it

```
python run.py            # run and check
python run.py --hold     # keep the programs running, ask your own MCP client
```

## What TopicForge shows

```
[1] Why does the planner get the IMU but not the scan?
    -> detect_qos_mismatches
    scan: Deadline (incompatible): writer lidar_driver -> reader nav_planner
```

- A Deadline is a promise: the writer offers "a new sample at least every
  N ms", the reader requests one. The offer must be at least as strict as
  the request.
- No deadline means "no promise" (an infinite period). That is the default
  in most DDS and ROS 2 setups, so this mismatch appears as soon as one
  reader starts requiring a deadline.
- The IMU driver promises 10 ms for a 100 ms request: compatible, not
  reported.

## What this does not do

TopicForge sees the deadline each side **declares**. It does not see whether
a running writer actually meets it: that needs the data itself, which
TopicForge does not decode yet.

## Ask your agent

> nav_planner receives the imu topic but never the scan topic on DDS domain
> 0. Why?

## Remember

- A deadline mismatch blocks the connection entirely; it is not a "late
  data" warning.
- Fix: declare a deadline on the writer at least as strict as the readers
  require, or relax the reader.
