"""RTI Connext DDS role node: one participant, N writers, M readers.

    python rti_node.py --domain 0 --name imu_driver \
        --write imu:Imu:reliable --read cmd_vel:Twist

Writes deterministic samples at --rate-hz until stopped. TopicForge is the
read-only observer; this node is an ordinary application.

Requirements: `pip install rti.connext` (Connext 7.x Python API) and a license
file, normally pointed to by RTI_LICENSE_FILE. Never commit the license.
NOT RUN against a real bus (no license on the dev machine).

RTI API calls, checked against rticommunity sources (gh api):
  - rticonnextdds-comparison-tractor-fleet, dds/robot_node.py and
    rticonnextdds-comparison-air-traffic, connext_dds/python/common.py:
    `qos.participant_name.name = ...`, `dds.DomainParticipant(domain_id, qos)`,
    `qos.deadline.period = ...`.
  - rticonnextdds-examples, asynchronous_publication/py: `history.kind =
    dds.HistoryKind.KEEP_LAST`, `history.depth = N`, `HistoryKind.KEEP_ALL`,
    `dp.default_datawriter_qos`, `dp.default_datareader_qos`,
    `dds.DataWriter(dp.implicit_publisher, topic, qos)`,
    `dds.DataReader(dp.implicit_subscriber, topic, qos)`, `writer.write(s)`.
  - rti-genesis, genesis_lib/graph_monitoring.py:
    `durability.kind = dds.DurabilityKind.TRANSIENT_LOCAL`,
    `reliability.kind = dds.ReliabilityKind.RELIABLE`.
Partition, liveliness and ownership (NOT verified against a source, mapped
from the RTI Python API naming): `publisher_qos.partition.name = [...]` on the
implicit publisher / subscriber (applied through a new Publisher / Subscriber
per partition set), `qos.liveliness.kind = dds.LivelinessKind.AUTOMATIC |
MANUAL_BY_PARTICIPANT | MANUAL_BY_TOPIC`, `qos.liveliness.lease_duration`,
`qos.ownership.kind = dds.OwnershipKind.EXCLUSIVE`,
`qos.ownership_strength.value = N`, `participant.assert_liveliness()`.
`--stop-asserting-after S` stops writing and asserting after S seconds.
Not verified against a source: `dds.Duration.from_milliseconds`,
`dds.DurabilityKind.VOLATILE`, `dds.ReliabilityKind.BEST_EFFORT` (symmetric
with verified names), `reader.take_data()`, `str` as an unbounded string
member of an `@idl.struct`, and building the struct dynamically with
`idl.struct(cls)` instead of the decorator syntax.
"""

# No `from __future__ import annotations` here: `@idl.struct` resolves the IDL
# field types from real annotations.
import signal
import sys
import time
import types
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import spec

VENDOR = "rti"


def _build_types(idl: Any) -> dict[str, type]:
    """Create one @idl.struct per entry of spec.TYPES."""
    kinds = {
        "uint32": (idl.uint32, 0),
        "float32": (idl.float32, 0.0),
        "float64": (idl.float64, 0.0),
        "string": (str, ""),
    }
    built: dict[str, type] = {}
    for type_name, fields in spec.TYPES.items():
        body: dict[str, Any] = {n: kinds[k][1] for n, k in fields}
        body["__annotations__"] = {n: kinds[k][0] for n, k in fields}
        cls = types.new_class(type_name, (), {}, lambda ns, b=body: ns.update(b))
        built[type_name] = idl.struct(cls)
    return built


