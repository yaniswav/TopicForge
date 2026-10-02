"""Scenario buses for the blind agent evaluation, with injected failures.

    python scripts/agent_eval/scenarios.py <scenario>   # until stop_<scenario> exists (max 40 min)

Run it with the demo venv's interpreter (cyclonedds, dust-dds and mcp). Each
scenario starts its broker first (so TopicForge is on the bus before
anything happens), polls list_participants once so TopicForge has seen
everyone, then runs its chaos schedule. Nothing polls TopicForge afterwards:
whatever the agent learns, it learns by asking.
"""

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "examples" / "dds"))
from harness import Bus, Node, kill_tree  # noqa: E402

PY = sys.executable
# No console windows on Windows; a session of its own elsewhere.
SPAWN: dict = (
    {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
    if sys.platform == "win32"
    else {"start_new_session": True}
)


def n(name, writes=(), reads=(), rate=10.0, extra=(), vendor="cyclone"):
    """A generic role node, with extra CLI flags when needed."""
    args = ["--name", name, "--rate-hz", str(rate)]
    for w in writes:
        args += ["--write", w]
    for r in reads:
        args += ["--read", r]
    script = REPO / "examples" / "dds" / "nodes" / f"{vendor}_node.py"
    return Node(name, vendor, script=script, args=(*args, *extra))


def ask(port, tool, args=None):
    with socket.create_connection(("127.0.0.1", port), timeout=120) as s:
        s.sendall((json.dumps({"tool": tool, "args": args or {}}) + "\n").encode())
        data = b""
        while chunk := s.recv(65536):
            data += chunk
    return json.loads(data)


# ------------------------------------------------------------------ scenarios
# name: (domain, nodes, chaos[(t_seconds, action, node_or_nodes)])

SCAN = "scan:LidarScan"
SCENARIOS = {
    # A safety monitor crashes after 25 s; the robot keeps driving.
    "crash_live": (
        84,
        [
            n("nav_planner", writes=("cmd_vel:Twist:reliable",)),
            n("lidar_driver", writes=(f"{SCAN}:best_effort",)),
            n(
                "safety_monitor",
                reads=("cmd_vel:Twist:reliable",),
                writes=("estop:Heartbeat:reliable,transient_local",),
            ),
            n(
                "motor_controller",
                reads=("cmd_vel:Twist:reliable", "estop:Heartbeat:reliable,transient_local"),
            ),
        ],
        [(25, "crash", "safety_monitor")],
    ),
    # nav_planner crash-restarts three times, 8 s apart.
    "restart_loop": (
        85,
        [
            n("lidar_driver", writes=(f"{SCAN}:best_effort",)),
            n("nav_planner", reads=(f"{SCAN}:best_effort",), writes=("cmd_vel:Twist",)),
            n("motor_controller", reads=("cmd_vel:Twist",)),
        ],
        [
            (15, "restart", "nav_planner"),
            (23, "restart", "nav_planner"),
            (31, "restart", "nav_planner"),
        ],
    ),
    # Partition separates a camera from its viewer; their QoS also differ.
    "partition_split": (
        87,
        [
            n("camera_driver", writes=("image:Status:best_effort,partition=front",)),
            n("front_viewer", reads=("image:Status:reliable,partition=rear",)),
            n("recorder", reads=("image:Status:best_effort,partition=front",)),
        ],
        [],
    ),
    # The estop publisher hangs: process alive, no data, manual liveliness lost.
    "hung_estop": (
        88,
        [
            n(
                "estop_publisher",
                writes=("estop:Heartbeat:reliable,liveliness=manual_topic,lease=500",),
                extra=("--stop-asserting-after", "20"),
            ),
            n(
                "motor_controller",
                reads=("estop:Heartbeat:reliable,liveliness=manual_topic,lease=500",),
            ),
            n("nav_planner", writes=("cmd_vel:Twist:reliable",)),
        ],
        [],
    ),
    # A full robot with nine seeded problems (see TRUTH.md).
    "big_bus": (
        86,
        [
            n("lidar_front", writes=(f"{SCAN}:best_effort",)),
            n("lidar_rear", writes=("scan_rear:LidarScan:best_effort",), vendor="dust"),
            n("imu_driver", writes=("imu:Imu:reliable,deadline=10",)),
            n("wheel_odom", writes=("odom:Odom:reliable",)),
            n(
                "localization",
                reads=(f"{SCAN}:best_effort", "imu:Imu:reliable,deadline=50", "odom:Odom:reliable"),
                writes=("pose:Odom:reliable",),
            ),
            n(
                "nav_planner",
                reads=(
                    "pose:Odom:reliable",
                    f"{SCAN}:reliable",
                    "mission:Status:reliable,transient_local",
                ),
                writes=("cmd_vel:Twist:reliable",),
            ),
            n("mission_control", writes=("mission:Status:reliable,volatile",), rate=1.0),
            n(
                "motor_controller",
                reads=(
                    "cmd_vel:Twist:reliable,deadline=100",
                    "estop:Heartbeat:reliable,liveliness=manual_topic,lease=500",
                ),
                writes=("motor_state:Status:reliable",),
            ),
            n("safety_monitor", writes=("estop:Heartbeat:reliable",), rate=5.0),
            n("battery_monitor", writes=("battery:Status:reliable",), rate=1.0),
            n("dashboard", reads=("batery:Status:best_effort", "motor_state:Status:best_effort")),
            n("camera_driver", writes=("image:Status:best_effort,partition=front",)),
            n(
                "object_detector",
                reads=("image:Status:best_effort,partition=perception",),
                writes=("detections:Status:reliable",),
            ),
            n(
                "arm_controller_a",
                writes=("arm_cmd:Twist:reliable,ownership=exclusive,strength=10",),
            ),
            n("arm_driver", reads=("arm_cmd:Twist:reliable,ownership=shared",)),
            n(
                "logger",
                reads=(
                    "detections:Status:best_effort",
                    "odom:Odom:best_effort",
                    "scan_rear:LidarScan:best_effort",
                ),
            ),
        ],
        [],
    ),
    # A healthy mixed-vendor robot: nothing should be reported.
    "healthy_bus": (
        89,
        [
            n("lidar_driver", writes=(f"{SCAN}:best_effort",)),
            n("imu_driver", writes=("imu:Imu:reliable,deadline=10",)),
            n(
                "localization",
                reads=(f"{SCAN}:best_effort", "imu:Imu:reliable,deadline=50"),
                writes=("pose:Odom:reliable",),
            ),
            n(
                "nav_planner",
                reads=("pose:Odom:reliable", "mission:Status:reliable,transient_local"),
                writes=("cmd_vel:Twist:reliable,deadline=100",),
            ),
            n("mission_control", writes=("mission:Status:reliable,transient_local",), rate=1.0),
            n("motor_controller", reads=("cmd_vel:Twist:reliable,deadline=200",)),
            n("watchdog", writes=("heartbeat:Heartbeat:reliable",), rate=1.0, vendor="dust"),
            n("supervisor", reads=("heartbeat:Heartbeat:best_effort",)),
            n("camera_driver", writes=("image:Status:best_effort,partition=front",)),
            n("object_detector", reads=("image:Status:best_effort,partition=front",)),
        ],
        [],
    ),
    # Partitions with wildcards: who really gets the camera?
    "partition_wild": (
        90,
        [
            n("camera_driver", writes=("image:Status:best_effort,partition=robot1",)),
            n("viewer_all", reads=("image:Status:best_effort,partition=robot*",)),
            n("viewer_robot2", reads=("image:Status:best_effort,partition=robot2",)),
            n("viewer_default", reads=("image:Status:best_effort",)),
        ],
        [],
    ),
    # Two exclusive arm controllers; one shared reader.
    "ownership_pair": (
        91,
        [
            n(
                "arm_controller_a",
                writes=("arm_cmd:Twist:reliable,ownership=exclusive,strength=10",),
            ),
            n(
                "arm_controller_b",
                writes=("arm_cmd:Twist:reliable,ownership=exclusive,strength=5",),
            ),
            n("arm_driver", reads=("arm_cmd:Twist:reliable,ownership=exclusive",)),
            n("arm_monitor", reads=("arm_cmd:Twist:reliable",)),
        ],
        [],
    ),
    # Same topic name, different types across suppliers.
    "type_divergence": (
        92,
        [
            n("lidar_driver", writes=(f"{SCAN}:best_effort",)),
            n("localization", reads=("scan:Odom:best_effort",), vendor="dust"),
            n("nav_planner", reads=(f"{SCAN}:best_effort",)),
        ],
        [],
    ),
    # imu_driver was started on the wrong domain.
    "domain_confusion": (
        93,
        [
            n("lidar_driver", writes=(f"{SCAN}:best_effort",)),
            n("localization", reads=(f"{SCAN}:best_effort", "imu:Imu:reliable")),
        ],
        [],
    ),
}


def main() -> None:
    name = sys.argv[1]
    domain, nodes, chaos = SCENARIOS[name]
    port = 8700 + domain
    stop = HERE / f"stop_{name}"
    stop.unlink(missing_ok=True)
    with open(HERE / f"{name}.broker.log", "ab") as blog:
        broker = subprocess.Popen(
            [PY, str(HERE / "broker.py"), "--domain", str(domain), "--port", str(port)],
            stdout=blog,
            stderr=subprocess.STDOUT,
            cwd=HERE,
            **SPAWN,
        )
    try:
        time.sleep(6)
        with Bus(domain) as bus, Bus(1) as other:
            bus.start(*nodes)
            if name == "domain_confusion":  # started on the wrong domain on purpose
                other.start(n("imu_driver", writes=("imu:Imu:reliable",)))
            time.sleep(4)
            ask(port, "list_participants", {"domain_id": domain})  # TopicForge has seen everyone
            print(f"[{name}] up: domain {domain}, port {port}", flush=True)
            t0 = time.monotonic()
            pending = sorted(chaos)
            spec = {nd.name: nd for nd in nodes}
            while not stop.exists() and time.monotonic() - t0 < 2400:
                while pending and time.monotonic() - t0 >= pending[0][0]:
                    _, action, who = pending.pop(0)
                    bus.crash(who)
                    if action == "restart":
                        time.sleep(0.5)
                        bus.start(spec[who])
                    print(f"[{name}] t+{time.monotonic() - t0:.0f}s {action} {who}", flush=True)
                time.sleep(0.5)
    finally:
        kill_tree(broker)


if __name__ == "__main__":
    main()
