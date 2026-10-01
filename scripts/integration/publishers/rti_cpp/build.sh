#!/usr/bin/env bash
# Build the C++ (Modern C++ API) / RTI Connext demo participant. Needs RTI
# Connext Professional 7.x (host and target bundles), CMake >= 3.11 and a C++11
# compiler. Never installs anything. Result: build/rti_cpp
#
#   export NDDSHOME=$HOME/rti_connext_dds-7.3.0
#   export CONNEXTDDS_ARCH=x64Linux4gcc7.3.0   # only if several archs are installed
#   ./build.sh
set -euo pipefail
cd "$(dirname "$0")"

if [ -z "${NDDSHOME:-}" ]; then
    echo "error: NDDSHOME is not set (RTI Connext Professional 7.x install directory)" >&2
    exit 1
fi

args=(-S . -B build -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=ON)
if [ -n "${CONNEXTDDS_ARCH:-}" ]; then
    args+=("-DCONNEXTDDS_ARCH=${CONNEXTDDS_ARCH}")
fi

cmake "${args[@]}"
cmake --build build --config Release
echo "built: $(pwd)/build/rti_cpp"
