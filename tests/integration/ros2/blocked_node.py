"""A node whose executor is stuck (runs inside the bench container).

`/bench_blocked` announces its parameter services on the graph, then blocks in a timer
callback for good, so nothing ever answers a parameter request. `ros2 node info` still
lists it; `ros2 param dump` waits for ever. `get_node_info` must report that within its
deadline instead of hanging.
"""

from __future__ import annotations

import time

import rclpy
from rclpy.node import Node


class BlockedNode(Node):
    def __init__(self) -> None:
        super().__init__("bench_blocked")
        self.create_timer(0.5, self._block)

    def _block(self) -> None:
        time.sleep(86400)


def main() -> None:
    rclpy.init()
    node = BlockedNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        rclpy.shutdown()


if __name__ == "__main__":
    main()
