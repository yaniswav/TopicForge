"""Python / Cyclone DDS participant for the TopicForge multi-vendor demo.

Publishes `DemoOdom` (RELIABLE) and subscribes to `DemoLidarScan` with a
RELIABLE reader. The Rust / Dust node writes `DemoLidarScan` BEST_EFFORT, so
the pair is incompatible on purpose: a RELIABLE reader never matches a
BEST_EFFORT writer, and TopicForge's `detect_qos_mismatches` must say so.

Usage:
    python cyclone_publisher.py [--domain 0] [--rate-hz 10] [--duration-s 0]

`--duration-s 0` (the default) runs until interrupted.

TopicForge is the read-only observer; this node is an ordinary application.
"""

# No `from __future__ import annotations` here: cyclonedds resolves the IDL
# field types from real annotations, and string annotations on classes defined
# inside main() cannot be resolved ("Type uint32 ... cannot be resolved").
import argparse
import sys
import time
from dataclasses import dataclass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cyclone DDS demo participant")
    parser.add_argument("--domain", type=int, default=0)
    parser.add_argument("--rate-hz", type=float, default=10.0)
    parser.add_argument("--duration-s", type=float, default=0.0, help="0 = run until stopped")
    args = parser.parse_args(argv)

    try:
        from cyclonedds.core import Policy, Qos
        from cyclonedds.domain import DomainParticipant
        from cyclonedds.idl import IdlStruct
        from cyclonedds.idl.types import float32, float64, uint32
        from cyclonedds.pub import DataWriter
        from cyclonedds.sub import DataReader
        from cyclonedds.topic import Topic
        from cyclonedds.util import duration
    except ImportError:
        print("error: install the Cyclone binding first: pip install cyclonedds", file=sys.stderr)
        return 1

    @dataclass
    class Odom(IdlStruct, typename="Odom"):
        seq: uint32
        x: float64
        y: float64

    @dataclass
    class LidarScan(IdlStruct, typename="LidarScan"):
        seq: uint32
        range_m: float32

    reliable = Qos(Policy.Reliability.Reliable(duration(seconds=1)))

    dp = DomainParticipant(args.domain)
    odom_writer = DataWriter(dp, Topic(dp, "DemoOdom", Odom), qos=reliable)
    # Deliberately RELIABLE against the Rust BEST_EFFORT writer.
    _scan_reader = DataReader(dp, Topic(dp, "DemoLidarScan", LidarScan), qos=reliable)

    print(
        f"[cyclone_publisher] domain {args.domain}: writes DemoOdom (RELIABLE), "
        "reads DemoLidarScan (RELIABLE, incompatible with the BEST_EFFORT Rust writer)",
        flush=True,
    )

    period_s = 1.0 / max(args.rate_hz, 0.001)
    deadline = time.monotonic() + args.duration_s if args.duration_s > 0 else None
    seq = 0
    try:
        while deadline is None or time.monotonic() < deadline:
            odom_writer.write(Odom(seq=seq, x=seq * 0.05, y=0.0))
            seq += 1
            time.sleep(period_s)
    except KeyboardInterrupt:
        pass
    print(f"[cyclone_publisher] stopped after {seq} samples", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
