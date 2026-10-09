"""Publisher node that mimics a simulated 2D-lidar robot (runs inside the bench container).

Everything is stamped with simulated time that starts at 0, and `/clock`
is published by this same node. The numbers it publishes come from `ground_truth.py`, which
also writes the matching `ground_truth.json` when the node starts.
"""

from __future__ import annotations

import math
import time

import ground_truth as model
import rclpy
from builtin_interfaces.msg import Time
from geometry_msgs.msg import Twist
from ground_truth import CAMERA_DELAY_SEC, N_BEAMS
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from rosgraph_msgs.msg import Clock
from sensor_msgs.msg import Image, LaserScan
from std_msgs.msg import String


def sim_time_msg(t: float) -> Time:
    sec = int(t)
    return Time(sec=sec, nanosec=int((t - sec) * 1e9))


class BenchRobot(Node):
    def __init__(self) -> None:
        super().__init__("bench_robot")
        self._t0 = time.monotonic()
        qos = QoSProfile(depth=10)
        self._clock_pub = self.create_publisher(Clock, "/clock", qos)
        self._scan_pub = self.create_publisher(LaserScan, "/scan", qos)
        self._twist_pub = self.create_publisher(Twist, "/cmd_vel_out", qos)
        self._image_pub = self.create_publisher(Image, "/camera/image_raw", qos)
        self._edge_pub = self.create_publisher(LaserScan, "/scan_edge", qos)
        latched = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        self._desc_pub = self.create_publisher(String, "/robot_description_lite", latched)

        self._image_data = bytes(640 * 480 * 3)
        self.create_timer(model.CLOCK_PERIOD, self._tick_clock)
        self.create_timer(model.SCAN_PERIOD, self._tick_scan)
        self.create_timer(model.TWIST_PERIOD, self._tick_twist)
        self.create_timer(model.IMAGE_PERIOD, self._tick_image)
        self.create_timer(model.EDGE_PERIOD, self._tick_edge)

        desc = String()
        desc.data = "bench_robot: differential drive, 2D lidar"
        self._desc_pub.publish(desc)

    def _now(self) -> float:
        return time.monotonic() - self._t0

    def _tick_clock(self) -> None:
        msg = Clock()
        msg.clock = sim_time_msg(self._now())
        self._clock_pub.publish(msg)

    def _tick_scan(self) -> None:
        msg = LaserScan()
        msg.header.stamp = sim_time_msg(self._now())
        msg.header.frame_id = model.SCAN_FRAME
        msg.angle_min = model.ANGLE_MIN
        msg.angle_max = -model.ANGLE_MIN
        msg.angle_increment = model.ANGLE_INCREMENT
        msg.time_increment = 0.0
        msg.scan_time = model.SCAN_TIME
        msg.range_min = model.RANGE_MIN
        msg.range_max = model.RANGE_MAX
        msg.ranges = model.scan_ranges()
        msg.intensities = [float(i) for i in range(N_BEAMS)]
        self._scan_pub.publish(msg)

    def _tick_edge(self) -> None:
        # No-return beams: lidar drivers publish `inf`, and `nan` for invalid ones.
        msg = LaserScan()
        msg.header.stamp = sim_time_msg(self._now())
        msg.header.frame_id = "edge_laser"
        msg.ranges = [math.inf, math.nan, -math.inf, 1.5]
        self._edge_pub.publish(msg)

    def _tick_twist(self) -> None:
        msg = Twist()
        msg.linear.x = 0.25
        msg.angular.z = 0.5
        self._twist_pub.publish(msg)

    def _tick_image(self) -> None:
        if self._now() < CAMERA_DELAY_SEC:
            return
        msg = Image()
        msg.header.stamp = sim_time_msg(self._now())
        msg.header.frame_id = "camera_link"
        msg.height = 480
        msg.width = 640
        msg.encoding = "rgb8"
        msg.step = 640 * 3
        msg.data = self._image_data
        self._image_pub.publish(msg)


def main() -> None:
    model.write_truth()
    rclpy.init()
    node = BenchRobot()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
