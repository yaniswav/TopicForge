"""Fault scenarios of the bench robot, for the 0.8.0 agent evaluation (no ROS import, runs anywhere).

`publisher.py --fault <name>` runs the bench robot as a *clean* robot with exactly one fault
(or none, `healthy`). Without `--fault` the publisher is the CI bench, unchanged: that robot
carries on purpose a stale `map -> odom`, a frame with two parents, a URDF link nothing
publishes, wall-stamped topics and a steady stream of WARN and ERROR lines, so it can never
stand for "a healthy robot". The clean profile removes all of that:

- TF: `map -> odom` steady (except for `tf_stale`), `odom -> base_link`, `base_link -> tool`;
- URDF: the static links plus `tool`, every one of them published (except for `declared_drift`,
  which adds `arm_link` on a revolute joint nothing publishes);
- no `/imu/data`, `/pose_mixed`, `/stamps_regress`, and no periodic `/rosout` WARN or ERROR.

Faults that need another process (a hung executor, a node that keeps dying, a graph change)
list it in `COMPANIONS`; `scripts/agent_eval/ros2_scenarios.py` starts them. The expected
diagnosis of every scenario is in `scripts/agent_eval/TRUTH_ROS2.md`. This module never touches
TopicForge: the faults live in the bench publisher, TopicForge stays read-only.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass

import ground_truth as model

HEALTHY = "healthy"
BLOCKED_EXECUTOR = "blocked_executor"
TF_STALE = "tf_stale"
QOS_MISMATCH = "qos_mismatch"
RESTART_LOOP = "restart_loop"
DECLARED_DRIFT = "declared_drift"
GRAPH_V1_V2 = "graph_v1_v2"
FAULTS = (
    HEALTHY,
    BLOCKED_EXECUTOR,
    TF_STALE,
    QOS_MISMATCH,
    RESTART_LOOP,
    DECLARED_DRIFT,
    GRAPH_V1_V2,
)

# qos_mismatch: the laser edge publisher offers best_effort, /edge_viewer asks for reliable.
EDGE_TOPIC = "/scan_edge"
EDGE_VIEWER_NODE = "/edge_viewer"
# restart_loop: /path_planner lives FLAPPER_LIFE_S, logs an ERROR, exits 1; its supervisor
# starts a new process FLAPPER_GAP_S later (a new participant each time).
FLAPPER_NODE = "/path_planner"
FLAPPER_TOPIC = "/plan"
FLAPPER_LIFE_S = 6.0
FLAPPER_GAP_S = 2.0
FLAPPER_ERROR = "path_planner: costmap not received, giving up"
# tf_stale: the same mechanism as the CI bench (published for TF_FLAKY_ACTIVE_S after each new
# /tf subscriber, then silent), because TopicForge sees /tf only through a capture window.
MAP_ODOM_EDGE = model.TF_FLAKY_EDGE
# declared_drift: where the parameter file sits next to this module (the runner makes it absolute).
DRIFT_PARAMS_FILE = model.PARAMS_FILE_NAME
TOOL_FRAME = "tool"


@dataclass(frozen=True)
class Companion:
    """Another process a fault needs, started from the bench folder.

    `when` is `start` (with the publisher) or `after_baseline` (after the runner took the
    `graph_diff` baseline, so the change happens between the two snapshots).
    """

    script: str
    args: tuple[str, ...] = ()
    when: str = "start"


COMPANIONS: dict[str, tuple[Companion, ...]] = {
    HEALTHY: (),
    BLOCKED_EXECUTOR: (Companion("blocked_node.py"),),
    TF_STALE: (),
    QOS_MISMATCH: (Companion("fault_nodes.py", ("edge_viewer",)),),
    RESTART_LOOP: (Companion("fault_nodes.py", ("flapper",)),),
    DECLARED_DRIFT: (),
    GRAPH_V1_V2: (Companion("graph_change.py", when="after_baseline"),),
}


@dataclass(frozen=True)
class Profile:
    """What the publisher changes for one fault (always the clean robot, see the module text)."""

    fault: str
    map_odom_flaky: bool = False
    urdf_drift: bool = False
    edge_best_effort: bool = False


def profile_for(fault: str) -> Profile:
    """The publisher profile of `fault`; raises `ValueError` for a name not in `FAULTS`."""
    if fault not in FAULTS:
        raise ValueError(f"unknown fault {fault!r}; choose one of {', '.join(FAULTS)}")
    return Profile(
        fault=fault,
        map_odom_flaky=fault == TF_STALE,
        urdf_drift=fault == DECLARED_DRIFT,
        edge_best_effort=fault == QOS_MISMATCH,
    )


def parse_fault_args(argv: Sequence[str]) -> tuple[str | None, list[str]]:
    """Split `--fault NAME` from the arguments meant for rclpy.

    Returns `(fault, rest)`: `fault` is `None` when the flag is absent (the CI bench), `rest`
    is everything else, untouched, for `rclpy.init`. An unknown name exits with usage.
    """
    parser = argparse.ArgumentParser(prog="publisher.py", add_help=False)
    parser.add_argument("--fault", choices=FAULTS, default=None)
    namespace, rest = parser.parse_known_args(list(argv))
    return namespace.fault, rest


def steady_edges() -> tuple[tuple[str, str], ...]:
    """The `/tf` edges of the clean robot that are always published (without `map -> odom`)."""
    return (("odom", "base_link"), ("base_link", TOOL_FRAME))


def urdf_link_names(drift: bool) -> list[str]:
    """Links of the clean URDF: the static frames, `tool`, then `arm_link` when `drift`."""
    names: list[str] = []
    for parent, child in model.TF_STATIC_EDGES:
        names += [n for n in (parent, child) if n not in names]
    names.append(TOOL_FRAME)
    if drift:
        names.append(model.URDF_UNPUBLISHED_LINK)
    return names


def urdf_text(drift: bool) -> str:
    """The URDF the clean robot latches on `/robot_description` (links and joints only)."""
    parts = ['<?xml version="1.0"?>', '<robot name="eval_robot">']
    parts += [f'<link name="{name}"/>' for name in urdf_link_names(drift)]
    for parent, child in model.TF_STATIC_EDGES:
        parts.append(
            f'<joint name="{child}_joint" type="fixed">'
            f'<parent link="{parent}"/><child link="{child}"/></joint>'
        )
    parts.append(
        f'<joint name="{TOOL_FRAME}_joint" type="continuous">'
        f'<parent link="base_link"/><child link="{TOOL_FRAME}"/><axis xyz="0 0 1"/></joint>'
    )
    if drift:
        parts.append(
            f'<joint name="{model.URDF_UNPUBLISHED_LINK}_joint" type="revolute">'
            f'<parent link="base_link"/><child link="{model.URDF_UNPUBLISHED_LINK}"/>'
            '<limit lower="0" upper="1" effort="1" velocity="1"/></joint>'
        )
    parts.append("</robot>")
    return "".join(parts)
