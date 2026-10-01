#!/usr/bin/env bash
# Build the C / Dust DDS demo participant on Linux.
#
# Needs: git, cargo (Rust >= 1.87, edition 2024), gcc.
# Output: ./build/dust_c_publisher
#
# The C binding (dust_dds_c) is not published on crates.io (publish = false),
# so the Dust DDS repository is cloned at a pinned commit under ./build.
set -euo pipefail

# Head of s2e-systems/dust-dds main on 2026-10-01 (dust_dds_c 0.17.0).
DUST_COMMIT="74a70d7101cb1562e01c2469db62b24e7ec19a64"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD="$HERE/build"
SRC="$BUILD/dust-dds"
GEN="$BUILD/gen"

mkdir -p "$BUILD" "$GEN"
if [ ! -d "$SRC/.git" ]; then
    git clone https://github.com/s2e-systems/dust-dds.git "$SRC"
fi
git -C "$SRC" fetch --quiet origin "$DUST_COMMIT" 2>/dev/null || true
git -C "$SRC" checkout --quiet "$DUST_COMMIT"

# dust_dds_c builds the static/dynamic libs and writes include/dust_dds.h
# (cbindgen); dust_dds_gen turns Heartbeat.idl into Heartbeat.h.
(cd "$SRC" && cargo build --release -p dust_dds_c -p dust_dds_gen)
"$SRC/target/release/dust_dds_gen" "$HERE/Heartbeat.idl" "$GEN/Heartbeat.h"

gcc -std=c99 -O2 -Wall \
    -I"$SRC/bindings/c/include" -I"$GEN" \
    "$HERE/main.c" \
    "$SRC/target/release/libdust_dds_c.a" \
    -lpthread -ldl -lm \
    -o "$BUILD/dust_c_publisher"

echo "built $BUILD/dust_c_publisher"
