"""Pure parsers for `ros2 node list`, `ros2 node info` and `ros2 param dump`.

Module-level functions over CLI text, tested without ROS 2 on captured outputs
(`tests/fixtures/ros2_node/`).
"""

from __future__ import annotations

import re

import yaml

from topicforge.models import NodeInterface

_SECTION_KEYS = {
    "Subscribers": "subscribers",
    "Publishers": "publishers",
    "Service Servers": "service_servers",
    "Service Clients": "service_clients",
    "Action Servers": "action_servers",
    "Action Clients": "action_clients",
}
_SECTION = re.compile(
    r"^\s*(Subscribers|Publishers|Service Servers|Service Clients|Action Servers|Action Clients):\s*$"
)
_ENTRY = re.compile(r"^\s+(/?[^\s:]+):\s+(\S.*?)\s*$")
_PARAMETERS_KEY = "ros__parameters"


def parse_node_list(stdout: str) -> list[str]:
    """Fully qualified node names printed by `ros2 node list`, in order, duplicates kept.

    Lines that are not a name (the duplicate-name warning some distros print, blank
    lines) are skipped.
    """
    names: list[str] = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if line.startswith("/") and " " not in line:
            names.append(line)
    return names


def parse_node_info(stdout: str) -> dict[str, list[NodeInterface]] | None:
    """Parse `ros2 node info <node>` into `{section: [NodeInterface]}`.

    Keys are `subscribers`, `publishers`, `service_servers`, `service_clients`,
    `action_servers` and `action_clients`; a section the distro does not print (actions
    on old releases) or that is empty gives an empty list. Returns `None` when no
    section header is found, so the caller can report an unreadable output.
    """
    sections: dict[str, list[NodeInterface]] = {key: [] for key in _SECTION_KEYS.values()}
    current: list[NodeInterface] | None = None
    seen = False
    for line in stdout.splitlines():
        header = _SECTION.match(line)
        if header:
            seen = True
            current = sections[_SECTION_KEYS[header.group(1)]]
            continue
        entry = _ENTRY.match(line)
        if entry and current is not None:
            current.append(NodeInterface(name=entry.group(1), type=entry.group(2)))
    return sections if seen else None


def is_node_not_found(text: str) -> bool:
    """True when CLI output says the node does not exist (`Unable to find node '/x'`)."""
    return "unable to find node" in text.lower()


def parse_param_dump(stdout: str) -> dict[str, object] | None:
    """Parse the YAML of `ros2 param dump <node>` into the `ros__parameters` mapping.

    The CLI prints `<node>:\\n  ros__parameters:\\n    name: value`. The node key is not
    checked (distros differ on the leading slash). Returns an empty mapping when the
    node has no parameter, and `None` when the text is not that document.
    """
    try:
        document = yaml.safe_load(stdout)
    except yaml.YAMLError:
        return None
    if document is None:
        return {}
    if not isinstance(document, dict) or len(document) != 1:
        return None
    (body,) = document.values()
    if not isinstance(body, dict):
        return None
    params = body.get(_PARAMETERS_KEY)
    if params is None:
        return {} if _PARAMETERS_KEY in body or not body else None
    return params if isinstance(params, dict) else None


__all__ = ["is_node_not_found", "parse_node_info", "parse_node_list", "parse_param_dump"]
