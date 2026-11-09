"""What `get_image` returns besides the picture.

`ImageInfo` goes out as `structuredContent` and as a JSON text block, with no published output
schema: that schema would be sent on every `tools/list` (docs/CONTRACT.md section 9.30). `get_image`
is the one tool that does not return a Pydantic model alone, because an image is a content block.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from topicforge.models.schemas import _CONFIG


class ImageInfo(BaseModel):
    """Metadata of one captured image. `frame_id` and `encoding` are cut at 256 characters."""

    model_config = _CONFIG

    topic: str
    message_type: str
    frame_id: str
    stamp_ns: int
    received_ns: int | None
    encoding: str
    width: int | None
    height: int | None
    returned_width: int | None
    returned_height: int | None
    mime_type: str
    byte_count: int
    sha256: str
    mode_effective: str


@dataclass(frozen=True)
class CapturedImage:
    """One image as an adapter hands it over: its bytes and what is known about them."""

    info: ImageInfo
    data: bytes


__all__ = ["CapturedImage", "ImageInfo"]
