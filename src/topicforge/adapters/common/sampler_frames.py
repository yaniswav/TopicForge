"""Frames on the stdout of the sampler helper (`ros2_live/_sampler_helper.py`). Pure.

The helper cannot import this package, so it carries its own encoder; a test keeps the two
together. A frame is a 12-byte header (`TF`, version, kind, metadata length, body length, big
endian), UTF-8 JSON metadata, then the body:

- `h`, hello: the subscription exists. Metadata `pid`, `type`, `wall_ns`.
- `m`, message: metadata `wall_ns` (wall clock at reception) and `encoding`, with the body in that
  encoding. `yaml` is the text `ros2 topic echo` prints. `cdr` (raw bytes, for an image) and the
  optional `source_ns` and `received_ns` (middleware timestamps, for time windows) are decoded
  here but the helper does not send them yet. In a time window `index` numbers the message among
  all that arrived and `stride` says that, from now on, only indexes divisible by it are kept.
- `d`, dropped: a message over the helper's `--max-chars`, counted and not sent. Metadata `wall_ns`.
- `a`, arrival: a message of a time window that the stride does not select: only `wall_ns`.

Unknown metadata keys are kept, so a newer helper field does not break a reader.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from typing import IO, Any

MAGIC = b"TF"
VERSION = 1
KIND_HELLO = "h"
KIND_MESSAGE = "m"
KIND_DROPPED = "d"
KIND_ARRIVAL = "a"
ENCODING_YAML = "yaml"
ENCODING_CDR = "cdr"

_HEADER = struct.Struct(">2sBBII")
_KINDS = frozenset({KIND_HELLO, KIND_MESSAGE, KIND_DROPPED, KIND_ARRIVAL})
_MAX_META_BYTES = 64 * 1024
# Far above the largest accepted `TOPICFORGE_MAX_SAMPLE_BYTES`: a length past it is corruption.
MAX_BODY_BYTES = 1024 * 1024 * 1024


class FrameError(ValueError):
    """The bytes are not a frame of this version."""


@dataclass(frozen=True)
class Frame:
    """One decoded frame."""

    kind: str
    meta: dict[str, Any] = field(default_factory=dict)
    body: bytes = b""

    @property
    def wall_ns(self) -> int:
        """Wall clock at reception, 0 when the frame has none."""
        value = self.meta.get("wall_ns")
        return value if isinstance(value, int) else 0

    @property
    def index(self) -> int:
        """Position of a window's message among all that arrived, 0 when the frame has none."""
        value = self.meta.get("index")
        return value if isinstance(value, int) else 0

    @property
    def stride(self) -> int:
        """Stride of a window from this frame on, 1 when the frame has none."""
        value = self.meta.get("stride")
        return value if isinstance(value, int) and value > 0 else 1

    @property
    def encoding(self) -> str | None:
        """Encoding of the body of a message frame."""
        value = self.meta.get("encoding")
        return value if isinstance(value, str) else None

    @property
    def source_ns(self) -> int | None:
        """The publisher's middleware timestamp, when sent."""
        value = self.meta.get("source_ns")
        return value if isinstance(value, int) else None

    @property
    def received_ns(self) -> int | None:
        """The reader's middleware timestamp, when sent."""
        value = self.meta.get("received_ns")
        return value if isinstance(value, int) else None


def encode_frame(kind: str, meta: dict[str, Any], body: bytes = b"") -> bytes:
    """One frame as bytes."""
    head = json.dumps(meta, separators=(",", ":")).encode("utf-8")
    return _HEADER.pack(MAGIC, VERSION, ord(kind), len(head), len(body)) + head + body


def _read_exact(stream: IO[bytes], size: int) -> bytes | None:
    """`size` bytes, `None` when the stream ended first. A pipe may return less than asked."""
    chunks: list[bytes] = []
    left = size
    while left > 0:
        chunk = stream.read(left)
        if not chunk:
            return None
        chunks.append(chunk)
        left -= len(chunk)
    return b"".join(chunks)


def read_frame(stream: IO[bytes]) -> Frame | None:
    """The next frame, `None` at the end of the stream (also inside a frame cut by a kill).

    Raises `FrameError` for anything else that is not a frame of this version.
    """
    raw = _read_exact(stream, _HEADER.size)
    if raw is None:
        return None
    magic, version, kind_byte, meta_len, body_len = _HEADER.unpack(raw)
    kind = chr(kind_byte)
    if magic != MAGIC:
        raise FrameError(f"not a frame: starts with {magic!r}")
    if version != VERSION:
        raise FrameError(f"frame version {version}, expected {VERSION}")
    if kind not in _KINDS:
        raise FrameError(f"unknown frame kind {kind!r}")
    if meta_len > _MAX_META_BYTES or body_len > MAX_BODY_BYTES:
        raise FrameError(f"frame too long: metadata {meta_len}, body {body_len} bytes")
    head = _read_exact(stream, meta_len)
    body = _read_exact(stream, body_len)
    if head is None or body is None:
        return None
    try:
        meta = json.loads(head)
    except ValueError as exc:
        raise FrameError(f"frame metadata is not JSON: {exc}") from exc
    if not isinstance(meta, dict):
        raise FrameError("frame metadata is not an object")
    return Frame(kind, meta, body)


__all__ = [
    "ENCODING_CDR",
    "ENCODING_YAML",
    "KIND_ARRIVAL",
    "KIND_DROPPED",
    "KIND_HELLO",
    "KIND_MESSAGE",
    "MAGIC",
    "MAX_BODY_BYTES",
    "VERSION",
    "Frame",
    "FrameError",
    "encode_frame",
    "read_frame",
]
