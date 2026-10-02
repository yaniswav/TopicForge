# 12 The safety monitor dropped out

**Real use case.** A safety function dies silently. What does the robot
lose, and how fast can anyone know?

## The story

The safety monitor watches the velocity commands and publishes the
emergency-stop state. It crashes. The motor controller keeps executing
commands: nothing in it notices that `estop` lost its only writer.

| Program | Writes | Reads |
|---|---|---|
| `nav_planner` | `cmd_vel` | |
| `safety_monitor` | `estop` (TRANSIENT_LOCAL) | `cmd_vel` |
| `motor_controller` | | `cmd_vel`, `estop` |

## Run it

```
python run.py            # run and check (about 30 s: it waits for the lease)
python run.py --hold     # keep the programs running, ask your own MCP client
```

## What TopicForge shows

```
[2] The safety monitor crashes. When does the bus know?
    -> list_participants
  killed safety_monitor
    safety_monitor reported as left after 10 s

[3] What does the robot lose?
    -> peek_dds_samples (discovery)
    cmd_vel      writers: nav_planner (Twist)
                 readers: motor_controller (Twist)
    estop        writers: NOBODY
                 readers: motor_controller (Heartbeat)
```

- `estop` has no writer left: the motor controller waits for a signal that
  will never come.
- `cmd_vel` lost a reader: nobody supervises the commands anymore.

## What this does not do

Read this before relying on anything like it:

- The discovery lease is a discovery mechanism, not a safety mechanism.
  Detection takes as long as the lease the **dead** program announced: 10 s
  for Cyclone DDS by default, 100 s for several other vendors. TopicForge
  reports a crash only when the lease expires, and cannot tell it from a clean
  leave.
- A real safety design uses the Liveliness QoS (with a short lease on the
  safety topics) and a fail-safe reaction in the consumer. TopicForge checks
  that a reader's Liveliness request is compatible with the writer's offer, but
  it cannot observe runtime liveliness: a writer that is silent while its
  process is up looks healthy.

## Ask your agent

> Watch DDS domain 0. If a program disappears, tell me which topics lose
> their only writer or a reader, and what that means for the robot.

## Remember

- The consequence of a crash is in the wiring: which topics lost their only
  writer.
- A monitoring tool tells you after the fact. The robot itself must fail
  safe on its own.
