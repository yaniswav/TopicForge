"""A managed (lifecycle) node in a namespace (runs inside the bench container).

`/robot1/bench_lifecycle` is left in the `unconfigured` state: what matters is that it
serves `/robot1/bench_lifecycle/get_state` (lifecycle_msgs/srv/GetState) and publishes
`/robot1/bench_lifecycle/transition_event`, so `list_nodes` must flag it `lifecycle` and
count it under `/robot1`. rclpy ships the lifecycle node, so the image needs no extra package.
"""

from __future__ import annotations

import rclpy

try:
    from rclpy.lifecycle import LifecycleNode
except ImportError:  # Humble exports the class only as `rclpy.lifecycle.Node`
    from rclpy.lifecycle import Node as LifecycleNode


def main() -> None:
    rclpy.init()
    node = LifecycleNode("bench_lifecycle", namespace="/robot1")
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
