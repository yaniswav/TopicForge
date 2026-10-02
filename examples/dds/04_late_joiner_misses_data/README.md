# 04 A late joiner misses the data

**Concept:** QoS compatibility, Durability. Durability says whether a writer
keeps data for readers that join later. VOLATILE keeps nothing;
TRANSIENT_LOCAL keeps the last values for late joiners.

## The story

Mission control publishes the robot's mission when it starts. The navigation
planner boots later, and asks DDS to give it the last mission on arrival
(TRANSIENT_LOCAL). Mission control publishes VOLATILE: it keeps nothing.

| Program | Writes | Reads |
|---|---|---|
| `mission_control` | `mission` RELIABLE, VOLATILE | |
| `nav_planner` | | `mission` RELIABLE, TRANSIENT_LOCAL |

## Run it

```
python run.py            # run and check
python run.py --hold     # keep the programs running, ask your own MCP client
```

## What TopicForge shows

```
[1] Why does the planner never get the mission?
    -> detect_qos_mismatches
    mission: Durability (incompatible): writer mission_control -> reader nav_planner

[2] What does nav_planner actually receive on mission?
    -> nav_planner output
    [nav_planner] rx mission: 0 in 1 s
    [nav_planner] rx mission: 0 in 1 s
```

- The reader requests TRANSIENT_LOCAL, the writer offers VOLATILE. Same
  request/offered rule as in example 02: DDS refuses the match, and the
  planner gets nothing at all, not even the messages sent after it joined.

## Ask your agent

> nav_planner started after mission_control and never got the mission on DDS
> domain 0. Why?

## Remember

- Durability mismatches look like a startup-order bug ("it works if I start
  the planner first"), but they block every message, not only the old ones.
- Fix: publish configuration-like data (missions, maps, parameters) with
  TRANSIENT_LOCAL on the writer.
