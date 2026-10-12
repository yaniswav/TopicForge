"""Node fixtures of the mock robot (`list_nodes`, `get_node_info`).

The nodes are the ones that appear as publishers and subscribers in `fixtures.MOCK_TOPICS`,
so the topic and node views agree. Parameters go through the same masking and cutting
as live ones: `camera_driver` carries a secret and `robot_state_publisher` a long
robot description, so a mock call shows both behaviours.
"""

from __future__ import annotations

from topicforge.adapters.common.nodes import (
    build_node_info,
    build_node_listing,
    build_parameters,
    unknown_node_error,
)
from topicforge.adapters.ros2_mock.fixtures import MOCK_TOPICS
from topicforge.models import NodeInfo, NodeInterface, NodeListing

_PARAM_SERVICES: tuple[tuple[str, str], ...] = (
    ("describe_parameters", "rcl_interfaces/srv/DescribeParameters"),
    ("get_parameter_types", "rcl_interfaces/srv/GetParameterTypes"),
    ("get_parameters", "rcl_interfaces/srv/GetParameters"),
    ("list_parameters", "rcl_interfaces/srv/ListParameters"),
    ("set_parameters", "rcl_interfaces/srv/SetParameters"),
    ("set_parameters_atomically", "rcl_interfaces/srv/SetParametersAtomically"),
)


def _urdf() -> str:
    """A robot description longer than the parameter size cap, deterministic."""
    links = "".join(
        f'<link name="wheel_{i}"><visual><geometry><cylinder length="0.05" radius="0.1"/>'
        "</geometry></visual></link>"
        for i in range(40)
    )
    return f'<robot name="mock_diffbot"><link name="base_link"/>{links}</robot>'


# `ros__parameters` of each node, as `ros2 param dump` would print them (nested by `.`).
MOCK_NODE_PARAMETERS: dict[str, dict[str, object]] = {
    "/lidar_driver": {"use_sim_time": False, "frame_id": "laser", "scan_frequency": 10.0},
    "/camera_driver": {
        "use_sim_time": False,
        "frame_id": "camera_link",
        "width": 640,
        "height": 480,
        "rtsp": {"user": "viewer", "password": "hunter2"},
    },
    "/imu_driver": {"use_sim_time": False, "frame_id": "imu_link", "rate": 100.0},
    "/base_controller": {
        "use_sim_time": False,
        "wheel_separation": 0.34,
        "wheel_radius": 0.1,
        "cmd_vel_timeout": 0.5,
    },
    "/robot_state_publisher": {
        "use_sim_time": False,
        "publish_frequency": 50.0,
        "robot_description": _urdf(),
    },
    "/nav_planner": {
        "use_sim_time": False,
        "goal_tolerance": 0.25,
        "planner_plugins": ["GridBased"],
    },
}

_ACTION_SERVERS: dict[str, tuple[NodeInterface, ...]] = {
    "/nav_planner": (
        NodeInterface(name="/navigate_to_pose", type="nav2_msgs/action/NavigateToPose"),
    ),
}


def mock_node_names() -> list[str]:
    """Full names of the mock nodes, sorted."""
    return sorted(MOCK_NODE_PARAMETERS)


def mock_node_listing() -> NodeListing:
    """The mock `list_nodes` result."""
    return build_node_listing(mock_node_names(), "mock")


def _interfaces(node: str) -> dict[str, list[NodeInterface]]:
    publishers = [
        NodeInterface(name=t.name, type=t.message_type)
        for t in MOCK_TOPICS
        if node in t.publisher_nodes
    ]
    subscribers = [
        NodeInterface(name=t.name, type=t.message_type)
        for t in MOCK_TOPICS
        if node in t.subscriber_nodes
    ]
    event = NodeInterface(name="/parameter_events", type="rcl_interfaces/msg/ParameterEvent")
    log = NodeInterface(name="/rosout", type="rcl_interfaces/msg/Log")
    return {
        "publishers": sorted([*publishers, event, log], key=lambda i: i.name),
        "subscribers": sorted([*subscribers, event], key=lambda i: i.name),
        "service_servers": [
            NodeInterface(name=f"{node}/{name}", type=type_) for name, type_ in _PARAM_SERVICES
        ],
        "action_servers": list(_ACTION_SERVERS.get(node, ())),
    }


def mock_node_info(node: str) -> NodeInfo:
    """Detail of one mock node; an unknown name raises like the live adapter."""
    if node not in MOCK_NODE_PARAMETERS:
        raise unknown_node_error(node, mock_node_names())
    parameters, note = build_parameters(MOCK_NODE_PARAMETERS[node])
    return build_node_info(
        node,
        _interfaces(node),
        parameters=parameters,
        parameters_note=note,
        duplicate_count=1,
        mode="mock",
    )
