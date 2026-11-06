"""`get_node_info` and the `robot_description` read for the live adapter.

Wired like `tf_capture`: the adapter hands in the callables that start `ros2` processes
(`run`, `exec_process`) and the node listing, so this module starts no process itself and
never imports the adapter.
"""

from __future__ import annotations

from collections.abc import Callable

from topicforge import budget as call_budget
from topicforge.adapters.base import AdapterError, EffectiveMode
from topicforge.adapters.common.nodes import (
    build_node_info,
    build_parameters,
    offers_parameter_services,
    unknown_node_error,
)
from topicforge.adapters.ros2_live.node_parsers import (
    is_node_not_found,
    parse_node_info,
    parse_param_dump,
)
from topicforge.adapters.ros2_live.process_runner import ProcessResult
from topicforge.models import NodeInfo, NodeParameter
from topicforge.models.nodes import ParametersStatus

# Longest `ros2 param dump` output kept (1 MiB): a robot description can be that large.
_PARAM_DUMP_MAX_BYTES = 1024 * 1024
# Time `get_node_info` keeps back from the call budget for killing a stuck `ros2 param`.
_PARAM_STOP_RESERVE_SEC = 2.0
# Shortest dump wait worth starting: below it a healthy node may not answer in time, and a
# cut-short read would pass for a blocked executor. Shorter waits report `no_budget`.
_PARAM_MIN_WAIT_SEC = 2.0
# Node whose `robot_description` parameter `inspect_tf` reads when the topic is absent.
_URDF_NODE_BASENAME = "robot_state_publisher"

NodeNamesFn = Callable[[], list[str]]
RunFn = Callable[[list[str]], str]
ExecFn = Callable[..., tuple[ProcessResult, float]]


def get_node_info(
    node: str,
    timeout_s: float,
    *,
    exe: str,
    node_names: NodeNamesFn,
    run: RunFn,
    exec_process: ExecFn,
    mode: EffectiveMode,
) -> NodeInfo:
    """Interfaces and parameters of one node.

    `exe` is the `ros2` executable name, `node_names()` lists every node (duplicates
    kept), `run(cmd)` returns the stdout of a graph query and `exec_process(cmd, timeout,
    max_output_bytes=...)` returns `(ProcessResult, seconds allowed)` and leaves a
    deadline or a non-zero exit in the result.

    Three steps: `ros2 node list` (the node must exist; duplicates are counted),
    `ros2 node info` (a graph query that never waits for the node), then
    `ros2 param dump` bounded by `timeout_s` and the call budget. The dump prints
    YAML on stdout and is never given an output option, so nothing is written to
    disk. A node that does not answer in time yields `parameters` `None` with a
    note: a diagnosis (its executor is probably blocked), not an error.
    """
    names = node_names()
    if node not in names:
        raise unknown_node_error(node, sorted(set(names)))
    out = run([exe, "node", "info", node])
    if is_node_not_found(out):
        raise unknown_node_error(node, sorted(set(names)))
    interfaces = parse_node_info(out)
    if interfaces is None:
        raise AdapterError(
            f"Could not read the interfaces of node {node!r}: unrecognized `ros2 node info` output."
        )
    parameters, parameters_note, status = None, None, None
    if offers_parameter_services(node, interfaces["service_servers"]):
        parameters, parameters_note, status = _read_parameters(
            node, timeout_s, exe=exe, exec_process=exec_process
        )
    else:
        status = "no_parameter_services"
        parameters_note = (
            "The node does not offer parameter services (no "
            f"{node}/list_parameters), so its parameters cannot be read."
        )
    return build_node_info(
        node,
        interfaces,
        parameters=parameters,
        parameters_note=parameters_note,
        duplicate_count=names.count(node),
        mode=mode,
        parameters_status=status,
    )


def robot_description_parameter(
    timeout_s: float, *, exe: str, node_names: NodeNamesFn, exec_process: ExecFn
) -> tuple[str | None, str]:
    """The `robot_description` parameter of the robot_state_publisher node, read-only.

    Outside the adapter protocol: `inspect_tf` falls back to it when no node publishes
    `/robot_description`. The node is the first one named `robot_state_publisher` in
    any namespace; its parameters are read with the same `ros2 param dump` as
    `get_node_info` (a parameter read, the only request TopicForge sends to a node).
    Returns `(urdf_text, node)` or `(None, why)`; never raises for a missing node.
    """
    names = sorted(set(node_names()))
    holders = [n for n in names if n.rsplit("/", 1)[-1] == _URDF_NODE_BASENAME]
    if not holders:
        return None, f"no {_URDF_NODE_BASENAME} node is running"
    node = holders[0]
    wait = call_budget.clamp(timeout_s, _PARAM_STOP_RESERVE_SEC)
    if wait <= 0:
        return None, f"no time was left to read the parameters of {node}"
    result, shown = exec_process(
        [exe, "param", "dump", node], wait, max_output_bytes=_PARAM_DUMP_MAX_BYTES
    )
    if result.timed_out:
        return None, f"{node} did not answer its parameter read within {shown:g} s"
    if result.returncode != 0 or result.truncated:
        return None, f"the parameters of {node} could not be read"
    raw = parse_param_dump(result.stdout)
    text = None if raw is None else raw.get("robot_description")
    if not isinstance(text, str) or not text.strip():
        return None, f"{node} has no robot_description parameter"
    return text, node


def _read_parameters(
    node: str, timeout_s: float, *, exe: str, exec_process: ExecFn
) -> tuple[list[NodeParameter] | None, str | None, ParametersStatus | None]:
    """`(parameters, note, status)` from `ros2 param dump <node>`.

    Unreadable gives `(None, why, status)`: `no_budget` (nothing left to wait with),
    `timeout` (the only real non-answer), or `failed` (exit, size or format).
    """
    wait = call_budget.clamp(timeout_s, _PARAM_STOP_RESERVE_SEC)
    if wait <= 0 or wait < min(_PARAM_MIN_WAIT_SEC, timeout_s):
        return (
            None,
            (
                "Not enough time was left in this call's budget to read the parameters (the "
                "call waited for the ROS lock or for the graph queries): retry."
            ),
            "no_budget",
        )
    result, shown = exec_process(
        [exe, "param", "dump", node],
        wait,
        max_output_bytes=_PARAM_DUMP_MAX_BYTES,
    )
    if result.timed_out:
        return (
            None,
            (
                "The node announced its parameter services but did not answer within "
                f"{shown:g} s: its executor is probably blocked."
            ),
            "timeout",
        )
    if result.returncode != 0:
        tail = [ln for ln in result.stderr.strip().splitlines() if ln][-1:]
        return (
            None,
            (
                f"`ros2 param dump` failed (exit {result.returncode}): "
                f"{tail[0] if tail else 'no stderr'}"
            ),
            "failed",
        )
    if result.truncated:
        return None, "The parameter dump is larger than 1 MiB and was not read.", "failed"
    raw = parse_param_dump(result.stdout)
    if raw is None:
        return (
            None,
            "`ros2 param dump` printed something that is not a parameter document.",
            "failed",
        )
    parameters, note = build_parameters(raw)
    return parameters, note, None
