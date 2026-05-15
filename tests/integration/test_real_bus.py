"""Real-bus integration tests — gated by `@pytest.mark.integration`.

Parametrized over the scenario JSON files. The default
`pytest` invocation **does not** run these — the
`pyproject.toml` `addopts = "-ra --strict-markers"` plus the
explicit `-m integration` selection are required. CI exercises them
only when the `integration-tests` PR label is set
(`.github/workflows/integration.yml`).

These tests defer the heavy lifting (spawning publishers, polling
TopicForge, asserting outputs) to
`scripts/integration/scenarios_runner.py` so the same code path
works from a developer laptop via `run-local.{ps1,sh}` and from
CI via Docker compose.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

# Every test in this module carries the `integration` mark.
pytestmark = pytest.mark.integration


def _scenario_param_id(scenario: dict[str, Any]) -> str:
    name = scenario.get("name")
    return str(name) if name else "unnamed"


def test_runner_script_exists() -> None:
    """Sanity: the scenarios runner is on disk where we expect it."""
    runner = Path(__file__).parent.parent.parent / "scripts" / "integration" / "scenarios_runner.py"
    assert runner.is_file(), (
        f"scenarios runner missing at {runner} — see scripts/integration/README.md"
    )


def test_each_scenario_can_be_dispatched_to_runner(
    all_scenarios: list[dict[str, Any]],
    available_vendors: set[str],
    scenarios_dir: Path,
) -> None:
    """Dispatch each scenario through the runner script.

    The runner is responsible for skipping scenarios whose
    `required_vendors` are not in `available_vendors`. We capture
    its exit code and `stdout` for the parametrized assertion.

    This is the **only** integration test that actually shells out
    to a publisher subprocess ; CI invokes it under `pytest -m
    integration` after docker-compose has spun up the publisher
    images.
    """
    runner_path = (
        Path(__file__).parent.parent.parent / "scripts" / "integration" / "scenarios_runner.py"
    )
    for scenario in all_scenarios:
        scenario_file = scenarios_dir / f"{scenario['name']}.json"
        required = set(scenario["required_vendors"])
        if not required.issubset(available_vendors):
            pytest.skip(
                f"Scenario {scenario['name']!r} requires {sorted(required)} ; "
                f"locally available: {sorted(available_vendors)}"
            )
            continue
        result = subprocess.run(
            [sys.executable, str(runner_path), "--scenario", str(scenario_file)],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert result.returncode == 0, (
            f"Scenario {scenario['name']!r} failed.\n"
            f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
        )
