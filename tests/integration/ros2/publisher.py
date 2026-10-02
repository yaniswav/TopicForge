"""Publisher node that mimics a simulated 2D-lidar robot (runs inside the bench container).

Everything is stamped with simulated time that starts at 0, and `/clock`
is published by this same node.
"""

from __future__ import annotations

import math
import time

import rclpy
from builtin_interfaces.msg import Time
from geometry_msgs.msg import Twist
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from rosgraph_msgs.msg import Clock
from sensor_msgs.msg import Image, LaserScan
from std_msgs.msg import String

N_BEAMS = 541
CAMERA_DELAY_SEC = 5.0


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
        latched = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        self._desc_pub = self.create_publisher(String, "/robot_description_lite", latched)

        self._image_data = bytes(640 * 480 * 3)
        self.create_timer(0.02, self._tick_clock)
        self.create_timer(0.1, self._tick_scan)
        self.create_timer(0.2, self._tick_twist)
        self.create_timer(0.5, self._tick_image)

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
        msg.header.frame_id = "base_laser"
        msg.angle_min = -0.75 * math.pi
        msg.angle_max = 0.75 * math.pi
        msg.angle_increment = 1.5 * math.pi / (N_BEAMS - 1)
        msg.time_increment = 0.0
        msg.scan_time = 0.1
        msg.range_min = 0.05
        msg.range_max = 30.0
        msg.ranges = [1.0 + i * 0.001 for i in range(N_BEAMS)]
        msg.intensities = [float(i) for i in range(N_BEAMS)]
        self._scan_pub.publish(msg)

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
