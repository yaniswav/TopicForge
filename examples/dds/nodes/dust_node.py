"""Dust DDS (Python binding) role node: one participant, N writers, M readers.

    python dust_node.py --domain 0 --name nav_planner \
        --write cmd_vel:Twist:reliable --read odom:Odom

Writes deterministic samples at --rate-hz until stopped. TopicForge is the
read-only observer; this node is an ordinary application.

Limits of the Dust DDS Python binding (0.16, checked against a real bus):
  - It cannot serialize float32, float64 or string fields: `writer.write`
    panics ("not yet implemented"). Writers of such types (LidarScan, Odom,
    Imu, Twist, Status) are still created, so their topic, type and QoS are
    announced on the bus, but they stay silent and a warning goes to stderr.
    Heartbeat (uint32 only) publishes normally. Readers work for every type.
  - It has no EntityName policy, so the participant name is carried in the
    participant's `user_data` as UTF-8 bytes. user_data is announced in
    DCPSParticipant, but a generic observer will not read it as a name.
  - Partition, liveliness and ownership kind are supported (partition goes on
    the Publisher / Subscriber). `OwnershipStrengthQosPolicy` has no
    constructor in 0.16, so `strength=` is ignored with one warning on stderr.
  - `participant.assert_liveliness()` panics ("not yet implemented") in 0.16,
    so manual liveliness relies on writing alone to assert. That is enough for
    MANUAL_BY_TOPIC, and in practice for MANUAL_BY_PARTICIPANT while any
    writer writes. `--stop-asserting-after S` stops writing after S seconds
    while the node stays up.
"""

# No `from __future__ import annotations` here: dust_dds reads the real
# `__annotations__` of the dataclass to build the DDS type.
import signal
import sys
import time
from dataclasses import make_dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import spec

VENDOR = "dust"


def _build_type(dust_dds: Any, type_name: str) -> type:
    """Create the dataclass for `type_name`; its class name is the DDS type name."""
    kinds = {
        "uint32": dust_dds.TypeKind.uint32,
        "float32": dust_dds.TypeKind.float32,
        "float64": dust_dds.TypeKind.float64,
        # No string TypeKind exists; `str` is accepted by create_topic but cannot be written.
        "string": str,
    }
    return make_dataclass(type_name, [(n, kinds[k]) for n, k in spec.TYPES[type_name]])


def _writable(type_name: str) -> bool:
    """True when the binding can serialize every field of `type_name`."""
    return all(kind == "uint32" for _, kind in spec.TYPES[type_name])


def _take_seqs(reader: Any) -> list[int | None]:
    """Take what a reader holds: the `seq` of each valid sample, None when unreadable.

    An empty reader returns [] (verified on dust-dds 0.16), so nothing is
    suppressed here. The 0.16 Python binding delivers received samples as empty
    objects without fields (verified Dust to Dust), so `seq` is usually None:
    the samples are counted but their content cannot be shown.
    """
    seqs: list[int | None] = []
    for sample in reader.take(100):
        data = sample.get_data()  # None for a disposed instance
        if data is not None:
            seqs.append(getattr(data, "seq", None))
    return seqs


def _duration(dust_dds: Any, millis: int) -> Any:
    """A finite DurationKind of `millis` milliseconds."""
    sec, rest = divmod(millis, 1000)
    return dust_dds.DurationKind.Finite(dust_dds.Duration(sec=sec, nanosec=rest * 1_000_000))


def _liveliness(dust_dds: Any, qos: spec.QosSpec) -> Any:
    """The LivelinessQosPolicy for `qos` (infinite lease when none is given)."""
    kinds = {
        "automatic": dust_dds.LivelinessQosPolicyKind.Automatic,
        "manual_participant": dust_dds.LivelinessQosPolicyKind.ManualByParticipant,
        "manual_topic": dust_dds.LivelinessQosPolicyKind.ManualByTopic,
    }
    lease = (
        dust_dds.DurationKind.Infinite
        if qos.lease_ms is None
        else _duration(dust_dds, qos.lease_ms)
    )
    return dust_dds.LivelinessQosPolicy(kinds[qos.liveliness], lease)


def _qos_kwargs(dust_dds: Any, qos: spec.QosSpec) -> dict[str, Any]:
    """Map a QosSpec onto the keywords shared by DataWriterQos and DataReaderQos."""
    reliable = qos.reliability == "reliable"
    kind = (
        dust_dds.ReliabilityQosPolicyKind.Reliable
        if reliable
        else dust_dds.ReliabilityQosPolicyKind.BestEffort
    )
    history = (
        dust_dds.HistoryQosPolicyKind.KeepAll()
        if qos.history_depth is None
        else dust_dds.HistoryQosPolicyKind.KeepLast(qos.history_depth)
    )
    durability = (
        dust_dds.DurabilityQosPolicyKind.TransientLocal
        if qos.durability == "transient_local"
        else dust_dds.DurabilityQosPolicyKind.Volatile
    )
    kwargs: dict[str, Any] = {
        "reliability": dust_dds.ReliabilityQosPolicy(kind, _duration(dust_dds, 1000)),
        "durability": dust_dds.DurabilityQosPolicy(durability),
        "history": dust_dds.HistoryQosPolicy(history),
    }
    if qos.deadline_ms is not None:
        kwargs["deadline"] = dust_dds.DeadlineQosPolicy(_duration(dust_dds, qos.deadline_ms))
    if qos.liveliness != "automatic" or qos.lease_ms is not None:
        kwargs["liveliness"] = _liveliness(dust_dds, qos)
    if qos.ownership == "exclusive":
        kwargs["ownership"] = dust_dds.OwnershipQosPolicy(dust_dds.OwnershipQosPolicyKind.Exclusive)
    return kwargs


