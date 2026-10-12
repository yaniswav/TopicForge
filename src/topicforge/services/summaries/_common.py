"""Helpers shared by the message summarizers: tolerant number reading and quaternions.

Payload values arrive as plain Python data. A non-finite float is the string
`"nan"`, `"inf"` or `"-inf"` in a live payload and may still be a float in a
freshly decoded bag message, so every reader here accepts both.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

# A quaternion shorter than this is "not set": drivers publish (0, 0, 0, 0).
_MIN_QUATERNION_NORM = 1e-6


def as_float(value: object) -> float | None:
    """The finite float behind `value`, or `None` (missing, non-numeric, nan, inf)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def as_int(value: object) -> int | None:
    """The integer behind `value`, or `None`."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def non_finite_kind(value: object) -> str:
    """`"inf"`, `"-inf"` or `"nan"` for a non-finite number; anything else counts as `"nan"`."""
    if isinstance(value, str) and value in ("inf", "-inf"):
        return value
    if isinstance(value, float) and math.isinf(value):
        return "inf" if value > 0 else "-inf"
    return "nan"


def section(payload: Mapping[str, Any], *path: str) -> Mapping[str, Any]:
    """The nested mapping at `path`, or an empty mapping when any step is missing."""
    node: Any = payload
    for key in path:
        node = node.get(key) if isinstance(node, Mapping) else None
    return node if isinstance(node, Mapping) else {}


def text(value: object) -> str | None:
    """`value` when it is a string, else `None`."""
    return value if isinstance(value, str) else None


def frame_id(payload: Mapping[str, Any]) -> str | None:
    """`header.frame_id`, `None` when absent. An empty frame id is reported as is."""
    return text(section(payload, "header").get("frame_id"))


def vector(node: Mapping[str, Any]) -> tuple[float, float, float] | None:
    """`(x, y, z)` of a `Vector3`-like mapping when all three are finite numbers."""
    x, y, z = as_float(node.get("x")), as_float(node.get("y")), as_float(node.get("z"))
    if x is None or y is None or z is None:
        return None
    return x, y, z


def norm(values: tuple[float, float, float]) -> float:
    """Euclidean norm."""
    return math.sqrt(sum(v * v for v in values))


def euler_from_quaternion(node: Mapping[str, Any]) -> tuple[float, float, float] | str:
    """`(roll, pitch, yaw)` in radians from an `x y z w` mapping, or a reason string.

    The quaternion is normalized first. A quaternion with a missing or
    non-finite part, or a norm near zero (a driver that does not estimate
    orientation publishes zeros), returns a sentence saying so instead.
    """
    x, y, z, w = (as_float(node.get(k)) for k in ("x", "y", "z", "w"))
    if x is None or y is None or z is None or w is None:
        return "the quaternion is missing or has a non-finite component"
    length = math.sqrt(x * x + y * y + z * z + w * w)
    if length < _MIN_QUATERNION_NORM:
        return "the quaternion is all zeros (orientation not set)"
    x, y, z, w = x / length, y / length, z / length, w / length
    roll = math.atan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y))
    pitch = math.asin(max(-1.0, min(1.0, 2.0 * (w * y - z * x))))
    yaw = math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
    return roll, pitch, yaw


def sentence(problems: list[str]) -> str | None:
    """The problems joined into one sentence, or `None` when there are none."""
    if not problems:
        return None
    joined = "; ".join(problems)
    return joined[0].upper() + joined[1:] + "."
