#!/usr/bin/env bash
# Configure and build with CMake. The Cyclone DDS install prefix comes from
# CYCLONEDDS_HOME and/or CMAKE_PREFIX_PATH (';' or ':' separated).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

prefix=""
for part in "${CYCLONEDDS_HOME:-}" "${CMAKE_PREFIX_PATH:-}"; do
  [ -n "$part" ] && prefix="${prefix:+$prefix;}$part"
done
if [ -z "$prefix" ]; then
  echo "error: set CYCLONEDDS_HOME (or CMAKE_PREFIX_PATH) to the Cyclone DDS install prefix" >&2
  exit 1
fi

cmake -S . -B build -DCMAKE_BUILD_TYPE=Release "-DCMAKE_PREFIX_PATH=$prefix"
cmake --build build --config Release
