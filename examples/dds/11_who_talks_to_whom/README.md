# 11 Who talks to whom?

You inherit a robot assembled from two suppliers. The design document says how
it is wired; TopicForge shows how it is wired on the wire.

## Programs

| Program | Vendor | Writes | Reads |
|---|---|---|---|
| `lidar_driver` | Cyclone DDS | `scan` | |
| `nav_planner` | Cyclone DDS | `cmd_vel` | `scan` |
| `motor_controller` | Dust DDS | `heartbeat` | `cmd_vel` |

## Run it

```
python run.py            # run and check
python run.py --hold     # keep the programs running, ask your own MCP client
```

## The wiring on the wire

```
[1] Who writes and who reads each topic?
    -> peek_dds_samples (discovery)
    cmd_vel      writers: nav_planner (Twist)
                 readers: a9febda2.f49f0000.00000000.000001c1 (Twist)
    heartbeat    writers: a9febda2.f49f0000.00000000.000001c1 (Heartbeat)
                 readers: NOBODY
    scan         writers: lidar_driver (LidarScan)
                 readers: nav_planner (LidarScan)
```

Each reader and writer is attached to its program through its GUID: the first
12 bytes of an endpoint GUID are its participant's. The Dust motor controller
is fully visible, without any Dust software on TopicForge's side, but it shows
by GUID only: Dust DDS announces no name, and its vendor shows as `unknown`.
Say so rather than guess.

`heartbeat` has a writer and no reader. That may be expected (a diagnostic
nobody consumes yet) or a missing monitor; the wiring raises the question and
you answer it.

Names and vendor ids are
optional extras that some vendors do not send.

Prompt to try:

> Draw the wiring of DDS domain 0: for each topic, who writes it, who reads
> it, and with which type. Point out anything that looks unconnected.
