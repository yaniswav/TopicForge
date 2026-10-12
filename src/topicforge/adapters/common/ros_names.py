"""ROS 2 name mangling on top of DDS topic names.

ROS 2 maps its topics to DDS topics by prefix: `/scan` is `rt/scan`, a service request is
`rq/<name>Request`, its reply `rr/<name>Reply`, and so on. Pure and binding-free.
"""

from __future__ import annotations

_TOPIC_PREFIX = "rt/"

# Prefixes of the ROS 2 service and action endpoints, plus the middleware's own
# discovery topic: hidden from `list_endpoints` unless `include_internal` is true.
_INTERNAL_PREFIXES: tuple[str, ...] = ("rq/", "rr/", "rs/", "rp/", "ra/")
_INTERNAL_NAMES: frozenset[str] = frozenset({"ros_discovery_info"})


def is_internal_dds_topic(dds_topic: str) -> bool:
    """True for ROS 2 service and action endpoints and `ros_discovery_info`."""
    return dds_topic in _INTERNAL_NAMES or dds_topic.startswith(_INTERNAL_PREFIXES)


def ros_topic_of(dds_topic: str) -> tuple[str | None, str | None]:
    """`(ros_topic, None)` for a ROS 2 topic, else `(None, note)` saying why there is none.

    `rt/scan` gives `/scan`.
    """
    if dds_topic.startswith(_TOPIC_PREFIX) and len(dds_topic) > len(_TOPIC_PREFIX):
        return "/" + dds_topic[len(_TOPIC_PREFIX) :], None
    if is_internal_dds_topic(dds_topic):
        return None, "ROS 2 service, action or discovery endpoint, not a topic."
    return None, "Not a ROS 2 topic: the DDS name has no `rt/` prefix."
