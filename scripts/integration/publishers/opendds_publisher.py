"""Minimal OpenDDS publisher scaffold for the integration rig.

OpenDDS does not currently ship a maintained Python binding on PyPI
(see `docs/projet-file/mcp-02-spec.md` and `docs/dds-interop-matrix.md`).
This scaffold exists so the integration runner has a per-vendor entry
point ; actually exercising it requires the maintainer to install
a working OpenDDS Python binding (e.g. via OpenDDS' own
`opendds_idl` workflow + Cython wrapper) on the host before running.

CLI mirrors the other publishers so the scenarios JSON files stay
vendor-neutral.
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OpenDDS integration publisher (scaffold)")
    parser.add_argument("--topic", required=True)
    parser.add_argument("--rate-hz", type=float, default=10.0)
    parser.add_argument("--duration-s", type=float, default=30.0)
    parser.add_argument("--domain", type=int, default=0)
    parser.add_argument("--gap-at-seq", type=int, default=None)
    _args = parser.parse_args(argv)

    print(
        "[opendds_publisher] OpenDDS Python bindings are not yet maintained "
        "on PyPI (v0.4.0 Phase 2.2). Install pyopendds manually or wait "
        "for upstream release — see docs/projet-file/mcp-02-spec.md "
        "§11 for the v0.5+ roadmap. This scaffold exits without "
        "publishing so the scenarios_runner can report the skip.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
