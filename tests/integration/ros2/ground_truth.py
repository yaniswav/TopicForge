"""The bench robot's model and its own ground truth (no ROS import, runs anywhere).

`publisher.py` takes its constants from here and writes `build_truth()` to disk when it
starts, so what is published and what is claimed cannot drift. The output uses the subset of
the OmniSim kit's `ground_truth.json` schema that `scripts/ground_truth/compare.py` reads,
which makes the bench a second producer of ground truth for the same comparator.

Everything is deterministic and exact by construction:

- the lidar is synthetic: `ranges[i] = 1.0 + i * 0.001` metres for 541 beams over
  [-135, +135] degrees, so every expected range is a closed formula (rounded to float32, as
  the message carries it);
- the "walls" are the sector minima of that scan under the sector rule of CONTRACT.md
  section 4 (front within +-45 degrees, left 45..135, right -135..-45, rear beyond +-135; an
  edge beam goes to the narrower sector, with a 1e-4 rad tolerance);
- rates are the timer periods, and the bench stamps everything with seconds since start on
  the same monotonic clock, so the simulated rate equals the wall rate;
- topics, types, counts and QoS are what `publisher.py`, `blocked_node.py` and
  `lifecycle_node.py` create (the last one a managed node in `/robot1`);
- the TF tree is the `tf` section: three static edges on `/tf_static`, three steady edges
  on `/tf` (one of them gives `tool` a second parent), and one edge (`map -> odom`) that
  is published for `TF_FLAKY_ACTIVE_S` after each new subscriber of `/tf` and then stops,
  so any capture longer than that sees it go stale.
"""

from __future__ import annotations

import json
import math
import os
import struct
from pathlib import Path
from typing import Any

N_BEAMS = 541
CAMERA_DELAY_SEC = 5.0
ANGLE_MIN = -0.75 * math.pi
ANGLE_INCREMENT = 1.5 * math.pi / (N_BEAMS - 1)
RANGE_MIN = 0.05
RANGE_MAX = 30.0
SCAN_TIME = 0.1
SCAN_FRAME = "base_laser"
EDGE_RANGES = ("inf", "nan", "-inf", 1.5)
# Timer periods in seconds.
CLOCK_PERIOD = 0.02
SCAN_PERIOD = 0.1
TWIST_PERIOD = 0.2
IMAGE_PERIOD = 0.5
EDGE_PERIOD = 0.5
TF_PERIOD = 0.1
# `map -> odom` is sent this long after each new subscriber of `/tf`, then it stops.
TF_FLAKY_ACTIVE_S = 3.0
# (parent, child). Static edges go out once on `/tf_static`.
TF_STATIC_EDGES = (
    ("base_link", SCAN_FRAME),
    ("base_link", "camera_link"),
    ("camera_link", "camera_optical_frame"),
)
# `tool` has two parents: the conflict `inspect_tf` must report.
TF_STEADY_EDGES = (("odom", "base_link"), ("base_link", "tool"), ("odom", "tool"))
TF_FLAKY_EDGE = ("map", "odom")
# The `inspect_tf` call of `scripts/ground_truth/calls.json`: the window outlasts the flaky edge.
TF_CALL = {
    "window_s": 12,
    "stale_after_s": 2.0,
    "source_frame": "camera_optical_frame",
    "target_frame": SCAN_FRAME,
}

# /rosout lines (0.7.3): WARN every LOG_WARN_PERIOD and ERROR every LOG_ERROR_PERIOD from
# /bench_robot, INFO every LOG_INFO_PERIOD from the lifecycle node. See `logs_truth`.
LOG_WARN_PERIOD = 1.0
LOG_ERROR_PERIOD = 2.0
LOG_INFO_PERIOD = 5.0

