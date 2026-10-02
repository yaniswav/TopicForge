"""Cyclone DDS role node: one participant, N writers, M readers.

    python cyclone_node.py --domain 0 --name lidar_driver \
        --write scan:LidarScan:best_effort --read odom:Odom

Writes deterministic samples at --rate-hz until stopped. TopicForge is the
read-only observer; this node is an ordinary application.
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


def _qos(core: Any, duration: Any, qos: spec.QosSpec) -> Any:
    """Map a QosSpec onto a Cyclone Qos."""
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
    return core.Qos(*policies)


def main(argv: list[str] | None = None) -> int:
    args = spec.parse_args("Cyclone DDS", argv)

    try:
        from cyclonedds import core
        from cyclonedds.domain import DomainParticipant
        from cyclonedds.idl import IdlStruct
        from cyclonedds.idl import types as idl_types
        from cyclonedds.pub import DataWriter
        from cyclonedds.sub import DataReader
        from cyclonedds.topic import Topic
        from cyclonedds.util import duration
    except ImportError:
        print("error: install the Cyclone binding first: pip install cyclonedds", file=sys.stderr)
        return 1

    try:
        dds_types = _build_types(IdlStruct, idl_types)
        # EntityName shows up as the participant name in DCPSParticipant.
        dp = DomainParticipant(args.domain, qos=core.Qos(core.Policy.EntityName(args.name)))
        writers = [
            (
                e,
                DataWriter(
                    dp, Topic(dp, e.topic, dds_types[e.type_name]), qos=_qos(core, duration, e.qos)
                ),
            )
            for e in args.write
        ]
        readers = [
            (
                e,
                DataReader(
                    dp, Topic(dp, e.topic, dds_types[e.type_name]), qos=_qos(core, duration, e.qos)
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
    rx = spec.RxReport(args.name, [e.topic for e, _ in readers], time.monotonic())
    try:
        while not stop:
            for endpoint, writer in writers:
                cls = dds_types[endpoint.type_name]
                writer.write(cls(**spec.sample_values(endpoint.type_name, seq)))
            # take, not read: read leaves samples in the cache, and a KEEP_ALL
            # reader would otherwise grow without bound.
            for endpoint, reader in readers:
                got = reader.take(N=100)
                # disposed instances come back as InvalidSample, without a seq
                rx.record(endpoint.topic, [s.seq for s in got if hasattr(s, "seq")])
            if rx.due(time.monotonic()):
                for line in rx.lines():
                    print(line, flush=True)
            seq += 1
            next_tick += period_s
            time.sleep(max(0.0, next_tick - time.monotonic()))
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"error: Cyclone DDS write failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
