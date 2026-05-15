"""Pytest fixtures for the real-bus integration rig.

Auto-discovers scenario JSON files in `tests/integration/scenarios/`
and exposes them via the `scenarios` fixture. Probes available DDS
vendor bindings via `importlib.util.find_spec` so individual
scenarios can skip themselves when their `required_vendors` list is
not satisfied locally.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCENARIOS_DIR = Path(__file__).parent / "scenarios"

# Module name probe for each vendor. Mirrors `_DDS_BACKEND_MODULES`
# in `topicforge.config.settings` but inlined here so the integration
# rig is self-contained and doesn't depend on internal probe order.
_VENDOR_MODULES: dict[str, str] = {
    "cyclone": "cyclonedds",
    "fast": "fastdds",
    "opendds": "pyopendds",
    "dust": "dust_dds_python",
    "rti": "rti.connextdds",
}


@pytest.fixture(scope="session")
def scenarios_dir() -> Path:
    """Path to the scenarios JSON directory."""
    return SCENARIOS_DIR


@pytest.fixture(scope="session")
def all_scenarios() -> list[dict[str, object]]:
    """Load every scenario JSON file into memory once per session."""
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(SCENARIOS_DIR.glob("*.json"))]


@pytest.fixture(scope="session")
def available_vendors() -> set[str]:
    """Set of vendor tags whose Python binding is importable locally.

    Used by `test_real_bus.py` to skip scenarios whose
    `required_vendors` set is not a subset of what's installed.
    """
    available: set[str] = set()
    for tag, module in _VENDOR_MODULES.items():
        try:
            if importlib.util.find_spec(module) is not None:
                available.add(tag)
        except (ModuleNotFoundError, ValueError):
            continue
    return available