def _apply_qos(dds: Any, base: Any, qos: spec.QosSpec) -> Any:
    """Set a QosSpec on a default DataWriterQos / DataReaderQos."""
    if qos.reliability == "reliable":
        base.reliability.kind = dds.ReliabilityKind.RELIABLE
    else:
        base.reliability.kind = dds.ReliabilityKind.BEST_EFFORT
    base.durability.kind = (
        dds.DurabilityKind.TRANSIENT_LOCAL
        if qos.durability == "transient_local"
        else dds.DurabilityKind.VOLATILE
    )
    if qos.history_depth is None:
        base.history.kind = dds.HistoryKind.KEEP_ALL
    else:
        base.history.kind = dds.HistoryKind.KEEP_LAST
        base.history.depth = qos.history_depth
    if qos.deadline_ms is not None:
        base.deadline.period = dds.Duration.from_milliseconds(qos.deadline_ms)
    base.liveliness.kind = {
        "automatic": dds.LivelinessKind.AUTOMATIC,
        "manual_participant": dds.LivelinessKind.MANUAL_BY_PARTICIPANT,
        "manual_topic": dds.LivelinessKind.MANUAL_BY_TOPIC,
    }[qos.liveliness]
    if qos.lease_ms is not None:
        base.liveliness.lease_duration = dds.Duration.from_milliseconds(qos.lease_ms)
    if qos.ownership == "exclusive":
        base.ownership.kind = dds.OwnershipKind.EXCLUSIVE
        if hasattr(base, "ownership_strength"):  # DataWriterQos only
            base.ownership_strength.value = qos.strength
    return base


def _group(dds: Any, dp: Any, cache: dict[tuple[str, ...], Any], e: spec.Endpoint, writer: bool):
    """The Publisher / Subscriber for the endpoint's partition set (implicit one if none)."""
    if not e.qos.partition:
        return dp.implicit_publisher if writer else dp.implicit_subscriber
    if e.qos.partition not in cache:
        qos = dp.default_publisher_qos if writer else dp.default_subscriber_qos
        qos.partition.name = list(e.qos.partition)
        cache[e.qos.partition] = (dds.Publisher if writer else dds.Subscriber)(dp, qos)
    return cache[e.qos.partition]


def main(argv: list[str] | None = None) -> int:
    args = spec.parse_args("RTI Connext DDS", argv)

    try:
        import rti.connextdds as dds
        import rti.idl as idl
    except (ImportError, OSError) as exc:
        print(
            f"error: cannot import rti.connextdds ({exc}). Install the RTI Connext Python "
            "API with: pip install rti.connext, and provide a license file (RTI_LICENSE_FILE).",
            file=sys.stderr,
        )
        return 1

    try:
        dds_types = _build_types(idl)
        participant_qos = dds.DomainParticipantQos()
        participant_qos.participant_name.name = args.name
        dp = dds.DomainParticipant(args.domain, participant_qos)
        groups: dict[tuple[str, ...], Any] = {}
        writers = [
            (
                e,
                dds.DataWriter(
                    _group(dds, dp, groups, e, True),
                    dds.Topic(dp, e.topic, dds_types[e.type_name]),
                    _apply_qos(dds, dp.default_datawriter_qos, e.qos),
                ),
            )
            for e in args.write
        ]
        readers = [
            (
                e,
                dds.DataReader(
                    _group(dds, dp, groups, e, False),
                    dds.Topic(dp, e.topic, dds_types[e.type_name]),
                    _apply_qos(dds, dp.default_datareader_qos, e.qos),
                ),
            )
            for e in args.read
        ]
    except Exception as exc:  # RTI raises dds.Error and others; keep the message readable
        print(
            f"error: could not create the RTI Connext entities ({type(exc).__name__}: {exc}). "
            "Check the license file (RTI_LICENSE_FILE) and its entity limits.",
            file=sys.stderr,
        )
        return 1

    print(spec.start_line(VENDOR, args), flush=True)

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
            if manual_participant and not hung:
                dp.assert_liveliness()
            for endpoint, writer in [] if hung else writers:
                cls = dds_types[endpoint.type_name]
                writer.write(cls(**spec.sample_values(endpoint.type_name, seq)))
            # take, so that a KEEP_ALL reader does not grow without bound
            for endpoint, reader in readers:
                rx.record(endpoint.topic, [d.seq for d in reader.take_data()])
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
        print(f"error: RTI Connext write failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
