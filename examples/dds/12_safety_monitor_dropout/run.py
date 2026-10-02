"""Example 12: the safety monitor dropped out.

The safety monitor watches the velocity commands and publishes the
emergency-stop state. It crashes. The robot keeps driving: nothing in the
motor controller notices that the estop topic lost its only writer.

TopicForge shows the crash once the monitor's lease expires, and what it
means for the robot: `estop` has no writer anymore, `cmd_vel` lost a reader.

The lesson an engineer in charge of safety expects to hear: the discovery
lease is a discovery mechanism, not a safety mechanism. Detection takes as
long as the lease the dead program announced (10 s for Cyclone DDS by
default, 100 s for several other vendors). The Liveliness QoS that a real
safety design would rely on is not checked by TopicForge.

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
    by_name,
    run_example,
    show_events,
    show_wiring,
    step,
    wait_for,
)

ESTOP = "estop:Heartbeat:reliable,transient_local"
NODES = (
    Node("nav_planner", "cyclone", writes=("cmd_vel:Twist:reliable",)),
    Node("safety_monitor", "cyclone", reads=("cmd_vel:Twist:reliable",), writes=(ESTOP,)),
    Node("motor_controller", "cyclone", reads=("cmd_vel:Twist:reliable", ESTOP)),
)

LEASE_WAIT_S = 40.0

PROMPT = (
    "Watch DDS domain 0. If a program disappears, tell me which topics lose their "
    "only writer or a reader, and what that means for the robot."
)


async def scenario(tf: TopicForge, bus: Bus, checks: Checks) -> None:
    step(1, "How is the robot wired while everything runs?", "peek_dds_samples (discovery)")
    show_wiring(await tf.wiring())
    monitor = by_name(await tf.participants(), "safety_monitor")
    if not checks.expect(monitor is not None, "safety_monitor is on the bus"):
        return
    assert monitor is not None

    step(2, "The safety monitor crashes. When does the bus know?", "list_participants")
    bus.crash("safety_monitor")

    async def gone() -> bool:
        parts = await tf.participants()
        return any(p["guid"] == monitor["guid"] and p.get("status") == "left" for p in parts)

    elapsed = await wait_for(gone, LEASE_WAIT_S)
    if elapsed is not None:
        print(f"    safety_monitor reported as left after {elapsed:.0f} s")
    checks.expect(
        elapsed is not None, f"safety_monitor reported as left within {LEASE_WAIT_S:.0f} s"
    )

    step(3, "What does the robot lose?", "peek_dds_samples (discovery)")
    wiring = await tf.wiring()
    show_wiring(wiring)
    estop = wiring.get("estop", {"writers": [], "readers": []})
    checks.expect(not estop["writers"], "estop: no writer left")
    checks.expect(
        [who for who, _ in estop["readers"]] == ["motor_controller"],
        "estop: motor_controller still waits for it",
    )
    cmd_readers = sorted(who for who, _ in wiring.get("cmd_vel", {}).get("readers", []))
    checks.expect(
        cmd_readers == ["motor_controller"], "cmd_vel: only motor_controller reads it now"
    )

    step(4, "What happened, in order?", "participant_events")
    events = await tf.events()
    show_events(events)
    checks.expect(
        any(e["guid"] == monitor["guid"] and e["event_type"] == "lost" for e in events),
        "timeline has a 'lost' event for safety_monitor",
    )


if __name__ == "__main__":
    raise SystemExit(
        run_example("12 The safety monitor dropped out", NODES, scenario, prompt=PROMPT)
    )
