"""Minimal Fast DDS publisher for the integration rig.

Symmetric to `cyclone_publisher.py` — same CLI surface, different
binding. Fast DDS 2.6.x Python publisher dispatch loop is sketched
here ; the IDL-typed write wiring is the maintainer's customization
point per scenario.
"""

from __future__ import annotations

import argparse
import sys
import time


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fast DDS integration publisher")
    parser.add_argument("--topic", required=True)
    parser.add_argument("--rate-hz", type=float, default=10.0)
    parser.add_argument("--duration-s", type=float, default=30.0)
    parser.add_argument("--domain", type=int, default=0)
    parser.add_argument("--gap-at-seq", type=int, default=None)
    args = parser.parse_args(argv)

    try:
        import fastdds  # type: ignore[import-not-found]
    except ImportError:
        print(
            "error: fastdds Python bindings not installed. `pip install fastdds` first.",
            file=sys.stderr,
        )
        return 1

    factory = fastdds.DomainParticipantFactory.get_instance()
    qos = fastdds.DomainParticipantQos()
    _participant = factory.create_participant(args.domain, qos)
    print(
        f"[fast_publisher] joined domain {args.domain} ; "
        f"target topic {args.topic!r} at {args.rate_hz} Hz for {args.duration_s} s. "
        "Scaffold-only: customize the TypeSupport + Topic + DataWriter for "
        "the scenario's IDL before running."
    )

    period_s = 1.0 / max(args.rate_hz, 0.001)
    end_time = time.monotonic() + args.duration_s
    seq = 0
    while time.monotonic() < end_time:
        if args.gap_at_seq is not None and seq == args.gap_at_seq:
            seq += 1
            continue
        seq += 1
        time.sleep(period_s)

    print(f"[fast_publisher] published {seq} samples (target seq cap).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
