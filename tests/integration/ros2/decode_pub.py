"""Deterministic publishers for the decode cell of the bench (`decode_check.py`). rclpy only.

Every message carries its index, so a decoded sample can be matched to the same message in
`ros2 topic echo`. Payload lengths vary to exercise CDR padding. Each writer's matched
subscription count goes to `sys.argv[1]` as JSON once a second: that is the writer's own view,
which is where ghost readers would show.
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import rclpy
from geometry_msgs.msg import Quaternion
from nav_msgs.msg import Odometry
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Image, LaserScan
from std_msgs.msg import String

RATE_HZ = 10.0


def _stamp(msg, i: int) -> None:
    msg.header.stamp.sec = 1000 + i
    msg.header.stamp.nanosec = 7 * i
    msg.header.frame_id = "frame" + "x" * (i % 4)


def make_string(i: int) -> String:
    return String(data=f"decode payload{'y' * (i % 5)} {i}")


def make_scan(i: int) -> LaserScan:
    m = LaserScan()
    _stamp(m, i)
    m.angle_min, m.angle_max = -math.pi, math.pi
    m.angle_increment = 2 * math.pi / 360
    m.time_increment, m.scan_time = 1e-4, 0.1
    m.range_min, m.range_max = 0.12, 12.5
    ranges = [0.5 + ((k * 37 + i) % 1000) / 83.0 for k in range(360)]
    ranges[10], ranges[11], ranges[12] = float("inf"), float("nan"), float("-inf")
    m.ranges = ranges
    m.intensities = [] if i % 2 == 0 else [float(k % 7) / 3 for k in range(360)]
    return m


def make_odom(i: int) -> Odometry:
    m = Odometry()
    _stamp(m, i)
    m.child_frame_id = "base" + "z" * (i % 3)
    m.pose.pose.position.x = 1.0 + i / 7
    m.pose.pose.position.y = -2.5
    m.pose.pose.orientation = Quaternion(x=0.0, y=0.0, z=math.sin(i / 9), w=math.cos(i / 9))
    m.pose.covariance = [k * 0.1 + i for k in range(36)]
    m.twist.twist.linear.x = 0.25
    m.twist.twist.angular.z = -0.125 * i
    m.twist.covariance = [(35 - k) * 0.01 for k in range(36)]
    return m


def make_image(i: int) -> Image:
    m = Image()
    _stamp(m, i)
    m.width, m.height, m.encoding, m.step = 4, 4, "rgb8", 12
    m.data = bytes([i % 251]) * 48
    return m


def main() -> None:
    status = Path(sys.argv[1])
    reliable = QoSProfile(history=HistoryPolicy.KEEP_LAST, depth=10)
    sensor = QoSProfile(
        history=HistoryPolicy.KEEP_LAST, depth=10, reliability=ReliabilityPolicy.BEST_EFFORT
    )
    latched = QoSProfile(
        history=HistoryPolicy.KEEP_LAST, depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL
    )
    rclpy.init()
    node = rclpy.create_node("decode_pub")
    moving = [
        (node.create_publisher(String, "/decode/string", reliable), make_string),
        (node.create_publisher(LaserScan, "/decode/scan", sensor), make_scan),
        (node.create_publisher(Odometry, "/decode/odom", reliable), make_odom),
        (node.create_publisher(Image, "/decode/image", reliable), make_image),
    ]
    silent = node.create_publisher(String, "/decode/silent", reliable)
    latched_pub = node.create_publisher(String, "/decode/latched", latched)
    latched_pub.publish(String(data="decode latched 1"))
    i = 0
    next_t = time.monotonic()
    last_status = 0.0
    while True:
        for pub, make in moving:
            pub.publish(make(i))
        i += 1
        rclpy.spin_once(node, timeout_sec=0)
        now = time.monotonic()
        if now - last_status >= 1.0:
            last_status = now
            counts = {p.topic_name: p.get_subscription_count() for p, _ in moving}
            counts[silent.topic_name] = silent.get_subscription_count()
            counts[latched_pub.topic_name] = latched_pub.get_subscription_count()
            tmp = status.with_suffix(".tmp")
            tmp.write_text(json.dumps(counts), encoding="utf-8")
            tmp.replace(status)
        next_t += 1.0 / RATE_HZ
        pause = next_t - time.monotonic()
        if pause > 0:
            time.sleep(pause)


if __name__ == "__main__":
    main()
