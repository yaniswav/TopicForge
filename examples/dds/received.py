"""The last step of several examples: what does the subscriber actually receive?

The generic role nodes print one `[name] rx <topic>: <count> in <t> s` line per
reader and per second (see `nodes/spec.py`). This reads those lines back from
the harness, so that the example shows the symptom (nothing arrives) next to
TopicForge's diagnosis.
"""

import asyncio
import re
import time

from harness import Bus, Checks, step

# How long to observe. "0 received" only means "broken" once discovery had time
# to finish, so the broken pair is watched for a fixed number of one-second
# reports (discovery takes a second or two) instead of a couple of lines. The
# control pair is watched until it has data, or until a timeout.
QUIET_LINES = 6
WAIT_S = 20.0
MIN_SAMPLES = 5
_COUNT = re.compile(r": (\d+) in [\d.]+ s")


async def rx_counts(bus: Bus, reader: str, topic: str, *, receives: bool) -> list[str]:
    """The `rx` lines of `reader` for `topic`, waiting until the verdict is meaningful."""
    prefix = f"[{reader}] rx {topic}:"
    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        lines = bus.lines(reader, prefix)
        done = _total(lines) >= MIN_SAMPLES if receives else len(lines) >= QUIET_LINES
        if done:
            break
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
    `receives=True` is the compatible control: at least MIN_SAMPLES samples in total.
    """
    step(number, f"What does {reader} actually receive on {topic}?", f"{reader} output")
    lines = await rx_counts(bus, reader, topic, receives=receives)
    for line in lines[-2:]:
        print(f"    {line}")
    checks.expect(len(lines) >= 2, f"{reader} reported on {topic} ({len(lines)} reports)")
    total = _total(lines)
    if receives:
        checks.expect(
            total >= MIN_SAMPLES, f"{topic}: {reader} receives data ({total} samples so far)"
        )
    else:
        checks.expect(total == 0, f"{topic}: {reader} receives nothing ({total} samples so far)")
    return lines
