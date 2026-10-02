"""Example 05: reliability, in code.

The programs of example 02 with their source: the LIDAR driver offers
BEST_EFFORT (`publisher.py --best-effort`), the planner asks for RELIABLE
(`subscriber.py --reliable`), and a default subscriber receives fine.
From inside the planner only sees "incompatible QoS: RELIABILITY", with no
writer named; TopicForge names both ends.

    python run.py          # run the example and check what TopicForge reports
    python run.py --hold   # leave the programs up and ask your own MCP client
"""

import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

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

NODES = (
    Node(
        "lidar_driver",
        "cyclone",
        script=HERE / "publisher.py",
        args=("--name", "lidar_driver", "--best-effort"),
    ),
    Node(
        "nav_planner",
        "cyclone",
        script=HERE / "subscriber.py",
        args=("--name", "nav_planner", "--reliable"),
    ),
    Node(
        "scan_logger",
        "cyclone",
        script=HERE / "subscriber.py",
        args=("--name", "scan_logger", "--best-effort"),
    ),
)

PROMPT = "nav_planner never receives the scan topic on DDS domain 0. Why?"

INCOMPATIBLE = "incompatible QoS from a writer: RELIABILITY (id 11)"


async def scenario(tf: TopicForge, bus: Bus, checks: Checks) -> None:
    print("  Seen from inside (what the programs print):")
    for _ in range(75):  # up to 15 s
        if bus.lines("nav_planner", "incompatible") and len(bus.lines("scan_logger", "rx ")) >= 20:
            break
        await asyncio.sleep(0.2)
    planner = bus.lines("nav_planner")
    logger_rx = bus.lines("scan_logger", "rx ")
    print(f"    nav_planner:  {INCOMPATIBLE!r}, {len(bus.lines('nav_planner', 'rx '))} rx lines")
    print(f"    scan_logger:  {len(logger_rx)} rx lines")
    checks.expect(INCOMPATIBLE in planner, "nav_planner reports the RELIABILITY incompatibility")
    checks.expect(not bus.lines("nav_planner", "rx "), "nav_planner (RELIABLE) receives nothing")
    checks.expect(len(logger_rx) >= 20, "scan_logger (BEST_EFFORT) receives the scan")

    step(1, "Which writer and which reader are incompatible?", "detect_qos_mismatches")
    parts = await tf.participants()
    mismatches = await tf.mismatches()
    show_mismatches(mismatches, parts)
    scan = mismatch_on(mismatches, "scan", "Reliability")
    checks.expect(scan is not None, "scan: Reliability mismatch reported")
    if scan:
        checks.expect(
            who(scan, "writer", parts) == "lidar_driver"
            and who(scan, "reader", parts) == "nav_planner",
            "writer lidar_driver, reader nav_planner (DDS alone only gave the policy id)",
        )
    checks.expect(
        not any(who(m, "reader", parts) == "scan_logger" for m in mismatches["reports"]),
        "scan_logger is not reported: BEST_EFFORT reader, compatible",
    )


if __name__ == "__main__":
    raise SystemExit(run_example("05 Reliability, in code", NODES, scenario, prompt=PROMPT))
