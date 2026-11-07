"""Stand-in for `rclpy.node.Node`: a subscription fires `FAKE_RCLPY_MESSAGES` times."""

import os
import time

import rclpy
from rclpy import serialization


class Node:
    def __init__(self, name, **options):
        self.name = name
        rclpy.trace(f"node {name} {sorted(options)}")

    def create_subscription(self, cls, topic, callback, qos, raw=False):
        rclpy.trace(
            f"subscription {topic} reliability={qos.reliability} durability={qos.durability} "
            f"depth={qos.depth}"
        )
        for index in range(int(os.environ.get("FAKE_RCLPY_MESSAGES", "0"))):
            rclpy.pending_callbacks.append(
                lambda i=index: callback(serialization.serialize_index(i) if raw else cls(i))
            )

    def destroy_node(self):
        time.sleep(float(os.environ.get("FAKE_RCLPY_HANG_DESTROY", "0")))
        rclpy.trace("destroy_node")
