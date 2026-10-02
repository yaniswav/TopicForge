"""Example 13: the deadline is not offered.

The planner requires a scan every 100 ms (Deadline on its reader). The new
LIDAR driver declares no deadline, which means no promise, so DDS does not
match them and the planner receives nothing, not even late scans.

The IMU pair is the control: the driver promises 10 ms, the planner asks for
100 ms. Offering more than asked is fine and must not be reported.
TopicForge sees declared deadlines, not whether a writer meets them.

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
    Node(
        "nav_planner",
        "cyclone",
        reads=("scan:LidarScan:reliable,deadline=100", "imu:Imu:reliable,deadline=100"),
    ),
    Node("lidar_driver", "cyclone", writes=("scan:LidarScan:reliable",)),
    Node("imu_driver", "cyclone", writes=("imu:Imu:reliable,deadline=10",)),
)

PROMPT = "nav_planner receives the imu topic but never the scan topic on DDS domain 0. Why?"


async def scenario(tf: TopicForge, bus: Bus, checks: Checks) -> None:
    step(1, "Why does the planner get the IMU but not the scan?", "detect_qos_mismatches")
    parts = await tf.participants()
    mismatches = await tf.mismatches()
    show_mismatches(mismatches, parts)

    scan = mismatch_on(mismatches, "scan", "Deadline")
    checks.expect(scan is not None, "scan: Deadline mismatch reported")
    if scan:
        checks.expect(
            who(scan, "writer", parts) == "lidar_driver"
            and who(scan, "reader", parts) == "nav_planner",
            "scan: lidar_driver offers no deadline, nav_planner requires 100 ms",
        )
    checks.expect(
        not any(m["topic"] == "imu" for m in mismatches["reports"]),
        "imu: 10 ms offered for 100 ms requested is compatible, not reported",
    )
    await show_received(bus, checks, 2, "nav_planner", "scan", receives=False)

    await show_received(bus, checks, 3, "nav_planner", "imu", receives=True)


if __name__ == "__main__":
    raise SystemExit(run_example("13 The deadline is not offered", NODES, scenario, prompt=PROMPT))
