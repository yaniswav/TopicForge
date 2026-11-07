"""Turn the raw CDR of one image message into the bytes `get_image` returns. Binding-free.

`sensor_msgs/CompressedImage` passes through byte for byte: JPEG or PNG, told by its signature, not
by the free-text `format`. `sensor_msgs/Image` goes through Pillow (the `[image]` extra): rgb8, bgr8,
mono8, and mono16 scaled from its own minimum and maximum to eight bits, resized to `max_edge` and
encoded as PNG, so an image that fits comes back equal, pixel for pixel. Decoding uses `rosbags`,
which needs no ROS 2 install, with the store of `ROS_DISTRO`, else Humble; the two image types have
not changed across distros.
"""

from __future__ import annotations

import hashlib
import io
import os
import struct
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from topicforge.adapters.base import AdapterError
from topicforge.models.image import CapturedImage, ImageInfo

RAW_TYPE = "sensor_msgs/msg/Image"
COMPRESSED_TYPE = "sensor_msgs/msg/CompressedImage"
IMAGE_TYPES = frozenset({RAW_TYPE, COMPRESSED_TYPE})

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SIGNATURE = b"\xff\xd8\xff"
# Bytes per pixel of what Pillow can take after the conversion below.
_CHANNELS = {"rgb8": 3, "bgr8": 3, "mono8": 1, "mono16": 2}
_JPEG_FRAME_MARKERS = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}
_JPEG_NO_LENGTH = frozenset({0x01, *range(0xD0, 0xD8)})


@dataclass(frozen=True)
class _Pixels:
    """What is left of a message once it is an encoded picture."""

    data: bytes
    mime_type: str
    encoding: str
    width: int | None
    height: int | None
    returned_width: int | None
    returned_height: int | None


@lru_cache(maxsize=1)
def _typestore(distro: str) -> Any:
    from rosbags.typesys import Stores, get_typestore  # type: ignore[import-not-found]

    store = Stores.__members__.get(f"ROS2_{distro.upper()}", Stores.ROS2_HUMBLE)
    return get_typestore(store)


def decode_image(
    topic: str,
    message_type: str,
    cdr: bytes,
    *,
    received_ns: int | None,
    mode_effective: str,
    max_edge: int,
    max_bytes: int,
) -> CapturedImage:
    """Decode `cdr`; raises `AdapterError` for what cannot be returned, saying why."""
    try:
        store = _typestore(os.environ.get("ROS_DISTRO", "humble"))
        msg = store.deserialize_cdr(cdr, message_type)
    except Exception as exc:
        raise AdapterError(
            f"could not decode the {message_type} message of {topic}: {type(exc).__name__}: {exc}"
        ) from exc
    pixels = _compressed(msg) if message_type == COMPRESSED_TYPE else _raw(msg, max_edge)
    if len(pixels.data) > max_bytes:
        hint = "lower `max_edge`" if message_type == RAW_TYPE else "`max_edge` does not apply here"
        raise AdapterError(
            f"the image of {topic} is {len(pixels.data)} bytes, over the {max_bytes} byte cap "
            f"(4 x `TOPICFORGE_MAX_SAMPLE_BYTES`): {hint}."
        )
    stamp = msg.header.stamp
    info = ImageInfo(
        topic=topic,
        message_type=message_type,
        frame_id=msg.header.frame_id,
        stamp_ns=stamp.sec * 1_000_000_000 + stamp.nanosec,
        received_ns=received_ns,
        encoding=pixels.encoding,
        width=pixels.width,
        height=pixels.height,
        returned_width=pixels.returned_width,
        returned_height=pixels.returned_height,
        mime_type=pixels.mime_type,
        byte_count=len(pixels.data),
        sha256=hashlib.sha256(pixels.data).hexdigest(),
        mode_effective=mode_effective,
    )
    return CapturedImage(info=info, data=pixels.data)


