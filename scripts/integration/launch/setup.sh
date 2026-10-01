#!/usr/bin/env bash
# Prepare the TopicForge multi-vendor demo on Linux (or WSL).
# Creates .venv-demo at the repo root, installs TopicForge with the Cyclone
# binding, and builds the Rust / Dust participant if cargo is available.
# Optional participants (Fast DDS C++, RTI, OpenSplice) have their own
# README under scripts/integration/publishers/.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
VENV="$REPO/.venv-demo"
PY="${PYTHON:-python3}"

echo "[setup] repo: $REPO"
"$PY" -c 'import sys; assert (3, 10) <= sys.version_info[:2] <= (3, 13), "Python 3.10 to 3.13 required (cyclonedds wheels); set PYTHON=python3.12"'
[ -d "$VENV" ] || "$PY" -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -e "$REPO[dds]"
"$VENV/bin/python" -m pip install --quiet "dust-dds==0.16.0"  # Python / Dust participant
echo "[setup] TopicForge + cyclonedds installed in $VENV"

if command -v cargo >/dev/null 2>&1; then
  (cd "$REPO/scripts/integration/publishers/dust_publisher" && cargo build --release --quiet)
  echo "[setup] Rust / Dust participant built"
else
  echo "[setup] cargo not found: install Rust (https://rustup.rs) to build the Dust participant"
fi

"$VENV/bin/python" "$REPO/scripts/integration/driver/demo_client.py" --list
echo "[setup] done. Run: scripts/integration/launch/run_demo.sh"
