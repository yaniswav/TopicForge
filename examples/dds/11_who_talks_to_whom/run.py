"""Example 11: who talks to whom?

The robot was assembled from two suppliers. The design document says how the
programs are wired; nobody knows whether the robot matches it. TopicForge
rebuilds the wiring from what each program announces on the bus: for every
topic, who writes it and who reads it, with the data type.

The motor controller comes from a supplier using Dust DDS. TopicForge sees
it without any Dust software, through standard discovery, but Dust announces
no name: it appears by its GUID. Be explicit about what you do not see.

    python run.py           # run the example and check what TopicForge reports
    python run.py --hold    # keep the programs running and ask your own MCP client
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness import Bus, Checks, Node, TopicForge, run_example, show_wiring, step

NODES = (
    Node("lidar_driver", "cyclone", writes=("scan:LidarScan:best_effort",)),
    Node(
        "nav_planner",
        "cyclone",
        reads=("scan:LidarScan:best_effort",),
        writes=("cmd_vel:Twist",),
    ),
    Node(
        "motor_controller",
        "dust",
        reads=("cmd_vel:Twist",),
        writes=("heartbeat:Heartbeat",),
        rate_hz=1.0,
    ),
)

PROMPT = (
    "Draw the wiring of DDS domain 0: for each topic, who writes it, who reads it, "
    "and with which type. Point out anything that looks unconnected."
)


async def scenario(tf: TopicForge, bus: Bus, checks: Checks) -> None:
    step(1, "Who writes and who reads each topic?", "peek_dds_samples (discovery)")
    wiring = await tf.wiring()
    show_wiring(wiring)

    def names(topic: str, side: str) -> list[str]:
        return [who for who, _ in wiring.get(topic, {}).get(side, [])]

    checks.expect(names("scan", "writers") == ["lidar_driver"], "scan written by lidar_driver")
    checks.expect(names("scan", "readers") == ["nav_planner"], "scan read by nav_planner")
    checks.expect(names("cmd_vel", "writers") == ["nav_planner"], "cmd_vel written by nav_planner")
    cmd_readers = names("cmd_vel", "readers")
    checks.expect(
        len(cmd_readers) == 1 and cmd_readers[0] not in {n.name for n in NODES},
        "cmd_vel read by the Dust motor controller, seen by GUID only (no name)",
    )
    checks.expect(
        len(names("heartbeat", "writers")) == 1 and not names("heartbeat", "readers"),
        "heartbeat written by the motor controller, read by nobody",
    )
    types = {t for side in wiring.get("cmd_vel", {}).values() for _, t in side}
    checks.expect(types == {"Twist"}, "cmd_vel: both sides announce the same type, Twist")


if __name__ == "__main__":
    raise SystemExit(run_example("11 Who talks to whom?", NODES, scenario, prompt=PROMPT))
