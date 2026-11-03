"""Pure parsers for `ros2 topic list -v` and the QoS block of `ros2 topic info --verbose`.

Module-level functions over CLI text, tested without ROS 2. Also holds the
`ros2 topic list -t`, `ros2 topic info` and `ros2 bag info` parsers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from topicforge.adapters.base import EffectiveMode
from topicforge.adapters.common.bag_kind import classify_bag_topic
from topicforge.models import BagAnalysis, BagTopicStats, SideQos, TopicInfo

_VERBOSE_LIST_HEADER = re.compile(r"^\s*(Published|Subscribed) topics:\s*$")
_VERBOSE_LIST_ITEM = re.compile(r"^\s*\*\s+(\S+)\s+\[.*\]\s+(\d+)\s+(?:publisher|subscriber)s?\s*$")
_NODE_NAME = re.compile(r"^\s*Node name:\s*(\S*)")
_NODE_NAMESPACE = re.compile(r"^\s*Node namespace:\s*(\S*)")
_ENDPOINT_TYPE = re.compile(r"^\s*Endpoint type:\s*(\S+)")
_RELIABILITY = re.compile(r"^\s*Reliability:\s*(\S+)")
_DURABILITY = re.compile(r"^\s*Durability:\s*(\S+)")

_RELIABILITY_VALUES = {"RELIABLE": "reliable", "BEST_EFFORT": "best_effort"}
_DURABILITY_VALUES = {"VOLATILE": "volatile", "TRANSIENT_LOCAL": "transient_local"}
_UNKNOWN_NODE = "_NODE_NAME_UNKNOWN_"


_SERVICE_LINE = re.compile(r"^\s*(/\S+)\s+\[([^\]]*)\]\s*$")


def parse_service_list(stdout: str) -> dict[str, list[str]]:
    """Parse `ros2 service list -t` into `{service: [types]}`.

    A line is `/name [pkg/srv/Type]`; some distros print several types separated by
    commas. Lines that do not match (warnings, blank lines) are skipped.
    """
    services: dict[str, list[str]] = {}
    for line in stdout.splitlines():
        match = _SERVICE_LINE.match(line)
        if match:
            types = [t.strip() for t in match.group(2).split(",") if t.strip()]
            services[match.group(1)] = types
    return services


@dataclass(frozen=True)
class EndpointQos:
    """QoS of one endpoint from `ros2 topic info --verbose`.

    `reliability` and `durability` are lowercase (`reliable`, `best_effort`,
    `volatile`, `transient_local`), or `None` when the CLI printed `UNKNOWN`
    or `SYSTEM_DEFAULT`. `node` is the fully qualified node name from the
    `Node name:` and `Node namespace:` lines, `None` when the CLI could not name
    the node (`_NODE_NAME_UNKNOWN_`).
    """

    endpoint_type: str
    reliability: str | None
    durability: str | None
    node: str | None = None


def parse_topic_list_verbose(stdout: str) -> dict[str, tuple[int, int]] | None:
    """Parse `ros2 topic list -v` into `{topic: (publisher_count, subscriber_count)}`.

    The CLI prints a `Published topics:` and a `Subscribed topics:` section,
    each listing ` * /name [type] N publisher(s)|subscriber(s)`, and leaves a
    topic out of a section where its count is 0. Returns `None` when neither
    header is present (an unrecognized format), so the caller can fall back.
    """
    counts: dict[str, list[int]] = {}
    section: int | None = None
    seen_header = False
    for line in stdout.splitlines():
        header = _VERBOSE_LIST_HEADER.match(line)
        if header:
            seen_header = True
            section = 0 if header.group(1) == "Published" else 1
            continue
        item = _VERBOSE_LIST_ITEM.match(line)
        if item and section is not None:
            counts.setdefault(item.group(1), [0, 0])[section] = int(item.group(2))
    if not seen_header:
        return None
    return {name: (pair[0], pair[1]) for name, pair in counts.items()}


def parse_topic_endpoint_qos(stdout: str) -> list[EndpointQos]:
    """Parse the per-endpoint QoS blocks of `ros2 topic info <topic> --verbose`.

    One entry per `Node name:` block that has an `Endpoint type:`. Fast DDS
    prints `History (Depth): UNKNOWN`; history is not read.
    """
    endpoints: list[EndpointQos] = []
    current: dict[str, str | None] | None = None

    def close() -> None:
        if current is not None and current.get("type"):
            endpoints.append(
                EndpointQos(
                    endpoint_type=str(current["type"]),
                    reliability=current.get("reliability"),
                    durability=current.get("durability"),
                    node=current.get("node"),
                )
            )

    for line in stdout.splitlines():
        if m := _NODE_NAME.match(line):
            close()
            current = {"name": m.group(1)}
        elif current is None:
            continue
        elif m := _NODE_NAMESPACE.match(line):
            current["node"] = qualified_node_name(m.group(1), current.get("name") or "")
        elif m := _ENDPOINT_TYPE.match(line):
            current["type"] = m.group(1)
        elif m := _RELIABILITY.match(line):
            current["reliability"] = _RELIABILITY_VALUES.get(m.group(1).upper())
        elif m := _DURABILITY.match(line):
            current["durability"] = _DURABILITY_VALUES.get(m.group(1).upper())
    close()
    return endpoints


def qualified_node_name(namespace: str, name: str) -> str | None:
    """Fully qualified node name from the `Node namespace:` and `Node name:` values.

    `("/", "lidar")` gives `/lidar`, `("/robot1", "lidar")` gives `/robot1/lidar`.
    `None` for an empty or unknown name.
    """
    if not name or name == _UNKNOWN_NODE:
        return None
    prefix = namespace.rstrip("/")
    return f"{prefix}/{name}"


def side_nodes(endpoints: list[EndpointQos], endpoint_type: str) -> list[str]:
    """Distinct node names of one side (`PUBLISHER` or `SUBSCRIPTION`), in CLI order."""
    names: list[str] = []
    for e in endpoints:
        if e.endpoint_type.upper() == endpoint_type and e.node and e.node not in names:
            names.append(e.node)
    return names


def summarize_side_qos(
    endpoints: list[EndpointQos], endpoint_type: str, declared_count: int
) -> tuple[SideQos | None, str | None]:
    """`(SideQos, None)` for one side, or `(None, note)` saying why there is none.

    `endpoint_type` is `PUBLISHER` or `SUBSCRIPTION`; `declared_count` is the count the
    CLI printed for that side. Reliability and durability are the shared value, or
    `mixed` when the endpoints disagree. A policy that no endpoint reported (`UNKNOWN` or
    `SYSTEM_DEFAULT`) leaves the side without QoS, with a note.
    """
    word = "publishers" if endpoint_type == "PUBLISHER" else "subscriptions"
    side = [e for e in endpoints if e.endpoint_type.upper() == endpoint_type]
    if declared_count == 0 and not side:
        who = "publisher" if endpoint_type == "PUBLISHER" else "subscriber"
        return None, f"The topic has no {who}, so there is no QoS to report."
    if not side:
        return None, f"The `ros2` CLI did not list the QoS of the {word}."
    reliability = _agree([e.reliability for e in side])
    durability = _agree([e.durability for e in side])
    if reliability is None or durability is None:
        return None, (
            f"The {word} did not report their reliability and durability "
            "(the CLI printed UNKNOWN or SYSTEM_DEFAULT)."
        )
    return SideQos(
        reliability=reliability,  # type: ignore[arg-type]
        durability=durability,  # type: ignore[arg-type]
        endpoint_count=len(side),
    ), None


def _agree(values: list[str | None]) -> str | None:
    known = {v for v in values if v is not None}
    if not known:
        return None
    return known.pop() if len(known) == 1 else "mixed"


# Topic list, topic info and bag info

_LIST_LINE = re.compile(r"^(\S+)\s+\[(.+)\]\s*$")
_TYPE_LINE = re.compile(r"^\s*Type:\s*(.+)$")
_PUB_COUNT = re.compile(r"^\s*Publisher count:\s*(\d+)\s*$")
_SUB_COUNT = re.compile(r"^\s*Subscription count:\s*(\d+)\s*$")
_BAG_DUR = re.compile(r"Duration:\s*([\d.]+)\s*s")
_BAG_COUNT = re.compile(r"Messages:\s*(\d+)")
_BAG_STORAGE = re.compile(r"Storage id:\s*(\S+)")
_BAG_TOPIC = re.compile(
    r"Topic:\s*(\S+)\s*\|\s*Type:\s*(\S+)\s*\|\s*Count:\s*(\d+)\s*\|\s*Serialization Format:"
)


def parse_topic_list(stdout: str) -> list[tuple[str, str]]:
    """Parse `ros2 topic list -t` output. Returns (name, type) pairs."""
    pairs: list[tuple[str, str]] = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _LIST_LINE.match(line)
        if m:
            pairs.append((m.group(1), m.group(2)))
    return pairs


def parse_pub_sub_counts(stdout: str) -> tuple[int, int]:
    """Parse publisher / subscription counts from `ros2 topic info` output."""
    pub = sub = 0
    for line in stdout.splitlines():
        if m := _PUB_COUNT.match(line):
            pub = int(m.group(1))
        elif m := _SUB_COUNT.match(line):
            sub = int(m.group(1))
    return pub, sub


def parse_topic_info(
    stdout: str, *, fallback_name: str, mode_effective: EffectiveMode
) -> TopicInfo | None:
    """Parse `ros2 topic info <topic> --verbose` output.

    Returns None when no message type is found (the adapter reports the topic
    as not found). `fallback_name` and `mode_effective` come from the caller,
    not from the output. `publisher_qos` and `subscription_qos` summarize the QoS block
    of each side (see `summarize_side_qos`); `publisher_nodes` and `subscriber_nodes` come
    from the `Node name:` / `Node namespace:` lines.
    """
    msg_type: str | None = None
    pub = sub = 0
    for line in stdout.splitlines():
        if m := _TYPE_LINE.match(line):
            msg_type = m.group(1).strip()
        elif m := _PUB_COUNT.match(line):
            pub = int(m.group(1))
        elif m := _SUB_COUNT.match(line):
            sub = int(m.group(1))
    if msg_type is None:
        return None
    endpoints = parse_topic_endpoint_qos(stdout)
    publisher_qos, publisher_note = summarize_side_qos(endpoints, "PUBLISHER", pub)
    subscription_qos, subscription_note = summarize_side_qos(endpoints, "SUBSCRIPTION", sub)
    return TopicInfo(
        name=fallback_name,
        message_type=msg_type,
        publisher_count=pub,
        subscriber_count=sub,
        publisher_qos=publisher_qos,
        publisher_qos_note=publisher_note,
        subscription_qos=subscription_qos,
        subscription_qos_note=subscription_note,
        publisher_nodes=side_nodes(endpoints, "PUBLISHER"),
        subscriber_nodes=side_nodes(endpoints, "SUBSCRIPTION"),
        mode_effective=mode_effective,
    )


def parse_bag_info(
    stdout: str, *, fallback_path: str, mode_effective: EffectiveMode
) -> BagAnalysis:
    """Parse `ros2 bag info <path>` output. `fallback_path` and `mode_effective` come from the caller."""
    duration = 0.0
    msg_count = 0
    storage: str | None = None
    topics: list[BagTopicStats] = []

    for line in stdout.splitlines():
        if m := _BAG_DUR.search(line):
            duration = float(m.group(1))
        if m := _BAG_COUNT.search(line):
            msg_count = int(m.group(1))
        if m := _BAG_STORAGE.search(line):
            storage = m.group(1)
        if m := _BAG_TOPIC.search(line):
            name, msg_type, count = m.group(1), m.group(2), int(m.group(3))
            freq = (count / duration) if duration > 0 else None
            topics.append(
                BagTopicStats(
                    name=name,
                    message_type=msg_type,
                    message_count=count,
                    kind=classify_bag_topic(name),
                    frequency_hz=freq,
                    frequency_basis="bag_duration" if freq is not None else None,
                )
            )

    return BagAnalysis(
        path=fallback_path,
        storage_format=storage,
        duration_s=duration,
        message_count=msg_count,
        topics=topics,
        anomalies=[],
        mode_effective=mode_effective,
    )
