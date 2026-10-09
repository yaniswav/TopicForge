"""`list_nodes` and `get_node_info`: live adapter with a stubbed runner, mock, MCP layer.

The live tests stub `run_process`, so no ROS 2 is needed. The bench in
`tests/integration/ros2/` runs the same calls against a real graph.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from topicforge import budget
from topicforge.adapters.base import AdapterError, MiddlewareAdapter
from topicforge.adapters.composite import CompositeAdapter
from topicforge.adapters.ros2_live.adapter import Ros2CliAdapter
from topicforge.adapters.ros2_live.process_runner import ProcessResult
from topicforge.adapters.ros2_mock import MockAdapter
from topicforge.config import Settings
from topicforge.server import build_app

FIXTURES = Path(__file__).parent / "fixtures" / "ros2_node"
_MODULE = "topicforge.adapters.ros2_live.adapter"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _ok(stdout: str, **kw: Any) -> ProcessResult:
    return ProcessResult(
        returncode=0, stdout=stdout, stderr="", timed_out=False, truncated=False, **kw
    )


TIMED_OUT = ProcessResult(returncode=None, stdout="", stderr="", timed_out=True, truncated=False)


class FakeCli:
    """A scripted `run_process`: answers by sub-command and records every call."""

    def __init__(self, replies: dict[str, ProcessResult]) -> None:
        self.replies = replies
        self.calls: list[tuple[list[str], float, dict[str, Any]]] = []

    def __call__(self, cmd: list[str], *, deadline_s: float, **kwargs: Any) -> ProcessResult:
        self.calls.append((cmd, deadline_s, kwargs))
        return self.replies[" ".join(cmd[1:3])]

    def ran(self, verb: str) -> list[list[str]]:
        return [c for c, _, _ in self.calls if " ".join(c[1:3]) == verb]


@pytest.fixture
def cli(monkeypatch: pytest.MonkeyPatch) -> FakeCli:
    fake = FakeCli(
        {
            "node list": _ok(_read("node_list_plain.txt")),
            "node info": _ok(_read("node_info_humble.txt")),
            "param dump": _ok(_read("param_dump_humble.yaml")),
        }
    )
    monkeypatch.setattr(f"{_MODULE}.shutil.which", lambda name: f"/fake/bin/{name}")
    monkeypatch.setattr(f"{_MODULE}.run_process", fake)
    return fake


def _info(adapter: Ros2CliAdapter, node: str = "/nav_planner", timeout_s: float = 8.0) -> Any:
    return adapter.get_node_info(node, timeout_s)


# ----- list_nodes -----


def test_list_nodes_is_one_cli_call(cli: FakeCli) -> None:
    cli.replies["node list"] = _ok(_read("node_list_duplicates.txt"))
    listing = Ros2CliAdapter().list_nodes()
    assert [c[1:] for c in (call[0] for call in cli.calls)] == [["node", "list"]]
    assert listing.mode_effective == "live"
    assert listing.duplicates == ["/lidar_driver"]
    assert {n.full_name: n.namespace for n in listing.nodes}["/robot1/base_controller"] == "/robot1"


def test_list_nodes_failure_is_an_adapter_error(cli: FakeCli) -> None:
    cli.replies["node list"] = ProcessResult(
        returncode=1, stdout="", stderr="boom\n", timed_out=False, truncated=False
    )
    with pytest.raises(AdapterError, match="failed"):
        Ros2CliAdapter().list_nodes()


# ----- get_node_info -----


def test_get_node_info_assembles_graph_and_parameters(cli: FakeCli) -> None:
    info = _info(Ros2CliAdapter())
    assert info.full_name == "/nav_planner" and info.mode_effective == "live"
    assert [i.name for i in info.publishers] == ["/cmd_vel", "/parameter_events", "/rosout"]
    assert info.action_servers[0].type == "nav2_msgs/action/NavigateToPose"
    assert info.parameters is not None and info.parameters_note is None
    assert info.parameters[0].name == "goal_tolerance"
    assert info.use_sim_time is False and info.use_sim_time_note is None
    assert info.duplicate_count == 1 and info.note is None


def test_param_dump_never_gets_an_output_option(cli: FakeCli) -> None:
    _info(Ros2CliAdapter())
    (cmd,) = cli.ran("param dump")
    assert cmd[1:] == ["param", "dump", "/nav_planner"]
    for call, _, _ in cli.calls:
        assert not any(a.startswith("--output") or a == "--print" for a in call)


def test_param_dump_output_is_capped_at_one_mebibyte(cli: FakeCli) -> None:
    _info(Ros2CliAdapter())
    dump = next(kw for c, _, kw in cli.calls if c[2:3] == ["dump"])
    assert dump["max_output_bytes"] == 1024 * 1024


def test_a_param_timeout_is_a_diagnosis_not_an_error(cli: FakeCli) -> None:
    cli.replies["param dump"] = TIMED_OUT
    info = _info(Ros2CliAdapter(), timeout_s=3)
    assert info.parameters is None
    assert info.parameters_note == (
        "The node announced its parameter services but did not answer within 3 s: "
        "its executor is probably blocked."
    )
    assert info.use_sim_time is None
    assert info.use_sim_time_note is not None and "parameters_note" in info.use_sim_time_note
    # The graph part is still there.
    assert info.subscribers[0].name == "/odom"


def test_the_param_read_gets_timeout_s_not_more(cli: FakeCli) -> None:
    _info(Ros2CliAdapter(), timeout_s=5)
    deadline = next(d for c, d, _ in cli.calls if c[2:3] == ["dump"])
    assert deadline == 5
    node_info_deadline = next(d for c, d, _ in cli.calls if c[2:3] == ["info"])
    assert node_info_deadline == 8


def test_the_param_read_is_shortened_by_what_the_call_already_spent(cli: FakeCli) -> None:
    """A call that waited for the lock has less time: the dump gets the remainder minus a reserve."""
    with budget.deadline_at(time.monotonic() + 6.0):
        _info(Ros2CliAdapter(), timeout_s=20)
    deadline = next(d for c, d, _ in cli.calls if c[2:3] == ["dump"])
    assert 2.0 < deadline < 4.1


def test_no_budget_left_for_the_param_read_is_said_plainly(cli: FakeCli) -> None:
    with budget.deadline_at(time.monotonic() + 1.5):
        info = _info(Ros2CliAdapter(), timeout_s=20)
    assert info.parameters is None
    assert info.parameters_note is not None and "No time was left" in info.parameters_note
    assert "executor" not in info.parameters_note  # not a diagnosis of the node
    assert cli.ran("param dump") == []  # nothing was started with no time to run


def test_unknown_node_lists_close_matches(cli: FakeCli) -> None:
    with pytest.raises(AdapterError) as exc:
        _info(Ros2CliAdapter(), "/nav_plannr")
    assert "not on the ROS 2 graph" in str(exc.value) and "/nav_planner" in str(exc.value)
    assert cli.ran("node info") == [] and cli.ran("param dump") == []


def test_a_node_that_vanishes_between_list_and_info_is_not_found(cli: FakeCli) -> None:
    cli.replies["node info"] = _ok(_read("node_info_not_found.txt"))
    with pytest.raises(AdapterError, match="not on the ROS 2 graph"):
        _info(Ros2CliAdapter())


def test_unrecognised_node_info_output_is_an_error(cli: FakeCli) -> None:
    cli.replies["node info"] = _ok("something else\n")
    with pytest.raises(AdapterError, match="unrecognized"):
        _info(Ros2CliAdapter())


def test_duplicate_names_are_flagged(cli: FakeCli) -> None:
    cli.replies["node list"] = _ok(
        _read("node_list_warning_stdout.txt").replace("lidar", "nav_planner_x")
    )
    cli.replies["node list"] = _ok("/nav_planner\n/nav_planner\n/other\n")
    info = _info(Ros2CliAdapter())
    assert info.duplicate_count == 2
    assert info.note is not None and "2 nodes share the name /nav_planner" in info.note


def test_a_node_without_parameter_services_skips_the_dump(cli: FakeCli) -> None:
    cli.replies["node list"] = _ok("/legacy_bridge\n")
    cli.replies["node info"] = _ok(_read("node_info_no_param_services.txt"))
    info = _info(Ros2CliAdapter(), "/legacy_bridge")
    assert cli.ran("param dump") == []
    assert info.parameters is None
    assert (
        info.parameters_note is not None
        and "does not offer parameter services" in info.parameters_note
    )
    assert info.use_sim_time is None and info.use_sim_time_note


def test_secrets_never_leave_the_adapter(cli: FakeCli) -> None:
    cli.replies["param dump"] = _ok(_read("param_dump_secrets.yaml"))
    info = _info(Ros2CliAdapter())
    text = info.model_dump_json()
    assert "hunter2" not in text and "tok-secret-value" not in text and "abcdef123456" not in text
    assert info.parameters_note is not None and "masked" in info.parameters_note
    assert info.use_sim_time is True


def test_a_huge_robot_description_is_cut(cli: FakeCli) -> None:
    urdf = "<robot>" + "link " * 100_000 + "</robot>"
    cli.replies["param dump"] = _ok(
        f"/nav_planner:\n  ros__parameters:\n    robot_description: '{urdf}'\n"
    )
    info = _info(Ros2CliAdapter())
    (p,) = info.parameters
    assert p.truncated and p.original_size == len(urdf)
    assert len(info.model_dump_json()) < 10_000


def test_a_dump_over_the_cap_is_reported_not_parsed(cli: FakeCli) -> None:
    cli.replies["param dump"] = ProcessResult(
        returncode=0,
        stdout="/n:\n  ros__parameters:\n    a: 'xxx",
        stderr="",
        timed_out=False,
        truncated=True,
    )
    info = _info(Ros2CliAdapter())
    assert info.parameters is None
    assert info.parameters_note is not None and "1 MiB" in info.parameters_note


def test_a_failing_param_dump_is_a_note(cli: FakeCli) -> None:
    cli.replies["param dump"] = ProcessResult(
        returncode=1, stdout="", stderr="Node not found\n", timed_out=False, truncated=False
    )
    info = _info(Ros2CliAdapter())
    assert info.parameters is None
    assert info.parameters_note is not None and "exit 1" in info.parameters_note
    assert "Node not found" in info.parameters_note


def test_a_non_yaml_dump_is_a_note(cli: FakeCli) -> None:
    cli.replies["param dump"] = _ok("this is not a parameter document")
    info = _info(Ros2CliAdapter())
    assert info.parameters is None and info.parameters_note


def test_nothing_is_written_to_the_working_directory(
    cli: FakeCli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    adapter = Ros2CliAdapter()
    adapter.list_nodes()
    _info(adapter)
    cli.replies["param dump"] = TIMED_OUT
    _info(adapter)
    assert list(tmp_path.iterdir()) == []


# ----- protocol, composite, mock -----


def test_every_adapter_satisfies_the_protocol() -> None:
    assert isinstance(Ros2CliAdapter(), MiddlewareAdapter)
    assert isinstance(MockAdapter(), MiddlewareAdapter)


def test_composite_routes_both_tools_to_the_ros_half() -> None:
    class Half:
        name = "x"
        effective_mode = "live"

        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[Any, ...]]] = []

        def list_nodes(self) -> str:
            self.calls.append(("list_nodes", ()))
            return "listing"

        def get_node_info(self, node: str, timeout_s: float = 8.0) -> str:
            self.calls.append(("get_node_info", (node, timeout_s)))
            return "info"

    ros, dds = Half(), Half()
    composite = CompositeAdapter(ros, dds)  # type: ignore[arg-type]
    assert composite.list_nodes() == "listing"
    assert composite.get_node_info("/n", 4.0) == "info"
    assert ros.calls == [("list_nodes", ()), ("get_node_info", ("/n", 4.0))]
    assert dds.calls == []


def test_mock_nodes_match_the_mock_topics() -> None:
    mock = MockAdapter()
    names = {n.full_name for n in mock.list_nodes().nodes}
    for topic in mock.list_topics():
        for node in mock.get_topic_info(topic.name).publisher_nodes:
            assert node in names
    scan_publishers = [i.name for i in mock.get_node_info("/lidar_driver").publishers]
    assert "/scan" in scan_publishers


def test_mock_shows_masking_and_cutting() -> None:
    camera = MockAdapter().get_node_info("/camera_driver")
    assert camera.parameters is not None
    password = next(p for p in camera.parameters if p.name == "rtsp.password")
    assert password.masked and password.value == "<masked>"
    assert "hunter2" not in camera.model_dump_json()
    rsp = MockAdapter().get_node_info("/robot_state_publisher")
    urdf = next(p for p in rsp.parameters or [] if p.name == "robot_description")
    assert urdf.truncated and urdf.original_size and urdf.original_size > len(str(urdf.value))
    assert rsp.use_sim_time is False


def test_mock_unknown_node() -> None:
    with pytest.raises(AdapterError, match="Close matches: /nav_planner"):
        MockAdapter().get_node_info("/nav_plannr")


# ----- MCP layer -----


def _app() -> Any:
    return build_app(
        Settings(mode="mock", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False)
    )


def _call(name: str, args: dict[str, Any]) -> Any:
    return asyncio.run(_app().call_tool(name, args))


def test_list_nodes_over_mcp() -> None:
    result = _call("list_nodes", {})
    data = result.structured_content
    assert data["total"] == 6 and data["duplicates"] == [] and data["mode_effective"] == "mock"
    assert json.loads(result.content[0].text) == data


def test_get_node_info_over_mcp_defaults_and_normalises_the_name() -> None:
    data = _call("get_node_info", {"node": "nav_planner"}).structured_content
    assert data["full_name"] == "/nav_planner"
    assert data["action_servers"] == [
        {"name": "/navigate_to_pose", "type": "nav2_msgs/action/NavigateToPose"}
    ]


@pytest.mark.parametrize("node", ["", "  ", "/bad name", "/a//b", "/trailing/", "/x;rm -rf"])
def test_malformed_node_names_are_rejected(node: str) -> None:
    with pytest.raises(ToolError):
        _call("get_node_info", {"node": node})


@pytest.mark.parametrize("timeout_s", [0, 0.5, 21, -1])
def test_timeout_s_is_bounded_1_to_20(timeout_s: float) -> None:
    with pytest.raises(ToolError):
        _call("get_node_info", {"node": "/nav_planner", "timeout_s": timeout_s})


@pytest.mark.parametrize("timeout_s", [1, 8, 20])
def test_timeout_s_in_range_is_accepted(timeout_s: float) -> None:
    assert _call("get_node_info", {"node": "/nav_planner", "timeout_s": timeout_s})


def test_unknown_node_is_an_error_result_with_matches() -> None:
    with pytest.raises(ToolError, match="Close matches: /lidar_driver"):
        _call("get_node_info", {"node": "/lidar_driv"})


def test_both_tools_use_the_ros_lane_and_are_open_world() -> None:
    tools = {t.name: t for t in asyncio.run(_app().list_tools())}
    for name in ("list_nodes", "get_node_info"):
        assert tools[name].annotations.open_world_hint is True
        assert "ROS lock" in tools[name].description
    assert "the only request TopicForge ever sends to a node is a parameter read" in (
        tools["get_node_info"].description
    )
