"""Example 06: durability and the late joiner, in code.

The mission is written ONCE, before the navigation planner exists. Three
publishers write it on three topics, then three subscribers join late:

  mission_tl               writer TRANSIENT_LOCAL, reader TRANSIENT_LOCAL: the
                           writer kept the sample, the late reader gets it.
  mission_volatile_writer  writer VOLATILE, reader TRANSIENT_LOCAL: a reader
                           cannot demand more than the writer offers, no match.
  mission_volatile_both    both VOLATILE: they match, but there is nothing to
                           hand over. Not a QoS incompatibility.

This is the only example where start order matters: the publishers must have
written before the subscribers exist. Read publisher.py and subscriber.py.

    python run.py           # run the example and check what TopicForge reports
    python run.py --hold    # keep the programs running and ask your own MCP client
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
    by_name,
    mismatch_on,
    run_example,
    show_mismatches,
    step,
    wait_for,
    who,
)

VARIANTS = (  # (topic, writer durability, reader durability)
    ("mission_tl", "transient_local", "transient_local"),
    ("mission_volatile_writer", "volatile", "transient_local"),
    ("mission_volatile_both", "volatile", "volatile"),
)
PUBLISHERS = tuple(
    Node(
        f"pub_{i}",
        "cyclone",
        script=HERE / "publisher.py",
        args=("--name", f"pub_{i}", "--topic", topic, "--durability", writer),
    )
    for i, (topic, writer, _) in enumerate(VARIANTS)
)


def subscribers(delay: float = 0.0) -> tuple[Node, ...]:
    return tuple(
        Node(
            f"sub_{i}",
            "cyclone",
            script=HERE / "subscriber.py",
            args=(
                *("--name", f"sub_{i}", "--topic", topic, "--durability", reader),
                *("--delay", str(delay)),
            ),
        )
        for i, (topic, _, reader) in enumerate(VARIANTS)
    )


PROMPT = (
    "Three mission topics on DDS domain 0 (mission_tl, mission_volatile_writer, "
    "mission_volatile_both): their subscribers joined late. Which ones can never "
    "get the mission, and why?"
)


async def scenario(tf: TopicForge, bus: Bus, checks: Checks) -> None:
    step(1, "The publishers write first. Only then do the subscribers join.", "(no tool)")
    for node in PUBLISHERS:
        checks.expect(bus.wait_line(node.name, "wrote mission"), f"{node.name} wrote the mission")
    subs = subscribers()
    bus.start(*subs)

    async def subs_seen() -> bool:
        parts = await tf.participants()
        return all(by_name(parts, n.name) for n in subs)

    await wait_for(subs_seen, 30.0, every_s=1.0)
    await asyncio.sleep(3.0)  # endpoint discovery and the history hand-over

    step(2, "Which subscriber got the mission?", "subscriber output")
    for node in subs:
        for line in bus.lines(node.name)[1:]:
            print(f"    {node.name}: {line}")
    checks.expect(
        bus.wait_line("sub_0", "rx mission=42", 10.0), "TL reader, TL writer: mission received"
    )
    checks.expect(
        bus.wait_line("sub_1", "incompatible QoS from a writer: DURABILITY (id 2)", 10.0),
        "TL reader, VOLATILE writer: the reader reports DURABILITY",
    )
    checks.expect(not bus.lines("sub_1", "rx"), "TL reader, VOLATILE writer: nothing received")
    checks.expect(bus.wait_line("sub_2", "matched writers: 1", 10.0), "VOLATILE both: matched")
    checks.expect(not bus.lines("sub_2", "incompatible"), "VOLATILE both: no incompatibility")
    checks.expect(not bus.lines("sub_2", "rx"), "VOLATILE both: nothing received")

    step(3, "What does TopicForge report?", "detect_qos_mismatches")
    parts = await tf.participants()
    mismatches = await tf.mismatches()
    show_mismatches(mismatches, parts)
    found = mismatch_on(mismatches, "mission_volatile_writer", "Durability")
    checks.expect(found is not None, "mission_volatile_writer: Durability mismatch reported")
    if found:
        checks.expect(
            who(found, "writer", parts) == "pub_1" and who(found, "reader", parts) == "sub_1",
            "mission_volatile_writer: VOLATILE writer pub_1, TRANSIENT_LOCAL reader sub_1",
        )
    checks.expect(
        not any(m["topic"] == "mission_tl" for m in mismatches["reports"]),
        "mission_tl: nothing to report, the pair is compatible",
    )
    checks.expect(
        not any(m["topic"] == "mission_volatile_both" for m in mismatches["reports"]),
        "mission_volatile_both: not reported, and it cannot be: no incompatibility exists",
    )


def nodes_for(argv: list[str]) -> tuple[Node, ...]:
    """Check mode starts the subscribers from the scenario; --hold starts them late itself."""
    return (*PUBLISHERS, *subscribers(delay=5.0)) if "--hold" in argv else PUBLISHERS


if __name__ == "__main__":
    raise SystemExit(
        run_example(
            "06 Durability and the late joiner, in code",
            nodes_for(sys.argv),
            scenario,
            prompt=PROMPT,
        )
    )
