"""Per-type message summaries and topic rate verdicts.

Pure and binding-free: everything here works on decoded payloads (nested dicts
and lists) and integer times, and is tested without ROS 2. A summary is
auxiliary, so `summarize_message` never raises: a bug or an odd message gives
`None`, not a failed tool call.

Adapters import this package inside functions, not at module level: importing
`topicforge.services` loads the factory, which loads the adapters.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection, Mapping
from typing import Any

from topicforge.models.summaries import MessageSummary
from topicforge.services.summaries.laser_scan import summarize_laser_scan
from topicforge.services.summaries.motion import summarize_imu, summarize_odometry
from topicforge.services.summaries.rate import compute_rate
from topicforge.services.summaries.sensor_data import summarize_image, summarize_point_cloud2

__all__ = ["compute_rate", "needs_whole_arrays", "summarize_message", "supports_summary"]

log = logging.getLogger(__name__)

Summarizer = Callable[[Mapping[str, Any], frozenset[str]], MessageSummary | None]

# Keyed by `<package>/<Name>`, the form of a message type without its `msg` part.
_SUMMARIZERS: dict[str, Summarizer] = {
    "sensor_msgs/LaserScan": summarize_laser_scan,
    "nav_msgs/Odometry": summarize_odometry,
    "sensor_msgs/Imu": summarize_imu,
    "sensor_msgs/Image": summarize_image,
    "sensor_msgs/PointCloud2": summarize_point_cloud2,
}

# The summaries that read an array, so the array must reach them whole. Image
# and PointCloud2 summaries use geometry fields only: their buffers stay cut.
_NEEDS_WHOLE_ARRAYS = frozenset({"sensor_msgs/LaserScan"})


def _key(message_type: str) -> str:
    """`sensor_msgs/msg/LaserScan` (or ROS 1 `sensor_msgs/LaserScan`) -> `sensor_msgs/LaserScan`."""
    parts = [p for p in message_type.split("/") if p not in ("msg", "")]
    return "/".join(parts)


def supports_summary(message_type: str) -> bool:
    """Whether a summarizer exists for `message_type`."""
    return _key(message_type) in _SUMMARIZERS


def needs_whole_arrays(message_type: str) -> bool:
    """Whether the summary of `message_type` reads an array and so needs it uncut."""
    return _key(message_type) in _NEEDS_WHOLE_ARRAYS


def summarize_message(
    message_type: str,
    payload: Mapping[str, Any],
    cut_fields: Collection[str] = (),
) -> MessageSummary | None:
    """The summary of a decoded message, or `None` for an unsupported type or an odd message.

    `cut_fields` lists the dotted paths of arrays in `payload` that are cut or
    replaced by text; a summary that needs one of them returns `None`.
    """
    summarizer = _SUMMARIZERS.get(_key(message_type))
    if summarizer is None:
        return None
    try:
        return summarizer(payload, frozenset(cut_fields))
    except Exception as exc:  # a summary must never fail a sampling call
        log.debug("summary of %s failed: %s", message_type, exc)
        return None
