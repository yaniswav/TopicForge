"""Example 02: why can't they talk?

The navigation planner subscribes to the LIDAR scan and never receives a
single message. Both programs run, both use the same topic and the same type.
TopicForge finds the reason: the planner asks for RELIABLE delivery, the
LIDAR driver only offers BEST_EFFORT, so DDS refuses to connect them.

A second pair, on the odometry topic, differs too but in the allowed
direction (a BEST_EFFORT reader accepts a RELIABLE writer). TopicForge must
not report it.

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
    matched_on,
    mismatch_on,
    run_example,
    show_mismatches,
    step,
    who,
)
from received import show_received

NODES = (
    Node(
        "lidar_driver",
        "cyclone",
        writes=("scan:LidarScan:best_effort",),
        reads=("odom:Odom:best_effort",),
    ),
    Node(
        "nav_planner",
        "cyclone",
        reads=("scan:LidarScan:reliable",),
        writes=("odom:Odom:reliable",),
    ),
)

PROMPT = "nav_planner never receives the scan topic on DDS domain 0. Why?"


async def scenario(tf: TopicForge, bus: Bus, checks: Checks) -> None:
    step(1, "Which reader/writer pairs can never talk?", "detect_qos_mismatches")
    parts = await tf.participants()
    mismatches = await tf.mismatches()
    show_mismatches(mismatches, parts)

    scan = mismatch_on(mismatches, "scan", "Reliability")
    checks.expect(scan is not None, "scan: Reliability mismatch reported")
    if scan:
        checks.expect(
            who(scan, "writer", parts) == "lidar_driver"
            and who(scan, "reader", parts) == "nav_planner",
            "scan: the BEST_EFFORT writer is lidar_driver, the RELIABLE reader nav_planner",
        )
    checks.expect(
        not any(m["topic"] == "odom" for m in mismatches["reports"]),
        "odom: RELIABLE writer to BEST_EFFORT reader is allowed, not reported",
    )
    odom = matched_on(mismatches, "odom")
    checks.expect(
        len(odom) == 1 and who(odom[0], "reader", parts) == "lidar_driver",
        "odom: `matched` lists lidar_driver as the reader DDS will connect",
    )
    checks.expect(not matched_on(mismatches, "scan"), "scan: no matched pair")
    await show_received(bus, checks, 3, "nav_planner", "scan", receives=False)

    await show_received(bus, checks, 4, "lidar_driver", "odom", receives=True)


if __name__ == "__main__":
    raise SystemExit(run_example("02 Why can't they talk?", NODES, scenario, prompt=PROMPT))