def main(argv: list[str] | None = None) -> int:
    args = spec.parse_args("Dust DDS", argv)

    try:
        import dust_dds
    except ImportError:
        print("error: install the Dust DDS binding first: pip install dust-dds", file=sys.stderr)
        return 1

    try:
        dds_types = {
            name: _build_type(dust_dds, name)
            for name in {e.type_name for e in (*args.write, *args.read)}
        }
        user_data = dust_dds.UserDataQosPolicy(list(args.name.encode("utf-8")))
        participant = dust_dds.DomainParticipantFactory.get_instance().create_participant(
            domain_id=args.domain, qos=dust_dds.DomainParticipantQos(user_data=user_data)
        )
        topics: dict[tuple[str, str], Any] = {}

        def topic_for(e: spec.Endpoint) -> Any:
            key = (e.topic, e.type_name)
            if key not in topics:
                topics[key] = participant.create_topic(
                    topic_name=e.topic, type_=dds_types[e.type_name]
                )
            return topics[key]

        publishers: dict[tuple[str, ...], Any] = {}
        subscribers: dict[tuple[str, ...], Any] = {}

        def partition_qos(cls: Any, e: spec.Endpoint) -> Any:
            """PublisherQos / SubscriberQos carrying the endpoint's partition, or None."""
            if not e.qos.partition:
                return None
            return cls(partition=dust_dds.PartitionQosPolicy(list(e.qos.partition)))

        def publisher_for(e: spec.Endpoint) -> Any:
            if e.qos.partition not in publishers:
                publishers[e.qos.partition] = participant.create_publisher(
                    qos=partition_qos(dust_dds.PublisherQos, e)
                )
            return publishers[e.qos.partition]

        def subscriber_for(e: spec.Endpoint) -> Any:
            if e.qos.partition not in subscribers:
                subscribers[e.qos.partition] = participant.create_subscriber(
                    qos=partition_qos(dust_dds.SubscriberQos, e)
                )
            return subscribers[e.qos.partition]

        writers = [
            (
                e,
                publisher_for(e).create_datawriter(
                    topic_for(e), qos=dust_dds.DataWriterQos(**_qos_kwargs(dust_dds, e.qos))
                ),
            )
            for e in args.write
        ]
        readers = [
            (
                e,
                subscriber_for(e).create_datareader(
                    topic_for(e), qos=dust_dds.DataReaderQos(**_qos_kwargs(dust_dds, e.qos))
                ),
            )
            for e in args.read
        ]
    except Exception as exc:
        print(f"error: Dust DDS setup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(spec.start_line(VENDOR, args), flush=True)

    stop = False

    def _stop(*_: Any) -> None:
        nonlocal stop
        stop = True

    for sig_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, sig_name):
            signal.signal(getattr(signal, sig_name), _stop)

    silent = sorted({e.type_name for e, _ in writers if not _writable(e.type_name)})
    if silent:
        print(
            f"warning: the Dust DDS Python binding cannot serialize {', '.join(silent)}; "
            "those writers exist on the bus but publish nothing",
            file=sys.stderr,
            flush=True,
        )
    if any(e.qos.ownership == "exclusive" and e.qos.strength for e, _ in writers):
        print(
            "warning: the Dust DDS Python binding cannot set ownership strength; "
            "strength= is ignored (strength 0)",
            file=sys.stderr,
            flush=True,
        )
    writers = [(e, w) for e, w in writers if _writable(e.type_name)]

    period_s = 1.0 / args.rate_hz
    # Absolute schedule: sleeping a full period after the work would drift
    # below the requested rate (about 7 Hz for 10 Hz on Windows).
    next_tick = time.monotonic()
    seq = 0
    timed_out: set[str] = set()
    started = time.monotonic()
    silent_at = None if args.stop_asserting_after is None else started + args.stop_asserting_after
    hung = False
    rx = spec.RxReport(args.name, [e.topic for e, _ in readers], started)
    try:
        while not stop:
            if not hung and silent_at is not None and time.monotonic() >= silent_at:
                hung = True
                print(f"[{args.name}] stopped writing and asserting liveliness", flush=True)
            for endpoint, writer in [] if hung else writers:
                cls = dds_types[endpoint.type_name]
                try:
                    writer.write(cls(**spec.sample_values(endpoint.type_name, seq)))
                except Exception as exc:
                    # Dust 0.16 RELIABLE writes time out against a Cyclone
                    # reader (observed 2026-10-02). A real program would keep
                    # running, so this one does too and says so once.
                    if "Timeout" not in str(exc):
                        raise
                    if endpoint.topic not in timed_out:
                        timed_out.add(endpoint.topic)
                        print(
                            f"warning: write on {endpoint.topic} timed out; continuing",
                            file=sys.stderr,
                            flush=True,
                        )
            # take, not read, so that a KEEP_ALL reader does not grow without bound
            for endpoint, reader in readers:
                rx.record(endpoint.topic, _take_seqs(reader))
            if rx.due(time.monotonic()):
                for line in rx.lines(time.monotonic()):
                    print(line, flush=True)
            seq += 1
            next_tick += period_s
            if next_tick < time.monotonic() - period_s:  # overran: do not burst to catch up
                next_tick = time.monotonic()
            time.sleep(max(0.0, next_tick - time.monotonic()))
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"error: Dust DDS write failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
