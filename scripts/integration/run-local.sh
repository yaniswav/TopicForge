#!/usr/bin/env bash
# Standalone local runner for the TopicForge integration scenarios.
#
# Probes locally installed DDS Python bindings, runs each scenario
# that has its required vendors available, and skips the rest with
# a clear message. No Docker required.
#
# Usage:
#   ./scripts/integration/run-local.sh
#
# Pre-requisites (any one of these gets you partial coverage):
#   pip install topicforge[dds-cyclone]
#   pip install topicforge[dds-fast]
#   pip install topicforge[dds-opendds]  # not yet on PyPI ; placeholder
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCENARIOS_DIR="${REPO_ROOT}/tests/integration/scenarios"
RUNNER="${REPO_ROOT}/scripts/integration/scenarios_runner.py"

if [[ ! -d "${SCENARIOS_DIR}" ]]; then
    echo "error: scenarios dir not found at ${SCENARIOS_DIR}" >&2
    exit 2
fi
if [[ ! -f "${RUNNER}" ]]; then
    echo "error: runner not found at ${RUNNER}" >&2
    exit 2
fi

echo "[run-local] dispatching scenarios from ${SCENARIOS_DIR}"
python "${RUNNER}" --scenarios "${SCENARIOS_DIR}"
