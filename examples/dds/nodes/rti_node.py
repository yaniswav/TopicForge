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
    return base


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
        writers = [
            (
                e,
                dds.DataWriter(
                    dp.implicit_publisher,
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
                    dp.implicit_subscriber,
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
    seq = 0
    try:
        while not stop:
            for endpoint, writer in writers:
                cls = dds_types[endpoint.type_name]
                writer.write(cls(**spec.sample_values(endpoint.type_name, seq)))
            for endpoint, reader in readers:
                if endpoint.qos.history_depth is None:
                    reader.take_data()  # KEEP_ALL would otherwise grow without bound
            seq += 1
            time.sleep(period_s)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"error: RTI Connext write failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
