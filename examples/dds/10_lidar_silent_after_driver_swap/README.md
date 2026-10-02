# 10 The LIDAR went silent after a driver swap

**Real use case.** Combines two problems, as a real incident does: a topic
name changed with the new supplier, and an old process was never stopped.

## The story

The LIDAR driver was replaced by one from another supplier, built on another
DDS implementation (Dust DDS). Since then the navigation planner receives no
scan.

| Program | Vendor | Writes | Reads |
|---|---|---|---|
| `nav_planner` | Cyclone DDS | | `scan` RELIABLE |
| `lidar_new` | Dust DDS | `lidar/scan` BEST_EFFORT | |
| `lidar_old` | Cyclone DDS | `scan` BEST_EFFORT | |

## Run it

```
python run.py            # run and check
python run.py --hold     # keep the programs running, ask your own MCP client
```

## What TopicForge shows

```
[1] Is a QoS mismatch blocking the scan?
    -> detect_qos_mismatches
    scan: Reliability (incompatible): writer lidar_old -> reader nav_planner

[2] Who writes and who reads each topic?
    -> peek_dds_samples (discovery)
    lidar/scan   writers: a9febda2.1ca20000.00000000.000001c1 (LidarScan)
                 readers: NOBODY
    scan         writers: lidar_old (LidarScan)
                 readers: nav_planner (LidarScan)

[3] What does nav_planner actually receive on scan?
    -> nav_planner output
    [nav_planner] rx scan: 0 in 1 s
    [nav_planner] rx scan: 0 in 1 s
```

- The mismatch report only finds the forgotten old driver. Stop there and
  you "fix" it by making the old driver RELIABLE, and the planner then
  receives scans from a driver that should not be running.
- The wiring shows the real cause: the new driver publishes on `lidar/scan`,
  a topic nobody reads. It appears by GUID: Dust DDS announces no name.

## Ask your agent

> Since we swapped the LIDAR driver, nav_planner receives no scan on DDS
> domain 0. Find every reason.

## Remember

- An empty or short mismatch report does not mean a healthy bus. A writer
  nobody reads, or a reader nobody writes, is the other half of the picture.
- After a swap, check for leftovers: two programs claiming the same role is
  a classic.
- TopicForge pairs readers and writers by topic name, assuming the same type
  and the default partition.
