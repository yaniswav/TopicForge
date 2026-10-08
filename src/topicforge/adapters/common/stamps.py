"""Message timestamp extraction shared by the live ROS 2 and the bag readers.

Pure functions over a decoded payload (nested dicts and lists), tested without
ROS 2. The rule, in order:

1. a top-level `header.stamp` -> source `header`;
2. a few well-known messages that keep their time in the body -> source
   `payload`: `rosgraph_msgs/Clock` (`clock`), `tf2_msgs/TFMessage`
   (`transforms[0].header.stamp`, only when the list is not empty) and
   `rcl_interfaces/Log` (`stamp`, recognised by its `level` and `msg` fields);
3. otherwise no stamp (`None`).
"""

from __future__ import annotations

from typing import Any, Literal

StampKind = Literal["header", "payload"]

_NS_PER_S = 1_000_000_000


def time_to_ns(stamp: object) -> int | None:
    """Nanoseconds of a `builtin_interfaces/Time` mapping, or `None` if it is not one."""
    if not isinstance(stamp, dict):
        return None
    sec, nanosec = stamp.get("sec"), stamp.get("nanosec")
    if not _is_int(sec) or not _is_int(nanosec):
        return None
    return int(sec) * _NS_PER_S + int(nanosec)


def payload_stamp(payload: dict[str, Any]) -> tuple[int, StampKind] | None:
    """The message's own time in nanoseconds and where it was found, or `None`."""
    header = payload.get("header")
    ns = time_to_ns(header.get("stamp")) if isinstance(header, dict) else None
    if ns is not None:
        return ns, "header"
    ns = _body_stamp_ns(payload)
    return None if ns is None else (ns, "payload")


def _body_stamp_ns(payload: dict[str, Any]) -> int | None:
    """Time kept in the body of `Clock`, `TFMessage` or `Log`; `None` for any other shape."""
    if "clock" in payload:
        return time_to_ns(payload["clock"])
    transforms = payload.get("transforms")
    if isinstance(transforms, list):
        first = transforms[0] if transforms else None
        header = first.get("header") if isinstance(first, dict) else None
        return time_to_ns(header.get("stamp")) if isinstance(header, dict) else None
    if "level" in payload and "msg" in payload:
        return time_to_ns(payload.get("stamp"))
    return None


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
