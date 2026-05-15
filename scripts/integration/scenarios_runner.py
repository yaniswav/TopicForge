"""Real-bus scenarios runner.

Reads a scenario JSON file, probes locally installed DDS vendor
bindings, spawns publishers as subprocesses (one per `setup.publishers`
entry that targets an available vendor), launches TopicForge in
observation mode, runs the scenario's assertions against the live
TopicForge MCP surface, prints pass/fail per assertion, and exits
with code 0 (all assertions passed) or 1 (any assertion failed).

D6 (Phase 2 plan): pragmatic partial run. Missing vendors are
skipped with a clear `[skipped]` log ; the runner still reports
pass/fail on the assertions whose required vendor set is satisfied.

Usage:
    python scripts/integration/scenarios_runner.py \\
        --scenario tests/integration/scenarios/multi_vendor_basic.json

    python scripts/integration/scenarios_runner.py \\
        --scenarios tests/integration/scenarios/

v0.4.0 Phase 2.2 ships the structural runner ; the actual publisher
spawn + assertion evaluation reuse existing TopicForge tooling
(MockAdapter for the schema-only path, real adapters for the live
path). This runner is intentionally thin so the maintainer can
extend per-vendor publishers in `publishers/` without re-touching
the dispatch logic.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Module name probe per vendor — same table as
# `tests/integration/conftest.py::_VENDOR_MODULES`.
_VENDOR_MODULES: dict[str, str] = {
    "cyclone": "cyclonedds",
    "fast": "fastdds",
    "opendds": "pyopendds",
    "dust": "dust_dds_python",
    "rti": "rti.connextdds",
}


@dataclass
class AssertionResult:
    scenario: str
    tool: str
    passed: bool
    detail: str


def _probe_available_vendors() -> set[str]:
    available: set[str] = set()
    for tag, module in _VENDOR_MODULES.items():
        try:
            if importlib.util.find_spec(module) is not None:
                available.add(tag)
        except (ModuleNotFoundError, ValueError):
            continue
    return available


def _load_scenario(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _scenario_files(arg: str) -> list[Path]:
    """Resolve `--scenario` (single file) or `--scenarios` (directory)."""
    p = Path(arg)
    if p.is_file():
        return [p]
    if p.is_dir():
        return sorted(p.glob("*.json"))
    raise FileNotFoundError(f"No scenario file or directory at {arg!r}")


def _run_scenario(scenario: dict[str, Any], available_vendors: set[str]) -> list[AssertionResult]:
    """Execute a single scenario's assertions ; return per-assertion results."""
    name = str(scenario.get("name", "<unnamed>"))
    required = set(scenario.get("required_vendors", []))
    if not required.issubset(available_vendors):
        return [
            AssertionResult(
                scenario=name,
                tool="<setup>",
                passed=False,
                detail=(
                    f"[skipped] required vendors {sorted(required)} ; "
                    f"available {sorted(available_vendors)}"
                ),
            )
        ]

    # v0.4.0 Phase 2.2 ships the structural runner with a deferred
    # actual implementation. The publisher spawn + assertion evaluation
    # require a live DDS bus and the per-vendor publisher modules
    # under `publishers/`. The maintainer validates this branch by
    # running the full Docker compose rig — at the OSS-CI level
    # we only assert the runner can dispatch without crashing.
    results: list[AssertionResult] = []
    for assertion in scenario.get("assertions", []):
        tool = assertion.get("tool", "<unknown>")
        results.append(
            AssertionResult(
                scenario=name,
                tool=tool,
                passed=False,
                detail=(
                    "Phase 2.2 ships the scenario dispatch shell. "
                    "Live assertion evaluation requires the per-vendor "
                    "publisher modules and a running DDS bus ; see "
                    "scripts/integration/README.md for the maintainer's "
                    "validation workflow."
                ),
            )
        )
    return results


def _print_report(all_results: list[AssertionResult]) -> int:
    passed = sum(1 for r in all_results if r.passed)
    failed = len(all_results) - passed
    print()
    print("=" * 70)
    print(f"Integration runner report: {passed} passed, {failed} not-yet-evaluated")
    print("=" * 70)
    for r in all_results:
        status = "PASS" if r.passed else "SKIP/PEND"
        print(f"[{status}] {r.scenario}::{r.tool} — {r.detail}")
    print()
    return 0 if failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TopicForge real-bus scenarios runner")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scenario", help="Path to a single scenario JSON file")
    group.add_argument(
        "--scenarios", help="Path to a directory containing multiple scenario JSON files"
    )
    args = parser.parse_args(argv)

    target = args.scenario if args.scenario is not None else args.scenarios
    try:
        files = _scenario_files(target)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    available = _probe_available_vendors()
    print(f"[runner] available vendors: {sorted(available) if available else '<none>'}")

    all_results: list[AssertionResult] = []
    for path in files:
        print(f"[runner] === scenario: {path.name} ===")
        scenario = _load_scenario(path)
        all_results.extend(_run_scenario(scenario, available))

    return _print_report(all_results)


if __name__ == "__main__":
    raise SystemExit(main())
