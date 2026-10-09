"""Summary of `sensor_msgs/msg/LaserScan`: counts, closest obstacle and sector minima."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from topicforge.models.summaries import LaserScanSummary, ScanReturn, ScanSector, ScanSectors
from topicforge.services.summaries._common import as_float, frame_id, non_finite_kind

# Sector edges in radians. A beam exactly on an edge belongs to the narrower
# sector (front, then left or right). The epsilon (about 0.006 degrees) absorbs
# float32 rounding of the angle fields, which would otherwise push a beam at
# exactly 135 degrees into the rear sector.
_FRONT_HALF = math.radians(45.0)
_SIDE_EDGE = math.radians(135.0)
_EPS = 1e-4

_SECTOR_NAMES = ("front", "left", "right", "rear")


def sector_of(bearing: float) -> str:
    """Sector name for a bearing in -pi..pi (sensor frame)."""
    if abs(bearing) <= _FRONT_HALF + _EPS:
        return "front"
    if abs(bearing) > _SIDE_EDGE + _EPS:
        return "rear"
    return "left" if bearing > 0 else "right"


def _wrap(angle: float) -> float:
    """Angle wrapped into -pi..pi."""
    return math.atan2(math.sin(angle), math.cos(angle))


def summarize_laser_scan(
    payload: Mapping[str, Any], cut_fields: frozenset[str] = frozenset()
) -> LaserScanSummary | None:
    """Summarize a decoded `LaserScan`, or `None` when `ranges` is not the whole array.

    A `ranges` replaced by the CLI's `<sequence ...>` text, or listed in
    `cut_fields`, cannot be summarized.
    """
    ranges = payload.get("ranges")
    if not isinstance(ranges, list) or "ranges" in cut_fields:
        return None

    angle_min = as_float(payload.get("angle_min"))
    angle_max = as_float(payload.get("angle_max"))
    increment = _increment(as_float(payload.get("angle_increment")), angle_min, angle_max, ranges)
    low, high = as_float(payload.get("range_min")), as_float(payload.get("range_max"))
    bounded = low is not None and high is not None and high > low
    start = angle_min if increment is not None else None

    counts = {"finite": 0, "inf": 0, "-inf": 0, "nan": 0, "out": 0}
    best: dict[str, ScanReturn | None] = dict.fromkeys(_SECTOR_NAMES)
    beams = dict.fromkeys(_SECTOR_NAMES, 0)
    closest: ScanReturn | None = None

    for index, value in enumerate(ranges):
        bearing = None if start is None else _wrap(start + index * increment)  # type: ignore[operator]
        sector = None if bearing is None else sector_of(bearing)
        if sector is not None:
            beams[sector] += 1
        number = as_float(value)
        if number is None:
            counts[non_finite_kind(value)] += 1
            continue
        counts["finite"] += 1
        if bounded and not (low <= number <= high):  # type: ignore[operator]
            counts["out"] += 1
            continue
        if bearing is None or sector is None:
            continue
        found = ScanReturn(range=number, bearing=bearing, beam_index=index)
        current = best[sector]
        if current is None or number < current.range:
            best[sector] = found
        if closest is None or number < closest.range:
            closest = found

    return LaserScanSummary(
        summary_type="laser_scan",
        frame_id=frame_id(payload),
        beam_count=len(ranges),
        angle_min=angle_min,
        angle_max=angle_max,
        angle_increment=increment,
        range_min=low,
        range_max=high,
        finite_count=counts["finite"],
        inf_count=counts["inf"],
        neg_inf_count=counts["-inf"],
        nan_count=counts["nan"],
        out_of_range_count=counts["out"],
        closest_obstacle=closest,
        sectors=ScanSectors(
            **{
                name: _sector(name, beams[name], best[name], start is not None)
                for name in _SECTOR_NAMES
            }
        ),
        note=_scan_note(len(ranges), start is not None, closest),
    )


def _increment(
    stated: float | None, angle_min: float | None, angle_max: float | None, ranges: list[Any]
) -> float | None:
    """`angle_increment` as stated, else derived from the angle span, else `None`."""
    if angle_min is None:
        return None
    if stated is not None and stated != 0.0:
        return stated
    if angle_max is not None and len(ranges) > 1:
        derived = (angle_max - angle_min) / (len(ranges) - 1)
        return derived if derived != 0.0 else None
    return None


def _sector(name: str, beam_count: int, found: ScanReturn | None, geometry: bool) -> ScanSector:
    if found is not None:
        return ScanSector(beam_count=beam_count, closest=found)
    if not geometry:
        note = "The scan has no angle geometry, so beams cannot be placed in a sector."
    elif beam_count == 0:
        note = f"The scan does not cover the {name} sector."
    else:
        note = f"No valid return in the {name} sector."
    return ScanSector(beam_count=beam_count, closest=None, note=note)


def _scan_note(beam_count: int, geometry: bool, closest: ScanReturn | None) -> str | None:
    if beam_count == 0:
        return "The scan has no ranges."
    if not geometry:
        return "The scan has no angle geometry: counts are given, bearings are not."
    if closest is None:
        return (
            "No beam has a valid return: every range is inf, nan or outside range_min..range_max."
        )
    return None
