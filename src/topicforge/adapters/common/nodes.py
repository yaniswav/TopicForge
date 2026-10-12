"""Node listing and node detail logic shared by the live and mock adapters.

Pure functions, no `ros2` and no DDS binding. The live adapter feeds them the
output of its parsers, the mock adapter feeds them fixtures, so both go through
the same masking, cutting and `use_sim_time` extraction.

Parameters are the one sensitive surface of `get_node_info`: a launch file can
put a password or a token in a parameter, and a robot description can be a
megabyte. `build_parameters` masks the first and cuts the second.
"""

from __future__ import annotations

import difflib
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import JsonValue

from topicforge.adapters.base import AdapterError
from topicforge.constants import DEFAULT_MAX_ARRAY_LENGTH
from topicforge.models import NodeInfo, NodeInterface, NodeListing, NodeListItem, NodeParameter

# Longest string parameter returned in full; a robot description is cut here.
MAX_PARAM_VALUE_CHARS = 2048
# Parameters returned for one node; a node with more reports the rest as cut.
MAX_PARAMETERS = 500
# Marker that replaces a masked value.
MASKED_VALUE = "<masked>"

# A parameter whose dotted name contains one of these words is a secret.
_SECRET_NAME = re.compile(r"password|secret|token|api[_-]?key|credential", re.IGNORECASE)

Mode = Literal["mock", "live"]


def split_full_name(full_name: str) -> tuple[str, str]:
    """`(name, namespace)` of a fully qualified node name: `/a/b` gives `("b", "/a")`."""
    namespace, _, name = full_name.rpartition("/")
    return name, namespace or "/"


def build_node_listing(full_names: Sequence[str], mode: Mode) -> NodeListing:
    """The `list_nodes` result for the names `ros2 node list` printed (duplicates included)."""
    counts = Counter(full_names)
    nodes = []
    for full_name in sorted(counts):
        name, namespace = split_full_name(full_name)
        nodes.append(
            NodeListItem(
                name=name,
                namespace=namespace,
                full_name=full_name,
                duplicate_count=counts[full_name],
            )
        )
    duplicates = [n.full_name for n in nodes if n.duplicate_count > 1]
    return NodeListing(
        nodes=nodes,
        returned=len(nodes),
        total=len(nodes),
        truncated=False,
        duplicates=duplicates,
        mode_effective=mode,
        note=_listing_note(len(nodes), duplicates),
    )


def _listing_note(total: int, duplicates: list[str]) -> str | None:
    if total == 0:
        return (
            "No node is visible on the graph. Nodes whose name starts with an underscore are "
            "hidden, and a node on another ROS_DOMAIN_ID is invisible."
        )
    if duplicates:
        shown = ", ".join(f"`{d}`" for d in duplicates[:5])
        return (
            f"{len(duplicates)} node name(s) are used by more than one node ({shown}): the "
            "ROS 2 CLI answers for one of them without saying which."
        )
    return None


def unknown_node_error(node: str, known: Sequence[str]) -> AdapterError:
    """The error for a node that is not on the graph, with the closest names that are."""
    base = node.rsplit("/", 1)[-1]
    by_base = [k for k in known if k.rsplit("/", 1)[-1] == base]
    close = list(dict.fromkeys([*by_base, *difflib.get_close_matches(node, known, n=3)]))[:3]
    hint = f" Close matches: {', '.join(close)}." if close else ""
    return AdapterError(
        f"Node '{node}' is not on the ROS 2 graph.{hint} Run list_nodes to see what exists "
        "(nodes whose name starts with an underscore are hidden)."
    )


def _json_safe(value: object) -> JsonValue:
    """A YAML value as JSON: non-finite floats become strings, unknown types their text."""
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else ("nan" if math.isnan(value) else _inf(value))
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return str(value)


def _inf(value: float) -> str:
    return "inf" if value > 0 else "-inf"


def _flatten(raw: Mapping[str, object], prefix: str = "") -> list[tuple[str, object]]:
    """Leaves of a nested parameter mapping, named with `.` between levels."""
    leaves: list[tuple[str, object]] = []
    for key, value in raw.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, Mapping):
            leaves.extend(_flatten(value, name))
        else:
            leaves.append((name, value))
    return leaves


