#!/usr/bin/env bash
# Build the OpenSplice demo participant on Linux with the prebuilt HDE.
# EXPERIMENTAL, see README.md. Output: build/ospl_publisher
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"

if [ -z "${OSPL_HOME:-}" ]; then
    cat >&2 <<'MSG'
[build.sh] OSPL_HOME is not set.
Download the prebuilt HDE from the GitHub release OSPL_V6_9_210323OSS of
ADLINK-IST/opensplice (x86_64.linux-gcc7-glibc2.27-installer.tar), extract it,
then in this shell:
    source <extracted dir>/HDE/x86_64.linux/release.com
and run build.sh again. Do NOT try to compile OpenSplice from source.
MSG
    exit 1
fi
if [ ! -x "$OSPL_HOME/bin/idlpp" ]; then
    echo "[build.sh] $OSPL_HOME/bin/idlpp not found: OSPL_HOME is not an HDE." >&2
    exit 1
fi
if ! command -v gcc >/dev/null 2>&1; then
    echo "[build.sh] gcc not found on PATH (idlpp also needs cpp)." >&2
    exit 1
fi

mkdir -p "$HERE/build"
cd "$HERE/build"

# idlpp writes its output in the current directory. -S is the standalone mode
# (SAC for -l c) and is mandatory together with -l.
"$OSPL_HOME/bin/idlpp" -S -l c -I "$OSPL_HOME/etc/idl" "$HERE/Status.idl"

# Libraries of the HDE needed by the SAC API and the generated type support.
# Only the ones present are linked, the exact set varies between HDE builds.
LIBS=""
for lib in dcpssac ddsuser ddskernel ddsserialization ddsdatabase ddsutil \
           ddsconf ddsconfparser ddsos; do
    if ls "$OSPL_HOME/lib/lib$lib.so"* >/dev/null 2>&1; then
        LIBS="$LIBS -l$lib"
    fi
done
if [ -z "$LIBS" ]; then
    echo "[build.sh] no OpenSplice libraries found in $OSPL_HOME/lib." >&2
    exit 1
fi

# The Wno-error flags keep GCC 14+ from rejecting the 2021 generated code.
# shellcheck disable=SC2086
gcc -O1 -Wall -fcommon \
    -Wno-error=implicit-function-declaration \
    -Wno-error=incompatible-pointer-types \
    -Wno-error=int-conversion \
    -I. \
    -I"$OSPL_HOME/include" \
    -I"$OSPL_HOME/include/sys" \
    -I"$OSPL_HOME/include/dcps/C/SAC" \
    -o ospl_publisher \
    StatusSacDcps.c StatusSplDcps.c "$HERE/src/ospl_publisher.c" \
    -L"$OSPL_HOME/lib" -Wl,--no-as-needed $LIBS -lpthread -lm -ldl -lrt

echo "[build.sh] built $HERE/build/ospl_publisher"
