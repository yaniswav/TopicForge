"""Small nodes for the agent-evaluation faults (run inside the bench image or on a ROS 2 host).

    python3 fault_nodes.py edge_viewer   # qos_mismatch: reads /scan_edge as RELIABLE
    python3 fault_nodes.py flapper       # restart_loop: keeps starting /path_planner, which dies

`edge_viewer` subscribes to `/scan_edge` with a reliable QoS while `publisher.py --fault
qos_mismatch` offers best_effort there, so DDS never connects them. `flapper` is a supervisor:
it starts `flapper-child` (a node `/path_planner` that logs an ERROR and exits 1 after
`FLAPPER_LIFE_S`), waits `FLAPPER_GAP_S`, and starts it again, printing `start N` for every
new process (N - 1 restarts so far). Each child is a new process and so a new DDS participant, like a node that a
launch file respawns. Constants and expected diagnoses: `faults.py`, `TRUTH_ROS2.md`.
"""

from __future__ import annotations

import subprocess
import sys
import time

import faults


def run_edge_viewer() -> None:
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import QoSProfile, ReliabilityPolicy
    from sensor_msgs.msg import LaserScan

    rclpy.init()
    node = Node(faults.EDGE_VIEWER_NODE.strip("/"))
    qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE)
    node.create_subscription(LaserScan, faults.EDGE_TOPIC, lambda _msg: None, qos)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


def run_flapper_child() -> int:
    import rclpy
    from rclpy.node import Node
    from std_msgs.msg import String

    rclpy.init()
    node = Node(faults.FLAPPER_NODE.strip("/"))
    publisher = node.create_publisher(String, faults.FLAPPER_TOPIC, 10)
    node.create_timer(0.5, lambda: publisher.publish(String(data="path")))
    deadline = time.monotonic() + faults.FLAPPER_LIFE_S
    try:
        while time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.1)
        node.get_logger().error(faults.FLAPPER_ERROR)
        for _ in range(5):  # let the /rosout line leave before the process dies
            rclpy.spin_once(node, timeout_sec=0.1)
    except KeyboardInterrupt:
        return 0
    finally:
        node.destroy_node()
        rclpy.shutdown()
    return 1


def run_flapper() -> None:
    count = 0
    try:
        while True:
            count += 1
            print(f"start {count}", flush=True)
            subprocess.run([sys.executable, __file__, "flapper-child"], check=False)
            time.sleep(faults.FLAPPER_GAP_S)
    except KeyboardInterrupt:
        pass


def main(argv: list[str]) -> int:
    role = argv[0] if argv else ""
    if role == "edge_viewer":
        run_edge_viewer()
    elif role == "flapper":
        run_flapper()
    elif role == "flapper-child":
        return run_flapper_child()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