def _cut(value: JsonValue) -> tuple[JsonValue, int | None]:
    """`value` limited to the size caps, and its original size when it was cut."""
    if isinstance(value, str) and len(value) > MAX_PARAM_VALUE_CHARS:
        return value[:MAX_PARAM_VALUE_CHARS], len(value)
    if isinstance(value, list) and len(value) > DEFAULT_MAX_ARRAY_LENGTH:
        return value[:DEFAULT_MAX_ARRAY_LENGTH], len(value)
    return value, None


def build_parameters(raw: Mapping[str, object]) -> tuple[list[NodeParameter], str | None]:
    """Parameters of a node from the parsed `ros__parameters` mapping, plus a note.

    Names are flattened with `.`, sorted, masked when they look like a secret and cut
    when the value is too long. The note says how many were masked or cut, `None`
    when nothing was.
    """
    leaves = sorted(_flatten(raw), key=lambda leaf: leaf[0])
    params: list[NodeParameter] = []
    masked = cut = 0
    for name, value in leaves[:MAX_PARAMETERS]:
        if _SECRET_NAME.search(name):
            params.append(
                NodeParameter(name=name, value=MASKED_VALUE, masked=True, truncated=False)
            )
            masked += 1
            continue
        shown, original = _cut(_json_safe(value))
        cut += original is not None
        params.append(
            NodeParameter(
                name=name,
                value=shown,
                masked=False,
                truncated=original is not None,
                original_size=original,
            )
        )
    parts: list[str] = []
    if masked:
        parts.append(
            f"{masked} value(s) masked because the name contains password, secret, token, "
            "api_key or credential."
        )
    if cut:
        parts.append(
            f"{cut} value(s) cut at {MAX_PARAM_VALUE_CHARS} characters or "
            f"{DEFAULT_MAX_ARRAY_LENGTH} list elements (see `original_size`)."
        )
    if len(leaves) > MAX_PARAMETERS:
        parts.append(f"Only the first {MAX_PARAMETERS} of {len(leaves)} parameters are listed.")
    return params, " ".join(parts) or None


def use_sim_time_of(parameters: list[NodeParameter] | None) -> tuple[bool | None, str | None]:
    """`(value, None)` when the node reports a boolean `use_sim_time`, else `(None, note)`."""
    if parameters is None:
        return (
            None,
            "The parameters were not read, so use_sim_time is unknown: see parameters_note.",
        )
    for p in parameters:
        if p.name == "use_sim_time":
            if isinstance(p.value, bool):
                return p.value, None
            return None, f"use_sim_time is {p.value!r}, not a boolean."
    return None, "The node does not declare a use_sim_time parameter."


def _duplicate_note(full_name: str, duplicate_count: int) -> str | None:
    if duplicate_count < 2:
        return None
    return (
        f"{duplicate_count} nodes share the name {full_name}: the interfaces and parameters "
        "below belong to one of them, and the ROS 2 CLI does not say which."
    )


def build_node_info(
    full_name: str,
    interfaces: Mapping[str, list[NodeInterface]],
    *,
    parameters: list[NodeParameter] | None,
    parameters_note: str | None,
    duplicate_count: int,
    mode: Mode,
) -> NodeInfo:
    """Assemble the `get_node_info` result; `interfaces` maps section keys to their entries."""
    sim_time, sim_time_note = use_sim_time_of(parameters)
    return NodeInfo(
        full_name=full_name,
        publishers=interfaces.get("publishers", []),
        subscribers=interfaces.get("subscribers", []),
        service_servers=interfaces.get("service_servers", []),
        service_clients=interfaces.get("service_clients", []),
        action_servers=interfaces.get("action_servers", []),
        action_clients=interfaces.get("action_clients", []),
        parameters=parameters,
        parameters_note=parameters_note,
        use_sim_time=sim_time,
        use_sim_time_note=sim_time_note,
        duplicate_count=duplicate_count,
        mode_effective=mode,
        note=_duplicate_note(full_name, duplicate_count),
    )


def offers_parameter_services(full_name: str, service_servers: Sequence[NodeInterface]) -> bool:
    """True when the node announced its parameter services (`<node>/list_parameters`)."""
    return any(s.name == f"{full_name}/list_parameters" for s in service_servers)


__all__ = [
    "MASKED_VALUE",
    "MAX_PARAMETERS",
    "MAX_PARAM_VALUE_CHARS",
    "build_node_info",
    "build_node_listing",
    "build_parameters",
    "offers_parameter_services",
    "split_full_name",
    "unknown_node_error",
    "use_sim_time_of",
]
