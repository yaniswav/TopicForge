#!/usr/bin/env bash
# Build the Rust / RTI Connector demo participant. Needs Rust 1.85+ and network
# access: cargo fetches the rtiddsconnector git dependency, and its build.rs
# downloads the native Connector libraries (connectorlibs-1.5.0.zip) from
# github.com/rticommunity/rticonnextdds-connector. No RTI Connext install is
# needed to build. Result: target/release/rti_rust, with the native libraries
# copied next to it.
#
# To build offline from a local copy of the native libraries, set
# RTI_CONNECTOR_DIR to a directory containing lib/<arch>/ (see the crate's
# docs/guide/getting_started.md).
set -euo pipefail
cd "$(dirname "$0")"

cargo build --release

# build.rs extracts the native libraries to <OUT_DIR>/lib/<arch>; copy them next
# to the executable (documented as one way to make them discoverable).
libdir="$(ls -dt target/release/build/rtiddsconnector-*/out/lib/* 2>/dev/null | head -n 1 || true)"
if [ -z "$libdir" ]; then
    echo "error: native Connector libraries not found under target/release/build" >&2
    exit 1
fi
find "$libdir" -maxdepth 1 -type f -exec cp -f {} target/release/ \;

echo "built: $(pwd)/target/release/rti_rust"
echo "run with: LD_LIBRARY_PATH=$(pwd)/target/release RTI_LICENSE_FILE=... ./target/release/rti_rust --domain 0"
