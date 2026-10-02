#!/usr/bin/env bash
# Run the TopicForge multi-vendor demo on Linux (or WSL). Run setup.sh first.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
exec "$REPO/.venv-demo/bin/python" "$REPO/scripts/integration/interop_check.py" "$@"
