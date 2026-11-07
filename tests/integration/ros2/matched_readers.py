"""A writer that prints how many readers it is matched with, for the ghost-reader test.

Runs under the distro's Python with ROS 2 sourced (`test_live_adapter.py` starts it). Publishes
`/sampler_ghost_check` at 10 Hz and prints `get_subscription_count()` after each message: the
writer's own view, which is where a killed reader stays as a ghost.
"""

from __future__ import annotations

import contextlib

import rclpy
from std_msgs.msg import String

rclpy.init()
node = rclpy.create_node("bench_matched_readers")
writer = node.create_publisher(String, "/sampler_ghost_check", 10)


def tick() -> None:
    writer.publish(String(data="x"))
    print(writer.get_subscription_count(), flush=True)


node.create_timer(0.1, tick)
with contextlib.suppress(KeyboardInterrupt):
    rclpy.spin(node)
# Leave at once: the ground-truth passes that follow compare the exact node list.
node.destroy_node()
rclpy.try_shutdown()
