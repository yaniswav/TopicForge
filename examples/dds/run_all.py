"""Run every DDS example in order and summarize.

    python examples/dds/run_all.py              # all examples
    python examples/dds/run_all.py 01 03        # only the examples whose number is given
    python examples/dds/run_all.py --strict     # a skipped example counts as a failure (CI)

Each example gets its own DDS domain (40 + its number) so that a leftover
program from one example can never be mistaken for a participant of the next.
Exit code 0 when at least one example ran and every example that ran passed.
An example that cannot run here (missing binding or license) is skipped,
unless --strict.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from harness import kill_tree  # noqa: E402

SKIPPED = 2  # exit code of an example that cannot run on this machine
TIMEOUT_S = 240


def examples(selected: list[str]) -> list[Path]:
    found = sorted(HERE.glob("[0-9][0-9]_*/run.py"))
    if selected:
        found = [p for p in found if p.parent.name[:2] in selected]
    return found


def run_one(script: Path, domain: int) -> int:
    """Run one example; on timeout, kill it with every program it started."""
    kwargs: dict = {"stdout": sys.stdout, "stderr": sys.stderr}
    if os.name == "nt":
        # Explicit stdout/stderr above: under CREATE_NO_WINDOW, Windows gives
        # the child no output at all unless the handles are passed.
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen([sys.executable, str(script), "--domain", str(domain)], **kwargs)
    try:
        return proc.wait(timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        kill_tree(proc)  # the example, TopicForge and the DDS programs alike
        return -1


def main(argv: list[str]) -> int:
    strict = "--strict" in argv
    selected = [a for a in argv if a != "--strict"]
    scripts = examples(selected)
    if not scripts:
        print(f"no example matches {selected}", file=sys.stderr)
        return 1

    results: list[tuple[str, str]] = []
    for script in scripts:
        name = script.parent.name
        domain = 40 + int(name[:2])
        print(f"\n===== {name} (domain {domain}) =====", flush=True)
        code = run_one(script, domain)
        verdict = {0: "PASS", SKIPPED: "skipped", -1: "TIMEOUT"}.get(code, "FAIL")
        results.append((name, verdict))

    print("\n===== summary =====")
    for name, verdict in results:
        print(f"  {verdict:<8} {name}")
    bad = {"FAIL", "TIMEOUT"} | ({"skipped"} if strict else set())
    if any(v in bad for _, v in results):
        return 1
    if not any(v == "PASS" for _, v in results):
        print("nothing ran: every example was skipped", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
