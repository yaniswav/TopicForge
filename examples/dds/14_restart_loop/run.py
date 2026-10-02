"""Example 14: a node in a restart loop.

nav_planner crashes at startup and its supervisor restarts it again and again.
Each restart is a new process, so a new participant GUID under the same name.
TopicForge shows one active nav_planner, older ones that left, and a
discovered/lost pair per crash. A GUID lives as long as one process; a name
is not unique.

    python run.py          # run the example and check what TopicForge reports
    python run.py --hold   # leave the programs up and ask your own MCP client
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness import (
    Bus,
    Checks,
    Node,
    TopicForge,
    run_example,
    show_events,
    show_participants,
    step,
    wait_for,
)

PLANNER = Node("nav_planner", "cyclone", reads=("scan:LidarScan:best_effort",))
NODES = (Node("lidar_driver", "cyclone", writes=("scan:LidarScan:best_effort",)), PLANNER)

CRASHES = 2
LEASE_WAIT_S = 45.0

PROMPT = (
    "Look at the last 10 minutes of DDS domain 0. Is any program restarting in a loop? "
    "How many times, and is it running now?"
)


def planners(parts: list[dict], status: str) -> list[dict]:
    return [p for p in parts if p.get("name") == PLANNER.name and p.get("status") == status]


async def scenario(tf: TopicForge, bus: Bus, checks: Checks) -> None:
    step(1, f"nav_planner crashes {CRASHES} times and is restarted", "list_participants")
    await tf.participants()  # TopicForge records each process while it is alive
    for _ in range(CRASHES):
        bus.crash(PLANNER.name)
        bus.start(PLANNER)
        await asyncio.sleep(4)
        await tf.participants()

    async def all_left() -> bool:
        return len(planners(await tf.participants(), "left")) >= CRASHES

    elapsed = await wait_for(all_left, LEASE_WAIT_S)
    parts = await tf.participants()
    show_participants(parts)
    checks.expect(elapsed is not None, f"{CRASHES} old nav_planner reported as left")
    checks.expect(len(planners(parts, "active")) == 1, "exactly one nav_planner is active now")

    step(2, "What does the timeline say?", "participant_events")
    events = [e for e in await tf.events() if e.get("name") == PLANNER.name]
    show_events(events)
    discovered = sum(e["event_type"] == "discovered" for e in events)
    lost = sum(e["event_type"] == "lost" for e in events)
    checks.expect(
        discovered == CRASHES + 1 and lost == CRASHES,
        f"{CRASHES + 1} discovered and {CRASHES} lost events for nav_planner",
    )
    checks.expect(
        len({e["guid"] for e in events}) == CRASHES + 1,
        "each restart is a new participant: a new GUID under the same name",
    )


if __name__ == "__main__":
    raise SystemExit(run_example("14 A node in a restart loop", NODES, scenario, prompt=PROMPT))
