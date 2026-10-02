"""Python / Fast DDS participant for the TopicForge multi-vendor demo.

Writes `DemoHeartbeat` (`struct Heartbeat { uint32 seq; }`, RELIABLE) at 1 Hz
and nothing else, so that `list_participants` shows one Fast DDS participant
written in Python.

Usage:
    python fast_py_publisher.py [--domain 0] [--rate-hz 1] [--duration-s 0]

`--duration-s 0` (the default) runs until interrupted.

Needs two things that `pip` cannot provide: the `fastdds` module of
eProsima's Fast-DDS-python, and the `Heartbeat` module generated from
`Heartbeat.idl` by `fastddsgen -python`. `build.sh` builds the second one and
`run.sh` sets the environment for both. API calls follow
fastdds_python_examples/HelloWorldExample/HelloWorldExample.py at tag v2.6.2
of https://github.com/eProsima/Fast-DDS-python.
"""

import argparse
import sys
import time


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fast DDS (Python) demo participant")
    parser.add_argument("--domain", type=int, default=0)
    parser.add_argument("--rate-hz", type=float, default=1.0)
    parser.add_argument("--duration-s", type=float, default=0.0, help="0 = run until stopped")
    args = parser.parse_args(argv)

    try:
        import fastdds
        import Heartbeat
    except ImportError as exc:
        print(
            f"error: {exc}. Build the binding and the Heartbeat type with build.sh, "
            "then start this script through run.sh (the `fastdds` module is not on PyPI)",
            file=sys.stderr,
        )
        return 1

    factory = fastdds.DomainParticipantFactory.get_instance()
    participant_qos = fastdds.DomainParticipantQos()
    factory.get_default_participant_qos(participant_qos)
    participant = factory.create_participant(args.domain, participant_qos)
    if participant is None:
        print(f"error: create_participant failed on domain {args.domain}", file=sys.stderr)
        return 1

    # The generated PubSubType is named "Heartbeat", the IDL struct name.
    topic_data_type = Heartbeat.HeartbeatPubSubType()
    type_support = fastdds.TypeSupport(topic_data_type)
    participant.register_type(type_support)

    topic_qos = fastdds.TopicQos()
    participant.get_default_topic_qos(topic_qos)
    topic = participant.create_topic("DemoHeartbeat", topic_data_type.get_name(), topic_qos)
    if topic is None:
        print("error: create_topic DemoHeartbeat failed", file=sys.stderr)
        return 1

    publisher_qos = fastdds.PublisherQos()
    participant.get_default_publisher_qos(publisher_qos)
    publisher = participant.create_publisher(publisher_qos)
    if publisher is None:
        print("error: create_publisher failed", file=sys.stderr)
        return 1

    writer_qos = fastdds.DataWriterQos()
    publisher.get_default_datawriter_qos(writer_qos)
    writer_qos.reliability().kind = fastdds.RELIABLE_RELIABILITY_QOS
    writer = publisher.create_datawriter(topic, writer_qos)
    if writer is None:
        print("error: create_datawriter failed", file=sys.stderr)
        return 1

    print(f"[fast_py] domain {args.domain}: writes DemoHeartbeat (RELIABLE)", flush=True)

    period_s = 1.0 / max(args.rate_hz, 0.001)
    deadline = time.monotonic() + args.duration_s if args.duration_s > 0 else None
    seq = 0
    try:
        while deadline is None or time.monotonic() < deadline:
            sample = Heartbeat.Heartbeat()
            sample.seq(seq)
            if writer.write(sample) != fastdds.RETCODE_OK:
                print("error: Fast DDS write failed", file=sys.stderr)
                return 1
            seq += 1
            time.sleep(period_s)
    except KeyboardInterrupt:
        pass
    finally:
        participant.delete_contained_entities()
        factory.delete_participant(participant)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
