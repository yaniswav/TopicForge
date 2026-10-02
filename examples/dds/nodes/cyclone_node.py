"""Cyclone DDS role node: one participant, N writers, M readers.

    python cyclone_node.py --domain 0 --name lidar_driver \
        --write scan:LidarScan:best_effort --read odom:Odom

Writes deterministic samples at --rate-hz until stopped. TopicForge is the
read-only observer; this node is an ordinary application.

Besides reliability, durability, history and deadline, an endpoint accepts
`partition=a|b`, `liveliness=automatic|manual_participant|manual_topic`,
`lease=MS`, `ownership=shared|exclusive` and `strength=N` (writers only):

    --write scan:LidarScan:reliable,partition=left,liveliness=manual_topic,\
lease=500,ownership=exclusive,strength=10

Partition goes on a Publisher / Subscriber (Cyclone rejects it on a writer or
reader), so the node creates one per distinct partition set. A manual-liveliness
writer is kept alive: writing asserts MANUAL_BY_TOPIC, and for
MANUAL_BY_PARTICIPANT the node also calls `dds_assert_liveliness` on the
participant (the Python binding has no wrapper, so it goes through ctypes).
`--stop-asserting-after S` makes the node stop writing AND asserting after S
seconds while the process stays alive and keeps reading: a hung process that
is still on the bus, whose liveliness lease then expires.
"""

# No `from __future__ import annotations` here: cyclonedds resolves the IDL
# field types from real annotations.
import signal
import sys
import time
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import spec

VENDOR = "cyclone"


def _build_types(IdlStruct: Any, idl_types: Any) -> dict[str, type]:
    """Create one IdlStruct dataclass per entry of spec.TYPES."""
    kinds = {
        "uint32": idl_types.uint32,
        "float32": idl_types.float32,
        "float64": idl_types.float64,
        "string": str,
    }
    built: dict[str, type] = {}
    for type_name, fields in spec.TYPES.items():
        annotations = {name: kinds[kind] for name, kind in fields}
        cls = types.new_class(
            type_name,
            (IdlStruct,),
            {"typename": type_name},
            lambda ns, a=annotations: ns.update(__annotations__=a),
        )
        built[type_name] = dataclass(cls)
    return built


def _liveliness(core: Any, duration: Any, qos: spec.QosSpec) -> Any | None:
    """The Liveliness policy for `qos`, or None when it is all defaults."""
    if qos.liveliness == "automatic" and qos.lease_ms is None:
        return None
    lease = duration(infinite=True) if qos.lease_ms is None else duration(milliseconds=qos.lease_ms)
    kinds = {
        "automatic": core.Policy.Liveliness.Automatic,
        "manual_participant": core.Policy.Liveliness.ManualByParticipant,
        "manual_topic": core.Policy.Liveliness.ManualByTopic,
    }
    return kinds[qos.liveliness](lease_duration=lease)


def _qos(core: Any, duration: Any, qos: spec.QosSpec, writer: bool = False) -> Any:
    """Map a QosSpec onto a Cyclone Qos (Partition is set on the Publisher / Subscriber)."""
    policy = core.Policy
    reliability = (
        policy.Reliability.BestEffort
        if qos.reliability == "best_effort"
        else policy.Reliability.Reliable(duration(seconds=1))
    )
    durability = (
        policy.Durability.TransientLocal
        if qos.durability == "transient_local"
        else policy.Durability.Volatile
    )
    history = (
        policy.History.KeepAll
        if qos.history_depth is None
        else policy.History.KeepLast(qos.history_depth)
    )
    policies = [reliability, durability, history]
    if qos.deadline_ms is not None:
        policies.append(policy.Deadline(duration(milliseconds=qos.deadline_ms)))
    liveliness = _liveliness(core, duration, qos)
    if liveliness is not None:
        policies.append(liveliness)
    if qos.ownership == "exclusive":
        policies.append(policy.Ownership.Exclusive)
        if writer:
            policies.append(policy.OwnershipStrength(strength=qos.strength))
    return core.Qos(*policies)


