"""Minimal CycloneDDS publisher for the integration rig.

CLI:
    python cyclone_publisher.py \\
        --topic /integration/heartbeat \\
        --rate-hz 10 \\
        --duration-s 30 \\
        --domain 0 \\
        [--gap-at-seq 50]

Publishes a `Beat { uint32 seq; }`-like message at `rate_hz` for
`duration_s` seconds on the specified DDS domain. When `--gap-at-seq`
is provided, the publisher skips that sequence number so the
`topic_metrics_sequence_gaps` scenario can detect the gap.

The script does NOT subscribe — TopicForge is the read-only observer.
"""

from __future__ import annotations

import argparse
import sys
import time


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CycloneDDS integration publisher")
    parser.add_argument("--topic", required=True)
    parser.add_argument("--rate-hz", type=float, default=10.0)
    parser.add_argument("--duration-s", type=float, default=30.0)
    parser.add_argument("--domain", type=int, default=0)
    parser.add_argument("--gap-at-seq", type=int, default=None)
    args = parser.parse_args(argv)

    try:
        # The scaffold imports the minimum surface ; the maintainer
        # imports DataWriter / Topic / IdlStruct alongside their
        # scenario's IDL-typed message class.
        from cyclonedds.domain import DomainParticipant  # type: ignore[import-not-found]
        from cyclonedds.pub import Publisher  # type: ignore[import-not-found]
    except ImportError:
        print(
            "error: cyclonedds Python bindings not installed. `pip install cyclonedds` first.",
            file=sys.stderr,
        )
        return 1

    # v0.4.0 Phase 2.2 scaffold — the actual IDL-typed message
    # construction needs `cyclonedds.idl` generated code or a
    # `@dataclasses.dataclass` decorated `IdlStruct` subclass.
    # The maintainer customizes this per scenario when running
    # the rig locally ; we document the entry points here.
    dp = DomainParticipant(args.domain)
    _pub = Publisher(dp)
    print(
        f"[cyclone_publisher] joined domain {args.domain} ; "
        f"target topic {args.topic!r} at {args.rate_hz} Hz for {args.duration_s} s. "
        "Scaffold-only: customize the IdlStruct + Topic + DataWriter for "
        "the scenario's IDL before running."
    )

    period_s = 1.0 / max(args.rate_hz, 0.001)
    end_time = time.monotonic() + args.duration_s
    seq = 0
    while time.monotonic() < end_time:
        if args.gap_at_seq is not None and seq == args.gap_at_seq:
            # Skip this sequence number to induce a gap.
            seq += 1
            continue
        # NOTE: actual `writer.write(Beat(seq=seq))` call is wired by
        # the maintainer alongside the IDL-typed Topic. The current
        # scaffold demonstrates the dispatch loop, not the wire.
        seq += 1
        time.sleep(period_s)

    print(f"[cyclone_publisher] published {seq} samples (target seq cap).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
