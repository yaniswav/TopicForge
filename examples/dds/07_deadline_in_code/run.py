"""Example 07: the deadline, declared and kept, in code.

Two lessons on one policy. Read publisher.py and subscriber.py.

  A. Declaring it. The reader requires a new scan every 200 ms; the writer
     promises nothing. DDS refuses to connect them (DEADLINE, id 4). This is
     a request/offered mismatch, like example 13, and TopicForge sees it.
  B. Keeping it. The writer promises 100 ms, which satisfies the reader's
     200 ms, so they connect. But the writer only publishes at 2 Hz, so the
     promise is broken at runtime: the subscriber counts missed deadlines.
     TopicForge reads declarations, not behavior: it reports nothing here.

    python run.py           # run the example and check what TopicForge reports
    python run.py --hold    # keep the programs running and ask your own MCP client
"""

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

PUB = HERE / "publisher.py"
SUB = HERE / "subscriber.py"

NODES = (
    # A: no promise against a 200 ms demand.
    Node("pub_a", "cyclone", script=PUB, args=("--name", "pub_a", "--topic", "scan_a")),
    Node(
        "sub_a",
        "cyclone",
        script=SUB,
        args=("--name", "sub_a", "--topic", "scan_a", "--deadline-ms", "200"),
    ),
    # B: a 100 ms promise, but only 2 writes per second.
    Node(
        "pub_b",
        "cyclone",
        script=PUB,
        args=("--name", "pub_b", "--topic", "scan_b", "--deadline-ms", "100", "--rate-hz", "2"),
    ),
    Node(
        "sub_b",
        "cyclone",
        script=SUB,
        args=("--name", "sub_b", "--topic", "scan_b", "--deadline-ms", "200"),
    ),
)

PROMPT = (
    "Two scan topics on DDS domain 0, scan_a and scan_b. The planner reads both and "
    "wants a new scan every 200 ms. Is each one really delivering on its deadline?"
)


async def scenario(tf: TopicForge, bus: Bus, checks: Checks) -> None:
    step(1, "A: the writer promises nothing. What does the reader say?", "subscriber output")
    ok = bus.wait_line("sub_a", "incompatible QoS from a writer: DEADLINE (id 4)", 15.0)
    for line in bus.lines("sub_a")[-2:]:
        print(f"    sub_a: {line}")
    checks.expect(ok, "A: the reader reports DEADLINE (id 4)")
    checks.expect(not bus.lines("sub_a", "rx"), "A: nothing received")

    step(
        2,
        "B: the writer promises 100 ms but writes at 2 Hz. What does it say?",
        "subscriber output",
    )
    ok = bus.wait_line("sub_b", "deadline missed total=3", 10.0)
    for line in bus.lines("sub_b")[-3:]:
        print(f"    sub_b: {line}")
    checks.expect(ok, "B: the reader counts at least 3 missed deadlines")
    checks.expect(not bus.lines("sub_b", "incompatible"), "B: the pair is compatible")
    checks.expect(
        bus.wait_line("pub_b", "promise broken", 10.0),
        "B: the writer notices it broke its own promise",
    )

    step(3, "What does TopicForge report?", "detect_qos_mismatches")
    parts = await tf.participants()
    mismatches = await tf.mismatches()
    show_mismatches(mismatches, parts)
    found = mismatch_on(mismatches, "scan_a", "Deadline")
    checks.expect(found is not None, "scan_a: Deadline mismatch reported")
    if found:
        checks.expect(
            who(found, "writer", parts) == "pub_a" and who(found, "reader", parts) == "sub_a",
            "scan_a: pub_a promises nothing, sub_a requires 200 ms",
        )
    checks.expect(
        not any(m["topic"] == "scan_b" for m in mismatches["reports"]),
        "scan_b: no mismatch, the declarations are compatible (the promise is broken at runtime)",
    )


if __name__ == "__main__":
    raise SystemExit(run_example("07 The deadline, in code", NODES, scenario, prompt=PROMPT))
