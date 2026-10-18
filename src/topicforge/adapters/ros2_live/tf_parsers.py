"""Pure parsers for the `tf2_msgs/msg/TFMessage` documents `ros2 topic echo` prints.

Module-level functions over CLI text, tested without ROS 2. The process side
(streaming, deadlines, QoS) lives in `Ros2CliAdapter.capture_tf`.
"""

from __future__ import annotations

from topicforge.adapters.common.stamps import time_to_ns
from topicforge.adapters.ros2_live.echo_parser import RAW_TEXT_KEY, parse_echo_document
from topicforge.models import TfSample


def parse_tf_message(document: str, received_ns: int) -> list[TfSample] | None:
    """The transforms of one echoed `TFMessage`, or `None` when it is not one.

    A transform whose frame names are missing is skipped; a missing or malformed stamp
    counts as 0. `received_ns` is the wall clock at which the document arrived.
    """
    message = parse_echo_document(document, truncate_length=None)
    transforms = message.payload.get("transforms")
    if RAW_TEXT_KEY in message.payload or not isinstance(transforms, list):
        return None
    out: list[TfSample] = []
    for item in transforms:
        if not isinstance(item, dict):
            continue
        header = item.get("header")
        header = header if isinstance(header, dict) else {}
        parent, child = header.get("frame_id"), item.get("child_frame_id")
        if not isinstance(parent, str) or not isinstance(child, str) or not parent or not child:
            continue
        stamp = time_to_ns(header.get("stamp")) or 0
        out.append(
            TfSample(
                parent=parent.lstrip("/"),
                child=child.lstrip("/"),
                stamp_ns=max(stamp, 0),
                received_ns=received_ns,
            )
        )
    return out


__all__ = ["parse_tf_message"]
