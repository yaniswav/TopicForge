"""Example 01: who is on the bus?

A small robot runs three programs from two DDS vendors. You ask TopicForge
to list them: their names, their vendor, the machine they run on.

    python run.py           # run the example and check what TopicForge reports
    python run.py --hold    # keep the programs running and ask your own MCP client
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness import (
    VENDOR_TAG,
    Bus,
    Checks,
    Node,
    TopicForge,
    by_name,
    run_example,
    show_participants,
    step,
)

NODES = (
    Node("watchdog", "dust", writes=("heartbeat:Heartbeat",), rate_hz=1.0),
    Node(
        "nav_planner",
        "cyclone",
        reads=("heartbeat:Heartbeat",),
        writes=("cmd_vel:Twist",),
    ),
    Node("motor_controller", "cyclone", reads=("cmd_vel:Twist",)),
)

PROMPT = "Who is on DDS domain 0? For each participant give its name, vendor and host."


async def scenario(tf: TopicForge, bus: Bus, checks: Checks) -> None:
    step(1, "Who is on the bus?", "list_participants")
    parts = await tf.participants()
    show_participants(parts)

    # Every program, plus TopicForge's own read-only participant.
    checks.expect(len(parts) >= len(NODES) + 1, f"at least {len(NODES) + 1} participants seen")
    for node in NODES:
        if node.vendor == "dust":
            # Dust DDS cannot announce a participant name: it shows up, unnamed.
            # Exactly one such participant: this example starts exactly one.
            unnamed = [
                p for p in parts if p.get("vendor") == VENDOR_TAG["dust"] and not p.get("name")
            ]
            checks.expect(
                len(unnamed) == 1, f"{node.name} ({node.vendor}) seen once, without a name"
            )
            continue
        found = by_name(parts, node.name)
        checks.expect(found is not None, f"{node.name} found by name")
        if found:
            checks.expect(
                found.get("vendor") == VENDOR_TAG[node.vendor], f"{node.name} is {node.vendor}"
            )
            checks.expect(bool(found.get("hostname")), f"{node.name} reports its host")


if __name__ == "__main__":
    raise SystemExit(run_example("01 Who is on the bus?", NODES, scenario, prompt=PROMPT))
