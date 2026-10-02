"""Example 03: a node crashed.

The LIDAR driver dies without a word: no clean shutdown, no goodbye message
on the bus. DDS notices only when the driver's lease expires (10 s for
Cyclone DDS by default). TopicForge then reports the participant as left,
and its event timeline shows when it joined and when it was lost.

    python run.py           # run the example and check what TopicForge reports
    python run.py --hold    # keep the programs running and ask your own MCP client
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness import (
    Bus,
    Checks,
    Node,
    TopicForge,
    by_name,
    run_example,
    show_events,
    show_participants,
    step,
    wait_for,
)

NODES = (
    Node("lidar_driver", "cyclone", writes=("scan:LidarScan:best_effort",)),
    Node("nav_planner", "cyclone", reads=("scan:LidarScan:best_effort",)),
)

LEASE_WAIT_S = 40.0  # Cyclone's 10 s lease, plus discovery and polling margin

PROMPT = (
    "Watch DDS domain 0. I am going to stop a program: tell me which one left and when. "
    "(Stop it from another terminal, or end this example with Ctrl+C.)"
)


async def scenario(tf: TopicForge, bus: Bus, checks: Checks) -> None:
    step(1, "Who is on the bus before the crash?", "list_participants")
    parts = await tf.participants()
    show_participants(parts)
    lidar = by_name(parts, "lidar_driver")
    if not checks.expect(
        lidar is not None and lidar.get("status") == "active", "lidar_driver active"
    ):
        return
    assert lidar is not None
    guid = lidar["guid"]

    step(2, "The LIDAR driver crashes. How long until the bus notices?", "list_participants")
    bus.crash("lidar_driver")

    async def lidar_left() -> bool:
        now = await tf.participants()
        return any(p["guid"] == guid and p.get("status") == "left" for p in now)

    elapsed = await wait_for(lidar_left, LEASE_WAIT_S)
    if elapsed is not None:
        print(f"    lidar_driver reported as left after {elapsed:.0f} s")
    checks.expect(elapsed is not None, f"lidar_driver reported as left within {LEASE_WAIT_S:.0f} s")
    nav = by_name(await tf.participants(), "nav_planner")
    checks.expect(nav is not None and nav.get("status") == "active", "nav_planner still active")

    step(3, "What happened on the bus, in order?", "participant_events")
    events = await tf.events()
    show_events(events)
    checks.expect(
        any(e["guid"] == guid and e["event_type"] == "lost" for e in events),
        "timeline has a 'lost' event for lidar_driver",
    )


if __name__ == "__main__":
    raise SystemExit(run_example("03 A node crashed", NODES, scenario, prompt=PROMPT))
