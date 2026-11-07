"""`get_image` for the live adapter: one raw CDR frame from the sampler helper, decoded here.

There is no CLI path: `ros2 topic echo` prints YAML, and a frame's worth of it is the very thing the
helper exists to avoid. Wired like `sampler.sample_messages`: the adapter hands in what it owns.
"""

from __future__ import annotations

from topicforge import budget as call_budget
from topicforge.adapters.base import AdapterError, EffectiveMode
from topicforge.adapters.common.image_decode import IMAGE_TYPES, decode_image
from topicforge.adapters.ros2_live.helper_stream import (
    SamplingBackend,
    failure_text,
    helper_command,
    stream_helper,
)
from topicforge.adapters.ros2_live.sampler import (
    NO_PUBLISHER_WAIT_SEC,
    STOP_RESERVE_SEC,
    TopicInfoFn,
    echo_qos_args,
    look_up_topic,
)
from topicforge.constants import ROS2_CLI_TIMEOUT_S
from topicforge.models.image import CapturedImage

# Far above a 4K frame in rgb16; a larger message is counted and never read.
MAX_IMAGE_MESSAGE_BYTES = 64 * 1024 * 1024


def capture_image(
    topic: str,
    *,
    topic_info: TopicInfoFn,
    mode: EffectiveMode,
    backend: SamplingBackend,
    max_edge: int,
    max_bytes: int,
    timeout_s: float,
) -> CapturedImage:
    """One frame of `topic` within `timeout_s`, under the publishers' QoS."""
    if backend.kind != "rclpy" or not backend.python:
        raise AdapterError(
            "`get_image` needs the sampling helper, and this server samples with the `ros2` CLI "
            f"({backend.note or 'no helper'}). Check `TOPICFORGE_ROS_SAMPLER` and "
            "`TOPICFORGE_ROS_PYTHON`."
        )
    timeout_s = call_budget.clamp(timeout_s, STOP_RESERVE_SEC)
    if timeout_s <= 0:
        raise AdapterError("busy: no time left in this call's budget for sampling, retry")
    info = look_up_topic(topic_info, topic, min(ROS2_CLI_TIMEOUT_S, timeout_s))
    if info.message_type not in IMAGE_TYPES:
        raise AdapterError(
            f"{topic} is {info.message_type}: `get_image` reads sensor_msgs/Image and "
            "CompressedImage. `sample_messages` reads any other type."
        )
    wait_s = timeout_s if info.publisher_count > 0 else min(timeout_s, NO_PUBLISHER_WAIT_SEC)
    qos = echo_qos_args(info)
    cmd = helper_command(
        backend.python,
        topic,
        info.message_type,
        [
            "--raw",
            "--count",
            "1",
            "--deadline",
            f"{wait_s:.3f}",
            "--max-chars",
            str(MAX_IMAGE_MESSAGE_BYTES),
            *qos,
        ],
    )
    run = stream_helper(cmd, count=1, deadline_s=wait_s)
    if run.cdr_frames:
        cdr, received_ns = run.cdr_frames[0]
        return decode_image(
            topic,
            info.message_type,
            cdr,
            received_ns=received_ns,
            mode_effective=mode,
            max_edge=max_edge,
            max_bytes=max_bytes,
        )
    if run.oversized:
        raise AdapterError(f"the images of {topic} are over {MAX_IMAGE_MESSAGE_BYTES} bytes.")
    if run.exit_code not in (None, 0):
        raise AdapterError(failure_text(run))
    raise AdapterError(_nothing_arrived(topic, info.publisher_count, wait_s, qos))


def _nothing_arrived(topic: str, publishers: int, wait_s: float, qos: list[str]) -> str:
    """Why no frame came: the three causes a caller can act on."""
    head = f"No image arrived on {topic} within {wait_s:g} s"
    if publishers == 0:
        return f"{head}: no publisher is announced on it."
    if "best_effort" in qos:
        return (
            f"{head}. The publisher is best-effort, and over DDS a frame of several MiB can lose a "
            "fragment and be dropped whole: a reliable publisher, a smaller frame or a "
            "CompressedImage topic avoids that."
        )
    return f"{head}: the publisher may be idle or slower than that."
