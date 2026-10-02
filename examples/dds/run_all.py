"""Run every DDS example in order and summarize.

    python examples/dds/run_all.py              # all examples
    python examples/dds/run_all.py 01 03        # only the examples whose number is given

Each example gets its own DDS domain (40 + its number) so that a leftover
program from one example can never be mistaken for a participant of the next.
Exit code 0 when every example that could run passed; an example that cannot
run on this machine (missing binding or license) is reported as skipped.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKIPPED = 2  # exit code of an example that cannot run on this machine
TIMEOUT_S = 240


def examples(selected: list[str]) -> list[Path]:
    found = sorted(p for p in HERE.glob("[0-9][0-9]_*/run.py"))
    if selected:
        found = [p for p in found if p.parent.name[:2] in selected]
    return found


def main(argv: list[str]) -> int:
    results: list[tuple[str, str]] = []
    for script in examples(argv):
        name = script.parent.name
        domain = 40 + int(name[:2])
        print(f"\n===== {name} (domain {domain}) =====", flush=True)
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            code = subprocess.run(
                [sys.executable, str(script), "--domain", str(domain)],
                # Explicit handles: under CREATE_NO_WINDOW, Windows gives the
                # child no output at all unless they are passed.
                stdout=sys.stdout,
                stderr=sys.stderr,
                timeout=TIMEOUT_S,
                check=False,
                creationflags=flags,
            ).returncode
        except subprocess.TimeoutExpired:
            code = -1
        verdict = {0: "PASS", SKIPPED: "skipped", -1: "TIMEOUT"}.get(code, "FAIL")
        results.append((name, verdict))

    print("\n===== summary =====")
    for name, verdict in results:
        print(f"  {verdict:<8} {name}")
    return 1 if any(v in ("FAIL", "TIMEOUT") for _, v in results) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
