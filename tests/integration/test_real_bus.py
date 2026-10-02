"""Real-bus integration test: runs the multi-vendor demo driver.

Deselected by default: `pyproject.toml` sets `-m "not integration"` in
`addopts`, so a plain `pytest` never runs this module. Select it with
`pytest -m integration`. It also skips itself unless the Cyclone binding is
importable and the Rust participant has been built once
(`cargo build --release` in `scripts/integration/publishers/dust_publisher`).
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
DRIVER = REPO / "scripts" / "integration" / "interop_check.py"
EXE = ".exe" if os.name == "nt" else ""
DUST_BIN = (
    REPO
    / "scripts"
    / "integration"
    / "publishers"
    / "dust_publisher"
    / "target"
    / "release"
    / f"dust_publisher{EXE}"
)
DEMO_DOMAIN = "42"
TIMEOUT_S = 180


def test_demo_driver_passes() -> None:
    """The demo driver exits 0 against a real Cyclone + Dust DDS bus."""
    if importlib.util.find_spec("cyclonedds") is None:
        pytest.skip("cyclonedds binding not installed")
    if not DUST_BIN.is_file():
        pytest.skip(f"Rust participant not built: {DUST_BIN}")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    result = subprocess.run(
        [sys.executable, str(DRIVER), "--domain", DEMO_DOMAIN],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        check=False,
        creationflags=flags,
    )
    assert result.returncode == 0, (
        f"demo driver failed.\nstdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )
