"""Pure parsers for `ros2 node list`, `ros2 node info` and `ros2 param dump`.

The fixtures under `tests/fixtures/ros2_node/` are written in the format the Humble and
Jazzy CLIs print (no ROS 2 needed).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from topicforge.adapters.ros2_live.node_parsers import (
    is_node_not_found,
    parse_node_info,
    parse_node_list,
    parse_param_dump,
)

FIXTURES = Path(__file__).parent / "fixtures" / "ros2_node"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_node_list_keeps_namespaces_and_duplicates_in_order() -> None:
    assert parse_node_list(_read("node_list_duplicates.txt")) == [
        "/camera/camera_driver",
        "/lidar_driver",
        "/lidar_driver",
        "/nav2/controller_server",
        "/nav_planner",
        "/robot1/base_controller",
    ]


def test_node_list_skips_the_duplicate_warning_and_blank_lines() -> None:
    assert parse_node_list(_read("node_list_warning_stdout.txt")) == [
        "/lidar_driver",
        "/lidar_driver",
        "/nav_planner",
    ]


def test_node_list_of_an_empty_graph_is_empty() -> None:
    assert parse_node_list(_read("node_list_empty.txt")) == []
    assert parse_node_list("\n\n") == []


def test_node_info_reads_all_six_sections() -> None:
    sections = parse_node_info(_read("node_info_humble.txt"))
    assert sections is not None
    assert [(i.name, i.type) for i in sections["subscribers"]] == [
        ("/odom", "nav_msgs/msg/Odometry"),
        ("/parameter_events", "rcl_interfaces/msg/ParameterEvent"),
        ("/scan", "sensor_msgs/msg/LaserScan"),
        ("/tf", "tf2_msgs/msg/TFMessage"),
    ]
    assert [i.name for i in sections["publishers"]] == [
        "/cmd_vel",
        "/parameter_events",
        "/rosout",
    ]
    assert len(sections["service_servers"]) == 6
    assert sections["service_servers"][3].name == "/nav_planner/list_parameters"
    assert [(i.name, i.type) for i in sections["service_clients"]] == [
        ("/map_server/load_map", "nav2_msgs/srv/LoadMap")
    ]
    assert [(i.name, i.type) for i in sections["action_servers"]] == [
        ("/navigate_to_pose", "nav2_msgs/action/NavigateToPose")
    ]
    assert sections["action_clients"] == []


def test_node_info_with_empty_sections_gives_empty_lists() -> None:
    sections = parse_node_info(_read("node_info_no_param_services.txt"))
    assert sections is not None
    assert [i.name for i in sections["publishers"]] == ["/rosout"]
    assert sections["subscribers"] == []
    assert sections["service_servers"] == []


def test_node_info_without_action_sections_is_still_read() -> None:
    """Old distros print no action sections: they read as empty."""
    text = "/talker\n  Subscribers:\n    /a: std_msgs/msg/String\n  Publishers:\n"
    sections = parse_node_info(text)
    assert sections is not None
    assert sections["action_servers"] == [] and sections["action_clients"] == []
    assert [i.name for i in sections["subscribers"]] == ["/a"]


@pytest.mark.parametrize("text", ["", "garbage\n", "Unable to find node '/nope'\n"])
def test_node_info_of_an_unrecognised_output_is_none(text: str) -> None:
    assert parse_node_info(text) is None


def test_not_found_detection() -> None:
    assert is_node_not_found(_read("node_info_not_found.txt"))
    assert not is_node_not_found(_read("node_info_humble.txt"))


def test_param_dump_reads_the_ros_parameters_mapping() -> None:
    params = parse_param_dump(_read("param_dump_humble.yaml"))
    assert params is not None
    assert params["use_sim_time"] is False
    assert params["goal_tolerance"] == 0.25
    assert params["planner_plugins"] == ["GridBased"]
    assert params["qos_overrides"] == {
        "/scan": {"subscription": {"depth": 5, "reliability": "best_effort"}}
    }


def test_param_dump_without_a_leading_slash_on_the_node_key() -> None:
    assert parse_param_dump("talker:\n  ros__parameters:\n    a: 1\n") == {"a": 1}


def test_param_dump_of_a_node_without_parameters_is_empty() -> None:
    assert parse_param_dump("") == {}
    assert parse_param_dump("/n:\n  ros__parameters: {}\n") == {}


@pytest.mark.parametrize(
    "text",
    [
        "just text, not a document",
        "- a\n- b\n",
        "/a:\n  ros__parameters:\n    x: 1\n/b:\n  ros__parameters:\n    y: 2\n",
        "/a:\n  other: 1\n",
        "/a:\n  ros__parameters: [1, 2]\n",
        "a: [unclosed\n",
    ],
)
def test_param_dump_of_anything_else_is_none(text: str) -> None:
    assert parse_param_dump(text) is None
