"""Result models of `list_nodes` and `get_node_info`.

A node is a process-level ROS 2 participant: it has a fully qualified name, a set
of topics, services and actions, and parameters. `NodeListing` is the cheap view
(names only, from one `ros2 node list` call); `NodeInfo` is the detail view of
one node.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

_CONFIG = ConfigDict(extra="forbid", frozen=True)

_MODE_EFFECTIVE_DESC = (
    "Runtime mode the adapter served this response in: `live` (real ROS2 "
    "introspection) or `mock` (deterministic fixtures). Lets a caller tell a "
    "real graph from a demo one without calling `health_check`."
)
_NOTE_DESC = "One sentence of context for the result. `None` means nothing to add."


class NodeListItem(BaseModel):
    """One node name on the ROS 2 graph."""

    model_config = _CONFIG

    name: str = Field(description="Node name without its namespace, e.g. `lidar_driver`.")
    namespace: str = Field(
        description="Namespace of the node: `/` for the root namespace, else e.g. `/robot1`."
    )
    full_name: str = Field(
        description="Fully qualified node name, e.g. `/lidar_driver` or `/robot1/lidar_driver`."
    )
    duplicate_count: int = Field(
        ge=1,
        description=(
            "How many nodes on the graph use this exact full name. 1 is normal; more than 1 "
            "means several processes share the name, and any tool that addresses the node by "
            "name answers for one of them."
        ),
    )


class NodeListing(BaseModel):
    """Envelope returned by `list_nodes`."""

    model_config = _CONFIG

    nodes: list[NodeListItem] = Field(
        description="Nodes on the graph, one entry per distinct full name, sorted by name."
    )
    returned: int = Field(ge=0, description="Length of `nodes`.")
    total: int = Field(ge=0, description="Distinct node names on the graph (no cap applies).")
    truncated: bool = Field(
        description="True when `returned` is less than `total` because of a cap."
    )
    duplicates: list[str] = Field(
        description=(
            "Full names that appear more than once on the graph. Empty when every name is unique."
        )
    )
    mode_effective: Literal["mock", "live"] = Field(description=_MODE_EFFECTIVE_DESC)
    note: str | None = Field(default=None, description=_NOTE_DESC)


class NodeInterface(BaseModel):
    """One topic, service or action a node uses, with its type."""

    model_config = _CONFIG

    name: str = Field(description="Fully qualified topic, service or action name.")
    type: str = Field(
        description=(
            "Interface type, e.g. `sensor_msgs/msg/LaserScan`, `std_srvs/srv/Empty` or "
            "`nav2_msgs/action/NavigateToPose`."
        )
    )


class NodeParameter(BaseModel):
    """One parameter of a node, as `ros2 param dump` printed it."""

    model_config = _CONFIG

    name: str = Field(
        description=(
            "Parameter name, with `.` between levels (`qos_overrides./scan.publisher.depth`)."
        )
    )
    value: JsonValue = Field(
        description=(
            "Parameter value: boolean, integer, float, string or a list of those. A masked "
            "value is the string `<masked>`; a cut value holds its first elements or "
            "characters (see `truncated`)."
        )
    )
    masked: bool = Field(
        description=(
            "True when the name looks like a secret (it contains password, secret, token, "
            "api_key or credential) and the value was replaced by `<masked>`."
        )
    )
    truncated: bool = Field(
        description="True when `value` was cut because it was too long (an URDF, a long list)."
    )
    original_size: int | None = Field(
        default=None,
        description=(
            "Characters (string) or elements (list) the value had before the cut. `None` "
            "unless `truncated` is true."
        ),
    )


class NodeInfo(BaseModel):
    """Detail of one node: its interfaces, its parameters and `use_sim_time`."""

    model_config = _CONFIG

    full_name: str = Field(description="Fully qualified node name, e.g. `/lidar_driver`.")
    publishers: list[NodeInterface] = Field(description="Topics the node publishes.")
    subscribers: list[NodeInterface] = Field(description="Topics the node subscribes to.")
    service_servers: list[NodeInterface] = Field(
        description="Services the node offers, its own parameter services included."
    )
    service_clients: list[NodeInterface] = Field(description="Services the node calls.")
    action_servers: list[NodeInterface] = Field(description="Actions the node serves.")
    action_clients: list[NodeInterface] = Field(description="Actions the node calls.")
    parameters: list[NodeParameter] | None = Field(
        default=None,
        description=(
            "Parameters of the node, sorted by name. `None` when they could not be read: see "
            "`parameters_note`. An empty list means the node answered and has no parameter."
        ),
    )
    parameters_note: str | None = Field(
        default=None,
        description=(
            "Why `parameters` is `None` (the node did not answer in time, or offers no "
            "parameter service), or what was masked or cut when it is set. `None` when "
            "the list is complete."
        ),
    )
    use_sim_time: bool | None = Field(
        default=None,
        description=(
            "Value of the node's `use_sim_time` parameter. `None` when unknown: see "
            "`use_sim_time_note`."
        ),
    )
    use_sim_time_note: str | None = Field(
        default=None, description="Why `use_sim_time` is `None`. `None` when it is set."
    )
    duplicate_count: int = Field(
        ge=1,
        description=(
            "How many nodes on the graph use this exact full name. More than 1 means the "
            "interfaces and parameters above belong to one of them, unidentified."
        ),
    )
    mode_effective: Literal["mock", "live"] = Field(description=_MODE_EFFECTIVE_DESC)
    note: str | None = Field(default=None, description=_NOTE_DESC)


__all__ = ["NodeInfo", "NodeInterface", "NodeListItem", "NodeListing", "NodeParameter"]