# Clock topics (0.7.3), see `clock_truth`.
IMU_PERIOD = 0.02
# `/imu/data` is stamped with the wall clock, this long before it is published.
IMU_WALL_AGE_S = 0.02
# `/pose_mixed` has two publishers, one stamped in sim time, one in wall time.
POSE_PERIOD = 0.1
# `/stamps_regress`: every REGRESS_EVERY-th stamp is REGRESS_DIP_S behind the previous one.
REGRESS_PERIOD = 0.2
REGRESS_EVERY = 4
REGRESS_DIP_S = 1.0
# name -> (type, latched). One publisher each, except PUBLISHER_COUNTS, from /bench_robot, no subscriber.
BENCH_TOPICS = {
    "/clock": ("rosgraph_msgs/msg/Clock", False),
    "/scan": ("sensor_msgs/msg/LaserScan", False),
    "/cmd_vel_out": ("geometry_msgs/msg/Twist", False),
    "/camera/image_raw": ("sensor_msgs/msg/Image", False),
    "/scan_edge": ("sensor_msgs/msg/LaserScan", False),
    "/robot_description_lite": ("std_msgs/msg/String", True),
    "/tf": ("tf2_msgs/msg/TFMessage", False),
    "/tf_static": ("tf2_msgs/msg/TFMessage", True),
    "/imu/data": ("sensor_msgs/msg/Imu", False),
    "/pose_mixed": ("geometry_msgs/msg/PoseStamped", False),
    "/stamps_regress": ("geometry_msgs/msg/PoseStamped", False),
}
PUBLISHER_COUNTS = {"/pose_mixed": 2}
# Present on every graph; the number of endpoints depends on hidden CLI and daemon nodes.
INFRA_TOPICS = {
    "/parameter_events": "rcl_interfaces/msg/ParameterEvent",
    "/rosout": "rcl_interfaces/msg/Log",
}
# A managed node in a namespace: `list_nodes` must flag it `lifecycle` (it serves
# `<name>/get_state` of type lifecycle_msgs/srv/GetState) and count it under `/robot1`.
# Its only topic is `<name>/transition_event`, published by rclpy, never by `/bench_robot`.
LIFECYCLE_NODE = "/robot1/bench_lifecycle"
LIFECYCLE_TOPIC = f"{LIFECYCLE_NODE}/transition_event"
LIFECYCLE_TOPIC_TYPE = "lifecycle_msgs/msg/TransitionEvent"
BENCH_NODES = {"/bench_robot": False, "/bench_blocked": None, LIFECYCLE_NODE: False}
PERIODS = {
    "/clock": CLOCK_PERIOD,
    "/scan": SCAN_PERIOD,
    "/cmd_vel_out": TWIST_PERIOD,
    "/camera/image_raw": IMAGE_PERIOD,
    "/scan_edge": EDGE_PERIOD,
}
STAMPS = {
    "/clock": "payload",
    "/scan": "header",
    "/scan_edge": "header",
    "/camera/image_raw": "header",
    "/cmd_vel_out": "none",
}
SECTOR_EDGE_EPS = 1e-4


def tf_truth() -> dict[str, Any]:
    """What `inspect_tf` must report for the bench tree (see `TF_*` above)."""
    edges = [*TF_STATIC_EDGES, *TF_STEADY_EDGES, TF_FLAKY_EDGE]
    return {
        "call": dict(TF_CALL),
        "static_edges": [list(e) for e in TF_STATIC_EDGES],
        "dynamic_edges": [list(e) for e in (*TF_STEADY_EDGES, TF_FLAKY_EDGE)],
        "frames": sorted({name for edge in edges for name in edge}),
        "roots": [TF_FLAKY_EDGE[0]],
        "stale": [TF_FLAKY_EDGE[1]],
        "two_parents": {"tool": sorted({"base_link", "odom"})},
        "finding_kinds": ["stale", "two_parents"],
        "steady_hz": 1.0 / TF_PERIOD,
        "path": [
            TF_CALL["source_frame"],
            "camera_link",
            "base_link",
            SCAN_FRAME,
        ],
        "real_time_factor": 1.0,
    }


