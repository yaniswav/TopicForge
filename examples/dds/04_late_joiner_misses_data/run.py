"""Example 04: a late joiner misses the data.

Mission control publishes the mission once, VOLATILE. The planner starts later
and asks for TRANSIENT_LOCAL so it can still get it. A reader cannot demand
more durability than its writer offers, so DDS refuses to match them.

    python run.py          # run the example and check what TopicForge reports
    python run.py --hold   # leave the programs up and ask your own MCP client
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness import (
    Bus,
    Checks,
    Node,
    TopicForge,
    mismatch_on,
    run_example,
    show_mismatches,
    step,
    who,
)
from received import show_received

NODES = (
    Node("mission_control", "cyclone", writes=("mission:Status:reliable,volatile",), rate_hz=1.0),
    Node("nav_planner", "cyclone", reads=("mission:Status:reliable,transient_local",)),
)

PROMPT = "nav_planner started after mission_control and never got the mission on DDS domain 0. Why?"


async def scenario(tf: TopicForge, bus: Bus, checks: Checks) -> None:
    step(1, "Why does the planner never get the mission?", "detect_qos_mismatches")
    parts = await tf.participants()
    mismatches = await tf.mismatches()
    show_mismatches(mismatches, parts)

    found = mismatch_on(mismatches, "mission", "Durability")
    checks.expect(found is not None, "mission: Durability mismatch reported")
    if found:
        checks.expect(
            who(found, "writer", parts) == "mission_control"
            and who(found, "reader", parts) == "nav_planner",
            "mission: VOLATILE writer mission_control, TRANSIENT_LOCAL reader nav_planner",
        )
        checks.expect(
            "Reliability" not in found["incompatible_policies"],
            "mission: reliability is not the problem (both RELIABLE)",
        )
    await show_received(bus, checks, 2, "nav_planner", "mission", receives=False)


if __name__ == "__main__":
    raise SystemExit(
        run_example("04 A late joiner misses the data", NODES, scenario, prompt=PROMPT)
    )