def _compressed(msg: Any) -> _Pixels:
    data = msg.data.tobytes()
    if data.startswith(_PNG_SIGNATURE):
        mime = "image/png"
    elif data.startswith(_JPEG_SIGNATURE):
        mime = "image/jpeg"
    else:
        raise AdapterError(
            f"this CompressedImage (format {msg.format!r}) is neither JPEG nor PNG: only those two "
            "are returned (`compressedDepth` carries a header before its PNG)."
        )
    width, height = compressed_size(data)
    return _Pixels(data, mime, msg.format, width, height, width, height)


def compressed_size(data: bytes) -> tuple[int | None, int | None]:
    """Width and height from a PNG or JPEG header, `(None, None)` when it cannot be told."""
    if data.startswith(_PNG_SIGNATURE):
        if len(data) < 24:
            return None, None
        width, height = struct.unpack(">II", data[16:24])
        return width, height
    at = 2
    while at + 4 <= len(data) and data[at] == 0xFF:
        marker = data[at + 1]
        if marker == 0xFF:  # fill byte
            at += 1
        elif marker in _JPEG_NO_LENGTH:
            at += 2
        elif marker in _JPEG_FRAME_MARKERS:
            if at + 9 > len(data):
                return None, None
            height, width = struct.unpack(">HH", data[at + 5 : at + 9])
            return width, height
        else:
            at += 2 + struct.unpack(">H", data[at + 2 : at + 4])[0]
    return None, None


def _raw(msg: Any, max_edge: int) -> _Pixels:
    encoding, width, height, step = msg.encoding, msg.width, msg.height, msg.step
    channels = _CHANNELS.get(encoding)
    if channels is None:
        raise AdapterError(
            f"encoding {encoding!r} is not supported: rgb8, bgr8, mono8 and mono16 are; use a "
            "`sample_messages` read of the fields for anything else."
        )
    row = width * channels
    data = msg.data
    if width <= 0 or height <= 0 or step < row or len(data) < step * height:
        raise AdapterError(
            f"inconsistent image: {width}x{height} {encoding}, step {step}, {len(data)} bytes"
        )
    try:
        from PIL import Image as Pillow  # type: ignore[import-not-found]
    except ImportError as exc:
        raise AdapterError(
            'a raw sensor_msgs/Image needs Pillow: pip install "topicforge[image]" '
            "(a CompressedImage does not)."
        ) from exc
    picture = Pillow.fromarray(_pixel_array(data, height, step, row, encoding, msg.is_bigendian))
    longest = max(width, height)
    if longest > max_edge:
        size = (max(width * max_edge // longest, 1), max(height * max_edge // longest, 1))
        picture = picture.resize(size, Pillow.Resampling.LANCZOS)
    out = io.BytesIO()
    picture.save(out, format="PNG")
    return _Pixels(
        out.getvalue(), "image/png", encoding, width, height, picture.width, picture.height
    )


def _pixel_array(
    data: Any, height: int, step: int, row: int, encoding: str, bigendian: int | bool
) -> Any:
    """Rows without their padding, as RGB or eight-bit gray."""
    import numpy as np

    rows = np.asarray(data, dtype=np.uint8)[: step * height].reshape(height, step)[:, :row]
    if encoding == "rgb8":
        return rows.reshape(height, row // 3, 3)
    if encoding == "bgr8":
        return rows.reshape(height, row // 3, 3)[:, :, ::-1]
    if encoding == "mono8":
        return rows
    wide = rows.tobytes()
    values = np.frombuffer(wide, dtype=">u2" if bigendian else "<u2").reshape(height, row // 2)
    low, high = int(values.min()), int(values.max())
    if high == low:
        return np.zeros((height, row // 2), dtype=np.uint8)
    return ((values.astype(np.float64) - low) * 255.0 / (high - low)).astype(np.uint8)


__all__ = ["COMPRESSED_TYPE", "IMAGE_TYPES", "RAW_TYPE", "compressed_size", "decode_image"]