def clock_truth() -> dict[str, Any]:
    """What `TopicRate.clock_basis` and the stamp fields must say per topic (0.7.3)."""
    return {
        "topics": {
            "/scan": {"basis": "sim"},
            "/cmd_vel_out": {"basis": "none"},
            "/imu/data": {
                "basis": "wall",
                "age_median_s_range": [0.015, 0.2],
                "age_p95_s_max": 0.5,
                "published_age_s": IMU_WALL_AGE_S,
            },
            "/pose_mixed": {"basis": "mixed"},
            "/stamps_regress": {"basis": "sim", "min_regressions": 2, "min_messages": 12},
        },
        "note": "wall = stamps are dates; the age is arrival minus stamp, so it also holds delivery",
    }


def logs_truth() -> dict[str, Any]:
    """What the log filter and the bag range must return (0.7.3).

    The live calls use a short `timeout_s`, so only the 10 s transient_local buffer of
    /rosout (read with depth 2000) can supply `min_error_msgs`: live lines alone would give
    one or two. `scripts/ground_truth/compare.py` reports the exact counts either way.
    """
    return {
        "topic": "/rosout",
        "node": "/bench_robot",
        "logger": "bench_robot",
        "warn_period_s": LOG_WARN_PERIOD,
        "error_period_s": LOG_ERROR_PERIOD,
        "min_error_msgs": 3,
        "lifecycle_node": LIFECYCLE_NODE,
        "lifecycle_logger": LIFECYCLE_NODE.strip("/").replace("/", "."),
        "info_period_s": LOG_INFO_PERIOD,
        "blocked_node": "/bench_blocked",
        "bag_min_errors": 2,
        "bag_range": {"topic": "/clock", "start_s": 2, "end_s": 4, "count": 50},
    }


def f32(value: float) -> float:
    """`value` rounded to float32, which is what a ROS message field carries."""
    return struct.unpack("f", struct.pack("f", value))[0]


def scan_ranges() -> list[float]:
    """The ranges the bench publishes on /scan (float64; the message stores float32)."""
    return [1.0 + i * 0.001 for i in range(N_BEAMS)]


def _sector(bearing: float) -> str:
    if abs(bearing) <= math.radians(45.0) + SECTOR_EDGE_EPS:
        return "front"
    if abs(bearing) > math.radians(135.0) + SECTOR_EDGE_EPS:
        return "rear"
    return "left" if bearing > 0 else "right"


def sector_minima() -> dict[str, dict[str, Any]]:
    """Closest beam per sector, computed from the geometry above."""
    start, step = f32(ANGLE_MIN), f32(ANGLE_INCREMENT)
    best: dict[str, tuple[float, int]] = {}
    for index, value in enumerate(f32(r) for r in scan_ranges()):
        sector = _sector(start + index * step)
        if sector not in best or value < best[sector][0]:
            best[sector] = (value, index)
    walls: dict[str, dict[str, Any]] = {}
    for name in ("front", "left", "right", "rear"):
        if name in best:
            walls[name] = {
                "sector": name,
                "in_field_of_view": True,
                "beam_index": best[name][1],
                "expected_range_m": best[name][0],
                "beam_index_tolerance": 0,
            }
        else:
            walls[name] = {
                "sector": name,
                "in_field_of_view": False,
                "note": "the scan spans exactly +-135 degrees: no beam beyond",
            }
    return walls


def _endpoint(topic: str, node: str, latched: bool) -> dict[str, Any]:
    namespace, _, name = node.rpartition("/")
    return {
        "node_name": name,
        "node_namespace": namespace or "/",
        "topic_type": LIFECYCLE_TOPIC_TYPE if topic == LIFECYCLE_TOPIC else BENCH_TOPICS[topic][0],
        "endpoint_type": "PUBLISHER",
        "reliability": "RELIABLE",
        "durability": "TRANSIENT_LOCAL" if latched else "VOLATILE",
    }


