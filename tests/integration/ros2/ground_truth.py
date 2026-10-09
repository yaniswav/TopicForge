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
- topics, types, counts and QoS are what `publisher.py` and `blocked_node.py` create.
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

# name -> (type, latched). One publisher each, from /bench_robot, no subscriber.
BENCH_TOPICS = {
    "/clock": ("rosgraph_msgs/msg/Clock", False),
    "/scan": ("sensor_msgs/msg/LaserScan", False),
    "/cmd_vel_out": ("geometry_msgs/msg/Twist", False),
    "/camera/image_raw": ("sensor_msgs/msg/Image", False),
    "/scan_edge": ("sensor_msgs/msg/LaserScan", False),
    "/robot_description_lite": ("std_msgs/msg/String", True),
}
# Present on every graph; the number of endpoints depends on hidden CLI and daemon nodes.
INFRA_TOPICS = {
    "/parameter_events": "rcl_interfaces/msg/ParameterEvent",
    "/rosout": "rcl_interfaces/msg/Log",
}
BENCH_NODES = {"/bench_robot": False, "/bench_blocked": None}
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
    return {
        "node_name": node.strip("/"),
        "node_namespace": "/",
        "topic_type": BENCH_TOPICS[topic][0],
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
        topics.append(
            {
                "name": name,
                "types": [type_name],
                "publisher_count": 1,
                "subscription_count": 0,
                "endpoints": [_endpoint(name, "/bench_robot", latched)],
            }
        )
    return {"nodes": sorted(BENCH_NODES), "use_sim_time": dict(BENCH_NODES), "topics": topics}


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
