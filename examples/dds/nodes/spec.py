"""Vendor-neutral description of the role nodes used by the DDS examples.

Pure Python, no DDS import. The vendor scripts (`cyclone_node.py`,
`dust_node.py`, `rti_node.py`) share this module so that they accept the same
command line and publish the same types, whatever the binding underneath.

Endpoint syntax: ``TOPIC:TYPE[:opt,opt,...]`` with options ``reliable``,
``best_effort``, ``volatile``, ``transient_local``, ``keep_all``,
``keep_last=N`` and ``deadline=MS``.
"""

import argparse
import math
from dataclasses import dataclass, field
from typing import Any, Literal

FieldKind = Literal["uint32", "float32", "float64", "string"]

# type name -> ordered (field name, kind). The first field is always `seq`.
TYPES: dict[str, tuple[tuple[str, FieldKind], ...]] = {
    "LidarScan": (("seq", "uint32"), ("range_m", "float32")),
    "Odom": (("seq", "uint32"), ("x", "float64"), ("y", "float64")),
    "Imu": (("seq", "uint32"), ("yaw_rad", "float64")),
    "Twist": (("seq", "uint32"), ("linear", "float64"), ("angular", "float64")),
    "Status": (("seq", "uint32"), ("text", "string")),
    "Heartbeat": (("seq", "uint32"),),
}

MAX_DOMAIN_ID = 232


@dataclass(frozen=True)
class QosSpec:
    """The QoS subset the role nodes can set on an endpoint.

    `history_depth` of None means KEEP_ALL. `deadline_ms` of None means no
    deadline.
    """

    reliability: Literal["reliable", "best_effort"] = "reliable"
    durability: Literal["volatile", "transient_local"] = "volatile"
    history_depth: int | None = 1
    deadline_ms: int | None = None


@dataclass(frozen=True)
class Endpoint:
    """One writer or reader: a topic, its type name and its QoS."""

    topic: str
    type_name: str
    qos: QosSpec = field(default_factory=QosSpec)


def _positive_int(option: str, raw: str) -> int:
    """Parse the integer argument of `keep_last=N` / `deadline=MS`."""
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"option {option}= needs an integer, got {raw!r}") from None
    if value < 1:
        raise ValueError(f"option {option}= must be >= 1, got {value}")
    return value


def _apply_options(opts: list[str]) -> QosSpec:
    """Fold a list of option tokens into a QosSpec, rejecting conflicts."""
    reliability: set[str] = set()
    durability: set[str] = set()
    history: set[int | None] = set()
    deadline_ms: int | None = None
    for opt in opts:
        name, _, value = opt.partition("=")
        if opt in ("reliable", "best_effort"):
            reliability.add(opt)
        elif opt in ("volatile", "transient_local"):
            durability.add(opt)
        elif opt == "keep_all":
            history.add(None)
        elif name == "keep_last" and "=" in opt:
            history.add(_positive_int("keep_last", value))
        elif name == "deadline" and "=" in opt:
            deadline_ms = _positive_int("deadline", value)
        else:
            raise ValueError(f"unknown QoS option {opt!r}")
    if len(reliability) > 1:
        raise ValueError("conflicting options: reliable and best_effort")
    if len(durability) > 1:
        raise ValueError("conflicting options: volatile and transient_local")
    if len(history) > 1:
        raise ValueError("conflicting history options: keep_all and keep_last")
    return QosSpec(
        reliability="best_effort" if "best_effort" in reliability else "reliable",
        durability="transient_local" if "transient_local" in durability else "volatile",
        history_depth=history.pop() if history else 1,
        deadline_ms=deadline_ms,
    )


def parse_endpoint(text: str) -> Endpoint:
    """Parse ``TOPIC:TYPE[:opt,opt,...]`` into an Endpoint.

    Raises ValueError with a readable message on any malformed input.
    """
    parts = text.split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"expected TOPIC:TYPE[:options], got {text!r}")
    topic, type_name = parts[0].strip(), parts[1].strip()
    if not topic:
        raise ValueError(f"empty topic name in {text!r}")
    if type_name not in TYPES:
        raise ValueError(f"unknown type {type_name!r}; choose one of {', '.join(TYPES)}")
    opts = [o.strip() for o in parts[2].split(",") if o.strip()] if len(parts) == 3 else []
    return Endpoint(topic, type_name, _apply_options(opts))


