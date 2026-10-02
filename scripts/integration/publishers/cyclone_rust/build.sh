#!/usr/bin/env bash
# Build with cargo against an installed Cyclone DDS (CYCLONEDDS_HOME), or pass
# --features vendored to build Cyclone DDS from the crate's bundled sources
# (needs cmake). Either way bindgen needs libclang (set LIBCLANG_PATH if it is
# not found automatically).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

case " $* " in
  *" vendored "*|*"=vendored"*) ;;
  *)
    if [ -z "${CYCLONEDDS_HOME:-}" ]; then
      echo "warning: CYCLONEDDS_HOME is not set; relying on system-wide Cyclone DDS headers/libs" >&2
    fi
    ;;
esac

cargo build --release "$@"
