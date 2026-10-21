"""A node that changes the graph between two `graph_diff` calls (runs inside the bench container).

`scripts/ground_truth/drive.py` starts it (`{"spawn": ...}` in `calls.json`) after the steady
`graph_diff` call and stops it when the session ends. It adds exactly three differences, the
ones `ground_truth.graph_diff_truth` names: the node `/bench_v2`, the topic `/scan_v2`
(`sensor_msgs/msg/LaserScan`) and a second writer on `/cmd_vel_out` with the same QoS as the
first (reliable, volatile, depth 10, as `publisher.py` creates it), so only the publisher count
of that topic changes. It subscribes to nothing, so no subscriber count moves.
"""

from __future__ import annotations

import ground_truth as model
import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node
from rclpy.qos import QoSProfile
from sensor_msgs.msg import LaserScan


class GraphChange(Node):
    def __init__(self) -> None:
        super().__init__(model.GRAPH_V2_NODE.strip("/"))
        qos = QoSProfile(depth=10)
        self._scan_pub = self.create_publisher(LaserScan, model.GRAPH_V2_TOPIC, qos)
        self._twist_pub = self.create_publisher(Twist, model.GRAPH_V2_SHARED_TOPIC, qos)
        self.create_timer(0.5, self._tick)

    def _tick(self) -> None:
        scan = LaserScan()
        scan.header.frame_id = "v2_laser"
        scan.ranges = [1.0, 2.0]
        self._scan_pub.publish(scan)
        self._twist_pub.publish(Twist())


def main() -> None:
    rclpy.init()
    node = GraphChange()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
