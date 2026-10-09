"""Classification of a bag topic: data, rosbag2 bookkeeping or ROS 2 infrastructure.

Pure and binding-free, so every bag path (`ros2 bag info`, the `rosbags` reader and the
mock fixtures) labels topics the same way. See `docs/CONTRACT.md` section 3.5.
"""

from __future__ import annotations

from typing import Literal

BagTopicKind = Literal["user", "rosbag2_internal", "ros_builtin"]

# Topics rosbag2 writes by itself while recording or playing.
_ROSBAG2_INTERNAL: frozenset[str] = frozenset({"/events/write_split", "/events/read_split"})

# ROS 2 infrastructure topics. `/tf`, `/tf_static` and `/clock` are data and stay `user`.
_ROS_BUILTIN: frozenset[str] = frozenset({"/parameter_events", "/rosout"})


def classify_bag_topic(name: str) -> BagTopicKind:
    """`rosbag2_internal`, `ros_builtin` or `user` for a fully qualified topic name."""
    if name in _ROSBAG2_INTERNAL:
        return "rosbag2_internal"
    if name in _ROS_BUILTIN:
        return "ros_builtin"
    return "user"
