# 01 Who is on the bus?

Concept: DDS discovery. Every DDS program announces itself on the bus,
whatever its vendor, through standard discovery messages. TopicForge reads
those announcements; it does not need to know the programs in advance.

## The story

A small robot runs three programs: a watchdog from one DDS vendor (Dust DDS),
a navigation planner and a motor controller from another (Cyclone DDS). You
just got access to the robot and want to know what runs on it.

| Program | Vendor | Writes | Reads |
|---|---|---|---|
| `watchdog` | Dust DDS | `heartbeat` | |
| `nav_planner` | Cyclone DDS | `cmd_vel` | `heartbeat` |
| `motor_controller` | Cyclone DDS | | `cmd_vel` |

## Run it

```
python run.py            # run and check
python run.py --hold     # keep the programs running, ask your own MCP client
```

## What TopicForge shows

```
[1] Who is on the bus?
    -> list_participants
    nav_planner        vendor=cyclone  active  host=DESKTOP-H0N0S7G
    motor_controller   vendor=cyclone  active  host=DESKTOP-H0N0S7G
    topicforge         vendor=cyclone  active  host=DESKTOP-H0N0S7G
    (no name)          vendor=unknown  active  host=?
```

`topicforge` is TopicForge itself: it joins the bus as a read-only
participant, so it is listed too. The Cyclone programs announce a name (the
standard DDS EntityName QoS) and their host. The Dust program has neither:
Dust DDS does not announce them, and it does not put its vendor id where
TopicForge can read it, so it shows as `unknown`. It is still seen, because
standard discovery does not depend on the vendor.

## Ask your agent

With `python run.py --hold` running and TopicForge configured in your MCP
client (see `../README.md`):

> Who is on DDS domain 0? For each participant give its name, vendor and host.

Give your participants names. An unnamed participant is a GUID, and a GUID
tells a human nothing.
