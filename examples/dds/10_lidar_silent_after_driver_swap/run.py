"""Example 10: the LIDAR went silent after a driver swap.

The robot's LIDAR driver was replaced by one from another supplier, running
on another DDS implementation. Since then the navigation planner receives no
scan. Two things went wrong at once, as in a real incident:

- the new driver publishes on `lidar/scan`, not `scan`: nobody reads it;
- the old driver's container was never stopped. It still publishes `scan`,
  BEST_EFFORT, which the RELIABLE planner refuses.

TopicForge finds both. The lesson: an empty mismatch report does not mean a
healthy bus. Look at who writes and who reads each topic too.

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
    run_example,
    show_mismatches,
    show_wiring,
    step,
    who,
)
from received import show_received

NODES = (
    Node("nav_planner", "cyclone", reads=("scan:LidarScan:reliable",)),
    Node("lidar_new", "dust", writes=("lidar/scan:LidarScan:best_effort",)),
    Node("lidar_old", "cyclone", writes=("scan:LidarScan:best_effort",)),
)

PROMPT = (
    "Since we swapped the LIDAR driver, nav_planner receives no scan on DDS domain 0. "
    "Find every reason."
)


async def scenario(tf: TopicForge, bus: Bus, checks: Checks) -> None:
    step(1, "Is a QoS mismatch blocking the scan?", "detect_qos_mismatches")
    parts = await tf.participants()
    mismatches = await tf.mismatches()
    show_mismatches(mismatches, parts)
    scan = mismatch_on(mismatches, "scan", "Reliability")
    checks.expect(
        scan is not None
        and who(scan, "writer", parts) == "lidar_old"
        and who(scan, "reader", parts) == "nav_planner",
        "scan: the forgotten old driver is BEST_EFFORT, the planner RELIABLE",
    )
    checks.expect(
        not any(m["topic"] == "lidar/scan" for m in mismatches["reports"]),
        "lidar/scan: no mismatch reported, because nobody reads it",
    )
    checks.expect(
        any("lidar/scan" in h and "no reader" in h for h in mismatches["hints"]),
        "lidar/scan: the scan's hints still flag it as a writer with no reader",
    )
    checks.expect(
        not mismatches["not_matched"],
        "no partition or type split: the pairs that exist are only QoS-checked",
    )

    step(2, "Who writes and who reads each topic?", "peek_dds_samples (discovery)")
    wiring = await tf.wiring()
    show_wiring(wiring)
    new = wiring.get("lidar/scan", {"writers": [], "readers": []})
    checks.expect(len(new["writers"]) == 1, "lidar/scan: the new driver writes it")
    checks.expect(not new["readers"], "lidar/scan: nobody reads it (orphan writer)")
    scan_writers = [who for who, _ in wiring.get("scan", {}).get("writers", [])]
    checks.expect(scan_writers == ["lidar_old"], "scan: only the old driver still writes it")
    await show_received(bus, checks, 3, "nav_planner", "scan", receives=False)


if __name__ == "__main__":
    raise SystemExit(
        run_example("10 The LIDAR went silent after a driver swap", NODES, scenario, prompt=PROMPT)
    )
