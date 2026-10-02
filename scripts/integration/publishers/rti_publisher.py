"""Python / RTI Connext DDS participant for the TopicForge multi-vendor demo.

Subscribes to `DemoImu` with a RELIABLE reader and publishes its own
`DemoHeartbeat` topic (RELIABLE, 1 Hz). The C++ / Fast DDS node writes
`DemoImu` BEST_EFFORT, so the pair is incompatible on purpose: a RELIABLE
reader never matches a BEST_EFFORT writer, and TopicForge's
`detect_qos_mismatches` must say so. This node writes nothing on `DemoImu`;
the heartbeat only gives the participant a publication visible to TopicForge
even when the Fast DDS node is not running.

Usage:
    python rti_publisher.py [--domain 0] [--heartbeat-hz 1] [--duration-s 0]

`--duration-s 0` (the default) runs until interrupted.

Requirements: `pip install rti.connext` (Connext 7.x Python API) and a license
file, normally pointed to by the RTI_LICENSE_FILE environment variable. See
`RTI.md` next to this file for the license terms; never commit the license.

API sources used (RTI Connext Python API, rticommunity/rticonnextdds-examples):
  - examples/connext_dds/asynchronous_publication/py/async_publisher.py:
    DomainParticipant(domain_id), Topic(participant, name, type),
    participant.default_datawriter_qos,
    writer_qos.reliability = dds.Reliability.reliable(dds.Duration.from_seconds(n)),
    DataWriter(participant.implicit_publisher, topic, qos), writer.write(sample).
  - examples/connext_dds/asynchronous_publication/py/async_subscriber.py:
    participant.default_datareader_qos,
    reader_qos.reliability.kind = dds.ReliabilityKind.RELIABLE,
    DataReader(participant.implicit_subscriber, topic, qos).
  - examples/connext_dds/using_qos_profiles/py/profiles.py:
    `@idl.struct` classes with `idl.int32`-style annotated fields and defaults.

RTPS vendor id of RTI is 01.01. RTI generally does not prefix its participant
GUID with its vendor id, so TopicForge may display this participant's vendor as
`unknown`. This is a known limit, documented in the CHANGELOG.

TopicForge is the read-only observer; this node is an ordinary application.
"""

# No `from __future__ import annotations` here: `@idl.struct` resolves the IDL
# field types from real annotations, and the classes are defined inside main()
# after the RTI import, so that the module imports cleanly without RTI.
import argparse
import os
import sys
import time


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RTI Connext DDS demo participant")
    parser.add_argument("--domain", type=int, default=0)
    parser.add_argument("--heartbeat-hz", type=float, default=1.0)
    parser.add_argument("--duration-s", type=float, default=0.0, help="0 = run until stopped")
    args = parser.parse_args(argv)

    try:
        import rti.connextdds as dds
        import rti.idl as idl
    except (ImportError, OSError) as exc:
        print(
            f"error: cannot import rti.connextdds ({exc}). Install the RTI Connext Python "
            "API with: pip install rti.connext, and provide a license file "
            "(set RTI_LICENSE_FILE, see RTI.md).",
            file=sys.stderr,
        )
        return 1

    if not os.environ.get("RTI_LICENSE_FILE"):
        print(
            "warning: RTI_LICENSE_FILE is not set. Trying anyway; Connext will fail "
            "to create entities if no license is found (see RTI.md).",
            file=sys.stderr,
        )

    @idl.struct
    class Imu:
        seq: idl.uint32 = 0
        yaw_rad: idl.float64 = 0.0

    @idl.struct
    class Heartbeat:
        seq: idl.uint32 = 0

    try:
        dp = dds.DomainParticipant(args.domain)

        reader_qos = dp.default_datareader_qos
        # Deliberately RELIABLE against the Fast DDS BEST_EFFORT writer.
        reader_qos.reliability.kind = dds.ReliabilityKind.RELIABLE
        _imu_reader = dds.DataReader(
            dp.implicit_subscriber, dds.Topic(dp, "DemoImu", Imu), reader_qos
        )

        writer_qos = dp.default_datawriter_qos
        writer_qos.reliability = dds.Reliability.reliable(dds.Duration.from_seconds(1))
        heartbeat_writer = dds.DataWriter(
            dp.implicit_publisher, dds.Topic(dp, "DemoHeartbeat", Heartbeat), writer_qos
        )
    except Exception as exc:  # RTI raises dds.Error and others; keep the message readable
        print(
            f"error: could not create the RTI Connext entities ({type(exc).__name__}: {exc}). "
            "Check the license file (RTI_LICENSE_FILE) and its entity limits.",
            file=sys.stderr,
        )
        return 1

    print(
        f"[rti_publisher] domain {args.domain}: writes DemoHeartbeat (RELIABLE), "
        "reads DemoImu (RELIABLE, incompatible with the BEST_EFFORT Fast DDS writer)",
        flush=True,
    )

    period_s = 1.0 / max(args.heartbeat_hz, 0.001)
    deadline = time.monotonic() + args.duration_s if args.duration_s > 0 else None
    seq = 0
    try:
        while deadline is None or time.monotonic() < deadline:
            heartbeat_writer.write(Heartbeat(seq=seq))
            seq += 1
            time.sleep(period_s)
    except KeyboardInterrupt:
        pass
    print(f"[rti_publisher] stopped after {seq} samples", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
