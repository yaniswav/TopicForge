#!/usr/bin/env bash
# Run the OpenSplice demo participant on Linux. Arguments go to the binary:
#   OSPL_HOME=<HDE dir> ./run_ospl.sh --domain 0
# EXPERIMENTAL, see README.md.
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
BIN="$HERE/build/ospl_publisher"

if [ -z "${OSPL_HOME:-}" ]; then
    echo "[run_ospl.sh] OSPL_HOME is not set (see README.md)." >&2
    exit 1
fi
if [ ! -x "$BIN" ]; then
    echo "[run_ospl.sh] $BIN missing: run build.sh first." >&2
    exit 1
fi

# The OpenSplice domain comes from Domain/Id of the XML named by OSPL_URI.
domain=0
prev=""
for arg in "$@"; do
    [ "$prev" = "--domain" ] && domain="$arg"
    prev="$arg"
done

# Pick the config before release.com, which only sets OSPL_URI when empty.
if [ -z "${OSPL_URI:-}" ]; then
    cfg="$OSPL_HOME/etc/config/ospl_sp_ddsi.xml"
    [ -f "$cfg" ] || cfg="$OSPL_HOME/etc/ospl_sp_ddsi.xml"
    if [ ! -f "$cfg" ]; then
        echo "[run_ospl.sh] ospl_sp_ddsi.xml not found under $OSPL_HOME/etc." >&2
        exit 1
    fi
    if [ "$domain" != "0" ]; then
        patched="$HERE/build/ospl_sp_ddsi_domain_$domain.xml"
        sed "s|<Id>0</Id>|<Id>$domain</Id>|" "$cfg" > "$patched"
        cfg="$patched"
    fi
    export OSPL_URI="file://$cfg"
elif [ "$domain" != "0" ]; then
    echo "[run_ospl.sh] OSPL_URI is set, --domain $domain is ignored: edit Domain/Id there." >&2
fi

# release.com is meant to be sourced and is not safe under set -eu.
set +eu
# shellcheck disable=SC1091
. "$OSPL_HOME/release.com" >/dev/null
set -eu

exec "$BIN" "$@"
