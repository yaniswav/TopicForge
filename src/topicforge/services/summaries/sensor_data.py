"""Summaries of `sensor_msgs/msg/Image` and `sensor_msgs/msg/PointCloud2`.

Neither reads the pixel or point buffer: the geometry fields say everything
the summary reports, so the buffer can stay cut or summarized in the payload.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from topicforge.models.summaries import ImageSummary, PointCloud2Summary, PointFieldSummary
from topicforge.services.summaries._common import as_int, frame_id, text

_POINT_FIELD_TYPES = {
    1: "INT8",
    2: "UINT8",
    3: "INT16",
    4: "UINT16",
    5: "INT32",
    6: "UINT32",
    7: "FLOAT32",
    8: "FLOAT64",
}
# The CLI prints an array it was told to summarize as `<sequence type: uint8, length: 921600>`.
_SEQUENCE_LENGTH = re.compile(r"length:\s*(\d+)")


def summarize_image(
    payload: Mapping[str, Any], cut_fields: frozenset[str] = frozenset()
) -> ImageSummary | None:
    """Size, encoding and buffer length of a decoded `Image`."""
    width, height = as_int(payload.get("width")), as_int(payload.get("height"))
    step = as_int(payload.get("step"))
    length, basis = _buffer_length(payload.get("data"), "data" in cut_fields, step, height)
    bigendian = payload.get("is_bigendian")
    missing = [n for n, v in (("width", width), ("height", height), ("step", step)) if v is None]
    return ImageSummary(
        summary_type="image",
        frame_id=frame_id(payload),
        width=width,
        height=height,
        encoding=text(payload.get("encoding")),
        step=step,
        # `ros2 topic echo` prints is_bigendian as 0/1 for the uint8 field.
        is_bigendian=None if bigendian is None else bool(bigendian),
        data_length=length,
        data_length_basis=basis,
        note=f"Missing fields: {', '.join(missing)}." if missing else None,
    )


def _buffer_length(
    data: object, cut: bool, step: int | None, height: int | None
) -> tuple[int | None, str | None]:
    """`(length, basis)` of the pixel buffer without reading it."""
    if isinstance(data, str):
        found = _SEQUENCE_LENGTH.search(data)
        if found:
            return int(found.group(1)), "payload"
    elif isinstance(data, list) and not cut:
        return len(data), "payload"
    if step is not None and height is not None:
        return step * height, "step_x_height"
    return None, None


def summarize_point_cloud2(
    payload: Mapping[str, Any], cut_fields: frozenset[str] = frozenset()
) -> PointCloud2Summary | None:
    """Point count and layout of a decoded `PointCloud2`."""
    width, height = as_int(payload.get("width")), as_int(payload.get("height"))
    count = width * height if width is not None and height is not None else None
    fields, problem = _point_fields(payload.get("fields"))
    dense = payload.get("is_dense")
    note = problem
    if count == 0:
        note = "The cloud has no points."
    elif count is None:
        note = "width or height is missing."
    return PointCloud2Summary(
        summary_type="point_cloud2",
        frame_id=frame_id(payload),
        width=width,
        height=height,
        point_count=count,
        point_step=as_int(payload.get("point_step")),
        row_step=as_int(payload.get("row_step")),
        is_dense=None if dense is None else bool(dense),
        fields=fields,
        note=note,
    )


def _point_fields(raw: object) -> tuple[list[PointFieldSummary], str | None]:
    """The point layout, and a sentence when `fields` was not read as a list."""
    if not isinstance(raw, list):
        return [], "The point layout (fields) was not read."
    out: list[PointFieldSummary] = []
    for item in raw:
        if not isinstance(item, Mapping) or not isinstance(item.get("name"), str):
            continue
        code = as_int(item.get("datatype"))
        out.append(
            PointFieldSummary(
                name=item["name"],
                datatype=_POINT_FIELD_TYPES.get(code, "UNKNOWN") if code is not None else "UNKNOWN",
                offset=as_int(item.get("offset")),
                count=as_int(item.get("count")),
            )
        )
    return out, None