def _group_qos(core: Any, partition: tuple[str, ...]) -> Any | None:
    """The Publisher / Subscriber Qos carrying `partition`, None for the default partition."""
    return core.Qos(core.Policy.Partition(list(partition))) if partition else None


def _assert_participant(dp: Any) -> None:
    """Assert the liveliness of every MANUAL_BY_PARTICIPANT writer of `dp` (via ctypes)."""
    from cyclonedds.internal import load_cyclonedds

    load_cyclonedds().dds_assert_liveliness(dp._ref)


def main(argv: list[str] | None = None) -> int:
    args = spec.parse_args("Cyclone DDS", argv)

    try:
        from cyclonedds import core
        from cyclonedds.domain import DomainParticipant
        from cyclonedds.idl import IdlStruct
        from cyclonedds.idl import types as idl_types
        from cyclonedds.pub import DataWriter, Publisher
        from cyclonedds.sub import DataReader, Subscriber
        from cyclonedds.topic import Topic
        from cyclonedds.util import duration
    except ImportError:
        print("error: install the Cyclone binding first: pip install cyclonedds", file=sys.stderr)
        return 1

    try:
        dds_types = _build_types(IdlStruct, idl_types)
        # EntityName shows up as the participant name in DCPSParticipant.
        dp = DomainParticipant(args.domain, qos=core.Qos(core.Policy.EntityName(args.name)))
        publishers: dict[tuple[str, ...], Any] = {}
        subscribers: dict[tuple[str, ...], Any] = {}

        def group(cache: dict[tuple[str, ...], Any], cls: Any, e: spec.Endpoint) -> Any:
            """The Publisher / Subscriber for the endpoint's partition set."""
            if e.qos.partition not in cache:
                cache[e.qos.partition] = cls(dp, qos=_group_qos(core, e.qos.partition))
            return cache[e.qos.partition]

        writers = [
            (
                e,
                DataWriter(
                    group(publishers, Publisher, e),
                    Topic(dp, e.topic, dds_types[e.type_name]),
                    qos=_qos(core, duration, e.qos, writer=True),
                ),
            )
            for e in args.write
        ]
        readers = [
            (
                e,
                DataReader(
                    group(subscribers, Subscriber, e),
                    Topic(dp, e.topic, dds_types[e.type_name]),
                    qos=_qos(core, duration, e.qos),
                ),
            )
            for e in args.read
        ]
    except Exception as exc:
        print(f"error: Cyclone DDS setup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(spec.start_line(VENDOR, args), flush=True)

    # SIGTERM (and SIGBREAK on Windows) become KeyboardInterrupt-like exits.
    stop = False

    def _stop(*_: Any) -> None:
        nonlocal stop
        stop = True

    for sig_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, sig_name):
            signal.signal(getattr(signal, sig_name), _stop)

    period_s = 1.0 / args.rate_hz
    # Absolute schedule: sleeping a full period after the work would drift
    # below the requested rate (about 7 Hz for 10 Hz on Windows).
    next_tick = time.monotonic()
    seq = 0
    started = time.monotonic()
    silent_at = None if args.stop_asserting_after is None else started + args.stop_asserting_after
    hung = False
    manual_participant = any(e.qos.liveliness == "manual_participant" for e, _ in writers)
    rx = spec.RxReport(args.name, [e.topic for e, _ in readers], started)
    try:
        while not stop:
            if not hung and silent_at is not None and time.monotonic() >= silent_at:
                hung = True
                print(f"[{args.name}] stopped writing and asserting liveliness", flush=True)
            if not hung:
                for endpoint, writer in writers:
                    cls = dds_types[endpoint.type_name]
                    writer.write(cls(**spec.sample_values(endpoint.type_name, seq)))
                if manual_participant:
                    _assert_participant(dp)
            # take, not read: read leaves samples in the cache, and a KEEP_ALL
            # reader would otherwise grow without bound.
            for endpoint, reader in readers:
                got = reader.take(N=100)
                # disposed instances come back as InvalidSample, without a seq
                rx.record(endpoint.topic, [s.seq for s in got if hasattr(s, "seq")])
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
        print(f"error: Cyclone DDS write failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
