"""Example 08: a crash, seen from inside and from outside.

A publisher is killed (no goodbye on the bus). Two ways to notice:

  inside   the subscriber's reader is told when a writer's liveliness lease
           runs out. The lease is the writer's own QoS: here 1 s.
  outside  TopicForge sees the participant discovery lease run out. For
           Cyclone DDS that is 10 s by default, and TopicForge only notices
           when it is asked.

The broken variant: a writer left with the default liveliness (an infinite
lease). Its subscriber is never told anything. Only the participant lease
reveals the crash. Read publisher.py and subscriber.py.

    python run.py           # run the example and check what TopicForge reports
    python run.py --hold    # keep the programs running and ask your own MCP client
"""

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from harness import (
    Bus,
    Checks,
    Node,
    TopicForge,
    by_name,
    run_example,
    show_events,
    step,
    wait_for,
)

PUB = HERE / "publisher.py"
SUB = HERE / "subscriber.py"

NODES = (
    Node(
        "pub_leased",
        "cyclone",
        script=PUB,
        args=("--name", "pub_leased", "--topic", "hb_leased", "--lease", "1000"),
    ),
    Node(
        "sub_leased",
        "cyclone",
        script=SUB,
        args=("--name", "sub_leased", "--topic", "hb_leased"),
    ),
    Node(
        "pub_default",
        "cyclone",
        script=PUB,
        args=("--name", "pub_default", "--topic", "hb_default"),
    ),
    Node(
        "sub_default",
        "cyclone",
        script=SUB,
        args=("--name", "sub_default", "--topic", "hb_default"),
    ),
)

LEASE_WAIT_S = 40.0  # Cyclone's 10 s participant lease, plus discovery and polling margin
INSIDE_S = 3.0  # a 1 s writer lease must be noticed well inside this

PROMPT = (
    "Two heartbeat publishers, pub_leased and pub_default, are about to be stopped on DDS "
    "domain 0. Tell me which participants left and when."
)


async def scenario(tf: TopicForge, bus: Bus, checks: Checks) -> None:
    step(1, "Everything is alive. What do the subscribers receive?", "subscriber output")
    for name in ("sub_leased", "sub_default"):
        checks.expect(bus.wait_line(name, "rx seq=", 10.0), f"{name} receives heartbeats")
    parts = await tf.participants()
    guids = {}
    for name in ("pub_leased", "pub_default"):
        found = by_name(parts, name)
        checks.expect(
            found is not None and found.get("status") == "active", f"TopicForge: {name} active"
        )
        guids[name] = found["guid"] if found else None

    step(2, "Both publishers crash. What does each subscriber notice?", "subscriber output")
    crashed = time.monotonic()
    bus.crash("pub_leased")
    bus.crash("pub_default")
    noticed = bus.wait_line("sub_leased", "writer lost liveliness", INSIDE_S)
    print(f"    sub_leased: {(bus.lines('sub_leased', 'writer lost') or ['(nothing)'])[0]}")
    checks.expect(noticed, f"hb_leased: the reader is told within {INSIDE_S:.0f} s of the crash")

    step(3, "How long until TopicForge sees it?", "list_participants")

    async def both_left() -> bool:
        now = await tf.participants()
        return all(
            any(p["guid"] == guid and p.get("status") == "left" for p in now)
            for guid in guids.values()
        )

    elapsed = await wait_for(both_left, LEASE_WAIT_S)
    if elapsed is not None:
        print(
            f"    both publishers reported as left {time.monotonic() - crashed:.0f} s after the crash"
        )
    checks.expect(
        elapsed is not None, f"both publishers reported as left within {LEASE_WAIT_S:.0f} s"
    )

    step(4, "What does the timeline say?", "participant_events")
    events = await tf.events()
    show_events([e for e in events if e["event_type"] == "lost"])
    for name, guid in guids.items():
        checks.expect(
            any(e["guid"] == guid and e["event_type"] == "lost" for e in events),
            f"timeline has a 'lost' event for {name}",
        )

    step(5, "And the subscriber of the writer with the default lease?", "subscriber output")
    print(f"    sub_default: {(bus.lines('sub_default')[-1:] or ['(nothing)'])[0]}")
    checks.expect(
        not bus.lines("sub_default", "writer lost"),
        "hb_default: the reader was never told the writer is gone",
    )


if __name__ == "__main__":
    raise SystemExit(run_example("08 A crash, seen from inside", NODES, scenario, prompt=PROMPT))
