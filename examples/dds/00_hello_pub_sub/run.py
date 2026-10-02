"""Example 00: hello, publisher and subscriber.

The smallest DDS program pair: `publisher.py` writes a Battery message ten
times a second on the topic `battery`, `subscriber.py` prints what it gets.
Two dashboards run at once: one on `battery`, one with a typo (`batery`).
The typo one connects to nothing, and DDS raises no error: it just never
receives anything. TopicForge sees it from outside: a topic read by one
program and written by nobody.

    python run.py           # run the example and check what TopicForge reports
    python run.py --hold    # keep the programs running and ask your own MCP client
"""

import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from harness import Bus, Checks, Node, TopicForge, run_example, show_wiring, step

NODES = (
    Node(
        "battery_monitor",
        "cyclone",
        script=HERE / "publisher.py",
        args=("--name", "battery_monitor"),
    ),
    Node("dashboard", "cyclone", script=HERE / "subscriber.py", args=("--name", "dashboard")),
    Node(
        "dashboard_typo",
        "cyclone",
        script=HERE / "subscriber.py",
        args=("--name", "dashboard_typo", "--topic", "batery"),
    ),
)

PROMPT = "dashboard_typo shows nothing on DDS domain 0. Which topics have a reader but no writer?"


async def scenario(tf: TopicForge, bus: Bus, checks: Checks) -> None:
    print("  Seen from inside (what the programs print):")
    for _ in range(25):  # up to 5 s to collect 20 samples
        if len(bus.lines("dashboard", "rx ")) >= 20:
            break
        await asyncio.sleep(0.2)
    rx = bus.lines("dashboard", "rx ")
    typo_rx = bus.lines("dashboard_typo", "rx ")
    typo_status = (bus.lines("dashboard_typo", "matched")[-1:] or ["(no status)"])[0]
    print(f"    dashboard:      {len(rx)} rx lines, first: {rx[0] if rx else '(none)'}")
    print(f"    dashboard_typo: {len(typo_rx)} rx lines, status: {typo_status}")
    checks.expect(len(rx) >= 20, "dashboard receives the battery messages")
    checks.expect(
        "matched writers: 0" in bus.lines("dashboard_typo"),
        "dashboard_typo reports 'matched writers: 0'",
    )
    checks.expect(not typo_rx, "dashboard_typo receives nothing")

    step(1, "Who writes and who reads each topic?", "peek_dds_samples")
    table = await tf.wiring()
    show_wiring(table)
    battery = table.get("battery", {"writers": [], "readers": []})
    typo = table.get("batery", {"writers": [], "readers": []})
    checks.expect(
        [n for n, _ in battery["writers"]] == ["battery_monitor"]
        and [n for n, _ in battery["readers"]] == ["dashboard"],
        "battery: written by battery_monitor, read by dashboard",
    )
    checks.expect(
        [n for n, _ in typo["readers"]] == ["dashboard_typo"] and not typo["writers"],
        "batery: read by dashboard_typo, written by nobody",
    )


if __name__ == "__main__":
    raise SystemExit(
        run_example("00 Hello, publisher and subscriber", NODES, scenario, prompt=PROMPT)
    )
