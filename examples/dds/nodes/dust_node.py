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
"""

# No `from __future__ import annotations` here: dust_dds reads the real
# `__annotations__` of the dataclass to build the DDS type.
import contextlib
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


def _take_seqs(reader: Any) -> list[int]:
    """Take what a reader holds and return the `seq` of each valid sample.

    Some bindings raise when there is no data; that is "nothing received".
    """
    with contextlib.suppress(Exception):
        seqs = []
        for sample in reader.take(100):
            data = sample.get_data()  # None for a disposed instance
            if data is not None:
                seqs.append(data.seq)
        return seqs
    return []


def _duration(dust_dds: Any, millis: int) -> Any:
    """A finite DurationKind of `millis` milliseconds."""
    sec, rest = divmod(millis, 1000)
    return dust_dds.DurationKind.Finite(dust_dds.Duration(sec=sec, nanosec=rest * 1_000_000))


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

        publisher = participant.create_publisher()
        writers = [
            (
                e,
                publisher.create_datawriter(
                    topic_for(e), qos=dust_dds.DataWriterQos(**_qos_kwargs(dust_dds, e.qos))
                ),
            )
            for e in args.write
        ]
        subscriber = participant.create_subscriber()
        readers = [
            (
                e,
                subscriber.create_datareader(
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
    writers = [(e, w) for e, w in writers if _writable(e.type_name)]

    period_s = 1.0 / args.rate_hz
    # Absolute schedule: sleeping a full period after the work would drift
    # below the requested rate (about 7 Hz for 10 Hz on Windows).
    next_tick = time.monotonic()
    seq = 0
    timed_out: set[str] = set()
    rx = spec.RxReport(args.name, [e.topic for e, _ in readers], time.monotonic())
    try:
        while not stop:
            for endpoint, writer in writers:
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
                for line in rx.lines():
                    print(line, flush=True)
            seq += 1
            next_tick += period_s
            time.sleep(max(0.0, next_tick - time.monotonic()))
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"error: Dust DDS write failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