def describe(endpoint: Endpoint) -> str:
    """Short human text, e.g. ``scan (LidarScan, BEST_EFFORT, VOLATILE, KEEP_LAST 1)``."""
    qos = endpoint.qos
    history = "KEEP_ALL" if qos.history_depth is None else f"KEEP_LAST {qos.history_depth}"
    text = (
        f"{endpoint.topic} ({endpoint.type_name}, {qos.reliability.upper()}, "
        f"{qos.durability.upper()}, {history}"
    )
    text += ")"
    if qos.deadline_ms is not None:
        text += f", deadline {qos.deadline_ms} ms"
    return text


def sample_values(type_name: str, seq: int) -> dict[str, Any]:
    """Deterministic, plausible values for every field of `type_name`."""
    wave = math.sin(seq * 0.1)
    values: dict[str, Any] = {
        "seq": seq,
        "range_m": 2.0 + 0.5 * wave,
        "x": seq * 0.05,
        "y": 0.5 * wave,
        "yaw_rad": 0.3 * wave,
        "linear": 0.2 + 0.05 * wave,
        "angular": 0.1 * wave,
        "text": f"ok {seq}",
    }
    return {name: values[name] for name, _ in TYPES[type_name]}


def _domain(raw: str) -> int:
    """argparse type for --domain: an integer in 0..232."""
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"domain must be an integer, got {raw!r}") from None
    if not 0 <= value <= MAX_DOMAIN_ID:
        raise argparse.ArgumentTypeError(f"domain must be in 0..{MAX_DOMAIN_ID}, got {value}")
    return value


def _endpoint(raw: str) -> Endpoint:
    """argparse type for --write / --read."""
    try:
        return parse_endpoint(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def build_parser(vendor_label: str) -> argparse.ArgumentParser:
    """Build the command line shared by every vendor node."""
    parser = argparse.ArgumentParser(
        description=f"{vendor_label} DDS role node (one participant, writers and readers)",
        epilog="endpoint syntax: TOPIC:TYPE[:reliable|best_effort|volatile|transient_local|"
        "keep_all|keep_last=N|deadline=MS,...]  types: " + ", ".join(TYPES),
    )
    parser.add_argument("--domain", type=_domain, default=0, help="DDS domain id, 0..232")
    parser.add_argument("--name", required=True, help="participant name (the robot role)")
    parser.add_argument("--write", action="append", type=_endpoint, default=[], metavar="ENDPOINT")
    parser.add_argument("--read", action="append", type=_endpoint, default=[], metavar="ENDPOINT")
    parser.add_argument("--rate-hz", type=float, default=10.0, help="write rate, default 10")
    return parser


def parse_args(vendor_label: str, argv: list[str] | None = None) -> argparse.Namespace:
    """Parse argv and require at least one --write or --read."""
    parser = build_parser(vendor_label)
    args = parser.parse_args(argv)
    if not args.write and not args.read:
        parser.error("give at least one --write or --read")
    if args.rate_hz <= 0:
        parser.error("--rate-hz must be > 0")
    return args


def start_line(vendor: str, args: argparse.Namespace) -> str:
    """The single line a node prints once its entities exist."""
    writes = "; ".join(describe(e) for e in args.write) or "nothing"
    reads = "; ".join(describe(e) for e in args.read) or "nothing"
    return f"[{args.name}] {vendor} domain {args.domain}: writes {writes}; reads {reads}"


class RxReport:
    """Once per second, one line per reader: how many samples it received.

    Lines look like `[nav_planner] rx scan: 10 in 1 s, last seq 123`, or
    `[nav_planner] rx scan: 0 in 1 s` when nothing arrived (printed anyway, so
    that absence is visible). The class does no I/O: the caller passes the
    clock to `due()` and prints what `lines()` returns.
    """

    PERIOD_S = 1.0

    def __init__(self, name: str, topics: list[str], now: float) -> None:
        self.name = name
        self._counts = {t: 0 for t in topics}
        self._last_seq: dict[str, int] = {}
        self._next = now + self.PERIOD_S

    def record(self, topic: str, seqs: list[int]) -> None:
        """Count the valid samples of one `take` and remember the last `seq`."""
        if seqs:
            self._counts[topic] += len(seqs)
            self._last_seq[topic] = seqs[-1]

    def due(self, now: float) -> bool:
        """True once per period; the next period starts from `now`."""
        if now < self._next:
            return False
        self._next = now + self.PERIOD_S
        return True

    def lines(self) -> list[str]:
        """The report lines for the period that just ended, then reset the counts."""
        out = []
        for topic, count in self._counts.items():
            line = f"[{self.name}] rx {topic}: {count} in 1 s"
            if count:
                line += f", last seq {self._last_seq[topic]}"
            out.append(line)
            self._counts[topic] = 0
        return out
