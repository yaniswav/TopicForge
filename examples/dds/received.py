"""The last step of several examples: what does the subscriber actually receive?

The generic role nodes print one `[name] rx <topic>: <count> in 1 s` line per
reader and per second (see `nodes/spec.py`). This reads those lines back from
the harness, so that the example shows the symptom (nothing arrives) next to
TopicForge's diagnosis.
"""

import asyncio
import re
import time

from harness import Bus, Checks, step

MIN_LINES = 2  # fewer than this and "every count is 0" would be vacuous
WAIT_S = 15.0
_COUNT = re.compile(r": (\d+) in 1 s")


async def rx_counts(bus: Bus, reader: str, topic: str, min_lines: int = MIN_LINES) -> list[str]:
    """The `rx` lines of `reader` for `topic`, waiting until there are enough."""
    prefix = f"[{reader}] rx {topic}:"
    deadline = time.monotonic() + WAIT_S
    while len(bus.lines(reader, prefix)) < min_lines and time.monotonic() < deadline:
        await asyncio.sleep(0.5)
    return bus.lines(reader, prefix)


def _total(lines: list[str]) -> int:
    return sum(int(m.group(1)) for line in lines if (m := _COUNT.search(line)))


async def show_received(
    bus: Bus,
    checks: Checks,
    number: int,
    reader: str,
    topic: str,
    *,
    receives: bool,
) -> list[str]:
    """Step `number`: print the reader's last rx lines for `topic` and check them.

    `receives=False` is the broken pair: every reported count must be 0.
    `receives=True` is the compatible control: at least 5 samples in total.
    """
    step(number, f"What does {reader} actually receive on {topic}?", f"{reader} output")
    lines = await rx_counts(bus, reader, topic)
    for line in lines[-2:]:
        print(f"    {line}")
    checks.expect(len(lines) >= MIN_LINES, f"{reader} reported on {topic} at least twice")
    total = _total(lines)
    if receives:
        checks.expect(total >= 5, f"{topic}: {reader} receives data ({total} samples so far)")
    else:
        checks.expect(total == 0, f"{topic}: {reader} receives nothing ({total} samples so far)")
    return lines
