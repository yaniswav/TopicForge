#!/usr/bin/env bash
# Start the Fast DDS Python participant with the environment build.sh recorded.
# Usage: run.sh [--domain N]   (extra arguments go to fast_py_publisher.py)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ ! -f "$HERE/build/env.sh" ]; then
    echo "error: run build.sh first (it needs FASTDDS_HOME, see README.md)" >&2
    exit 1
fi
# shellcheck disable=SC1091
. "$HERE/build/env.sh"
exec "$PYTHON" "$HERE/fast_py_publisher.py" "$@"
