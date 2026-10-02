# 03 A node crashed

**Concept:** liveliness and leases. A DDS participant that stops cleanly
says goodbye. One that crashes says nothing, and the bus only learns it is
gone when its lease expires: the participant promised to show signs of life
within a lease duration, and stopped doing so.

## The story

The LIDAR driver crashes: the process is killed, nothing is sent. How long
until the rest of the robot can know?

| Program | Writes | Reads |
|---|---|---|
| `lidar_driver` | `scan` | |
| `nav_planner` | | `scan` |

## Run it

```
python run.py            # run and check (about 30 s: it waits for the lease)
python run.py --hold     # keep the programs running, ask your own MCP client
```

## What TopicForge shows

```
[2] The LIDAR driver crashes. How long until the bus notices?
    -> list_participants
  killed lidar_driver
    lidar_driver reported as left after 14 s

[3] What happened on the bus, in order?
    -> participant_events
    lost       lidar_driver
    discovered lidar_driver
    discovered nav_planner
    discovered topicforge
```

- Cyclone DDS uses a 10 s lease by default. TopicForge polls the bus, so it
  reports the departure a few seconds after the lease expires.
- TopicForge notices a departure when it is asked about the bus: it polls,
  it does not keep a continuous history in the background. An agent watching
  for crashes has to ask regularly.
- The lease is a per-vendor default you can configure. Dust DDS, for
  example, uses 100 s: a crashed Dust program stays "active" much longer.

## Ask your agent

> Watch DDS domain 0. I am going to stop a program: tell me which one left
> and when.

Then stop one of the programs from another terminal (or stop `--hold` with
Ctrl+C and ask what happened).

## Remember

- "Still listed" does not mean "still alive": a crash is only visible after
  the lease.
- For a safety function, choose a lease short enough for your reaction time.
