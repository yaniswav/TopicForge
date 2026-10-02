# 14 A node in a restart loop

A program crashes at startup and its supervisor (systemd, a launch file, a
container restart policy) brings it back, again and again. From the outside it
looks "up" most of the time.

`nav_planner` crashes twice and is restarted each time. `lidar_driver` runs
normally.

## Run it

```
python run.py            # run and check (about 40 s)
python run.py --hold     # keep the programs running, ask your own MCP client
```

## Output

```
    lidar_driver       vendor=cyclone  active  host=DESKTOP-H0N0S7G
    topicforge         vendor=cyclone  active  host=DESKTOP-H0N0S7G
    nav_planner        vendor=cyclone  left    host=DESKTOP-H0N0S7G
    nav_planner        vendor=cyclone  left    host=DESKTOP-H0N0S7G
    nav_planner        vendor=cyclone  active  host=DESKTOP-H0N0S7G

[2] What does the timeline say?
    -> participant_events
    lost       nav_planner
    lost       nav_planner
    discovered nav_planner
    discovered nav_planner
    discovered nav_planner
```

Each restart is a new process, so a new DDS participant with a new GUID under
the same name: three `nav_planner`, one active. The timeline gives the count:
three discovered, two lost.

A GUID lives as long as one process, while a name is not unique: two
programs, or two lives of the same program, can share it. TopicForge tracks
discovery every 0.5 s in the background, so each restart shows up in the
timeline; a participant that cycles faster than that, between two passes, can
still be missed.

Prompt to try:

> Look at the last 10 minutes of DDS domain 0. Is any program restarting in
> a loop? How many times, and is it running now?
