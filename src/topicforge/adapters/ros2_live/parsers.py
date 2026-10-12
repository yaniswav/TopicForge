"""Pure parsers for `ros2 topic list -v` and the QoS block of `ros2 topic info --verbose`.

Module-level functions over CLI text, tested without ROS 2. The older parsers
(`parse_topic_list`, `parse_topic_info`, ...) live in `adapter.py`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from topicforge.models import SideQos

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