def _graph() -> dict[str, Any]:
    topics = []
    for name in sorted({*BENCH_TOPICS, *INFRA_TOPICS}):
        if name in INFRA_TOPICS:
            topics.append(
                {
                    "name": name,
                    "types": [INFRA_TOPICS[name]],
                    "counts": "ignore",
                    "publisher_count": 0,
                    "subscription_count": 0,
                    "endpoints": [],
                }
            )
            continue
        type_name, latched = BENCH_TOPICS[name]
        publishers = PUBLISHER_COUNTS.get(name, 1)
        topics.append(
            {
                "name": name,
                "types": [type_name],
                "publisher_count": publishers,
                "subscription_count": 0,
                "endpoints": [_endpoint(name, "/bench_robot", latched)] * publishers,
            }
        )
    topics.append(
        {
            "name": LIFECYCLE_TOPIC,
            "types": [LIFECYCLE_TOPIC_TYPE],
            "publisher_count": 1,
            "subscription_count": 0,
            "endpoints": [_endpoint(LIFECYCLE_TOPIC, LIFECYCLE_NODE, False)],
        }
    )
    topics.sort(key=lambda t: t["name"])
    return {
        "nodes": sorted(BENCH_NODES),
        "use_sim_time": dict(BENCH_NODES),
        "lifecycle_nodes": [LIFECYCLE_NODE],
        "nodes_by_namespace": nodes_by_namespace(),
        "topics": topics,
    }


def nodes_by_namespace() -> dict[str, int]:
    """Distinct bench nodes per namespace (`/` is the root), sorted, as `list_nodes` counts."""
    counts: dict[str, int] = {}
    for node in BENCH_NODES:
        namespace = node.rpartition("/")[0] or "/"
        counts[namespace] = counts.get(namespace, 0) + 1
    return dict(sorted(counts.items()))


def build_truth(distro: str, rmw: str) -> dict[str, Any]:
    """The bench's ground truth for one distro and RMW, in the OmniSim kit's schema subset."""
    ranges = [f32(r) for r in scan_ranges()]
    per_topic = {}
    for name, period in PERIODS.items():
        hz = 1.0 / period
        per_topic[name] = {
            "type": BENCH_TOPICS[name][0],
            "configured_hz": hz,
            "rate_wall_hz": hz,
            "rate_sim_hz": hz,
        }
    return {
        "schema_version": 1,
        "kit": "topicforge bench (tests/integration/ros2)",
        "generated_utc": "n/a (deterministic)",
        "bench": {"version": 1, "distro": distro, "rmw_implementation": rmw},
        "ros": {"distro": distro, "rmw_implementation": rmw, "use_sim_time": dict(BENCH_NODES)},
        "lidar": {
            "frame_id": SCAN_FRAME,
            "n_beams": N_BEAMS,
            "angle_min": f32(ANGLE_MIN),
            "angle_max": f32(-ANGLE_MIN),
            "angle_increment": f32(ANGLE_INCREMENT),
            "range_min": f32(RANGE_MIN),
            "range_max": f32(RANGE_MAX),
            "time_increment": 0.0,
            "scan_time": f32(SCAN_TIME),
            "n_intensities": N_BEAMS,
            "published_ranges": ranges,
            "beam_angle_formula": "angle_min + j * angle_increment",
            "range_formula": "1.0 + j * 0.001 (float32)",
        },
        "walls": sector_minima(),
        "edge_scan": {"topic": "/scan_edge", "ranges": list(EDGE_RANGES)},
        "stamps": dict(STAMPS),
        "rates": {
            "configured_hz": {n: v["configured_hz"] for n, v in per_topic.items()},
            "per_topic": per_topic,
            "fixed_rate_topics": sorted(PERIODS),
            "expected_verdicts": ["stable"],
            "note": "timer periods; the bench stamps with seconds since start, so sim rate = wall rate",
        },
        "tf": tf_truth(),
        "clock": clock_truth(),
        "logs": logs_truth(),
        "graph": {"before_capture": _graph()},
        "bag": None,
    }


def write_truth(path: str | Path | None = None) -> Path:
    """Write the truth for the current `ROS_DISTRO` and `RMW_IMPLEMENTATION` to `path`."""
    target = Path(path or os.environ.get("TOPICFORGE_BENCH_TRUTH", "/tmp/ground_truth.json"))
    distro = os.environ.get("ROS_DISTRO", "unknown")
    rmw = os.environ.get("RMW_IMPLEMENTATION") or "rmw_fastrtps_cpp"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(build_truth(distro, rmw), indent=1), encoding="utf-8")
    return target


if __name__ == "__main__":
    print(write_truth())
