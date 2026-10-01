"""Python / Dust DDS participant for the TopicForge multi-vendor demo.

Writes `DemoHeartbeat` (`struct Heartbeat { uint32 seq; }`, RELIABLE) at 1 Hz
and nothing else, so that `list_participants` shows one Dust DDS participant
written in Python.

Usage:
    python dust_py_publisher.py [--domain 0] [--rate-hz 1] [--duration-s 0]

`--duration-s 0` (the default) runs until interrupted.

Dust DDS discovers peers over UDP multicast only: a bus where multicast is
blocked will not see this participant.
"""

# No `from __future__ import annotations` here: dust_dds reads the real
# `__annotations__` of the dataclass to build the DDS type.
import argparse
import sys
import time
from dataclasses import dataclass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dust DDS (Python) demo participant")
    parser.add_argument("--domain", type=int, default=0)
    parser.add_argument("--rate-hz", type=float, default=1.0)
    parser.add_argument("--duration-s", type=float, default=0.0, help="0 = run until stopped")
    args = parser.parse_args(argv)

    try:
        import dust_dds
    except ImportError:
        print("error: install the Dust DDS binding first: pip install dust-dds", file=sys.stderr)
        return 1

    # The class name becomes the DDS type name, so it must be `Heartbeat`.
    @dataclass
    class Heartbeat:
        seq: dust_dds.TypeKind.uint32

    try:
        factory = dust_dds.DomainParticipantFactory.get_instance()
        participant = factory.create_participant(domain_id=args.domain)
        topic = participant.create_topic(topic_name="DemoHeartbeat", type_=Heartbeat)
        publisher = participant.create_publisher()
        writer_qos = dust_dds.DataWriterQos(
            reliability=dust_dds.ReliabilityQosPolicy(
                dust_dds.ReliabilityQosPolicyKind.Reliable,
                dust_dds.DurationKind.Finite(dust_dds.Duration(sec=1, nanosec=0)),
            )
        )
        writer = publisher.create_datawriter(topic, qos=writer_qos)
    except Exception as exc:
        print(f"error: Dust DDS setup failed: {exc}", file=sys.stderr)
        return 1

    print(f"[dust_py] domain {args.domain}: writes DemoHeartbeat (RELIABLE)", flush=True)

    period_s = 1.0 / max(args.rate_hz, 0.001)
    deadline = time.monotonic() + args.duration_s if args.duration_s > 0 else None
    seq = 0
    try:
        while deadline is None or time.monotonic() < deadline:
            writer.write(Heartbeat(seq))
            seq += 1
            time.sleep(period_s)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"error: Dust DDS write failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
