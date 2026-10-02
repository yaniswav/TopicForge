"""Example 13: the deadline is not offered.

The navigation planner requires a new scan at least every 100 ms: it declares
a Deadline of 100 ms on its reader. The new LIDAR driver declares no deadline
at all, which means "no promise" (an infinite deadline, the default in most
DDS and ROS 2 setups). A writer that promises nothing cannot satisfy a reader
that requires a promise, so DDS does not connect them: the planner receives
nothing, not even late scans.

The IMU pair is the control: the IMU driver promises 10 ms, the planner asks
for 100 ms. Offering more than asked is fine; TopicForge must not report it.

TopicForge sees the deadline each side declares. It cannot see whether a
running writer actually meets its deadline.

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
    mismatch_on,
    owner,
    run_example,
    show_mismatches,
    step,
)

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
            owner(scan.get("writer_guid"), parts) == "lidar_driver"
            and owner(scan.get("reader_guid"), parts) == "nav_planner",
            "scan: lidar_driver offers no deadline, nav_planner requires 100 ms",
        )
    checks.expect(
        not any(m["topic"] == "imu" for m in mismatches),
        "imu: 10 ms offered for 100 ms requested is compatible, not reported",
    )


if __name__ == "__main__":
    raise SystemExit(run_example("13 The deadline is not offered", NODES, scenario, prompt=PROMPT))
