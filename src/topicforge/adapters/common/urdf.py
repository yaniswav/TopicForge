"""Read the links and joints of a URDF, for `inspect_tf` (declared against published frames).

Pure functions, no `ros2` and no binding. The parser uses the standard library's
`ElementTree` and refuses any document that declares a `DOCTYPE` or an `ENTITY`, the
two ways an XML file can pull in a file or expand into gigabytes (a billion laughs).
A URDF has no use for either. A document that is not a `<robot>` with at least one
`<link>`, or that is larger than `MAX_URDF_CHARS`, is refused with a `UrdfError`
whose text is safe to show a client.

Matching. `robot_state_publisher` publishes one TF frame per link, named like the
link; with a `frame_prefix` the frame is `<prefix>/<link>`. `link_has_frame` accepts
both, and `frame_in_urdf` is the same rule read from the frame's side.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Collection
from dataclasses import dataclass

# Longest URDF read, in characters; the live adapter drops a larger message anyway.
MAX_URDF_CHARS = 4_000_000

FIXED_JOINT = "fixed"


class UrdfError(ValueError):
    """The document is not a URDF this module reads; the message is user-safe."""


@dataclass(frozen=True)
class UrdfJoint:
    """One `<joint>`: its type and the two links it joins."""

    name: str
    type: str
    parent: str
    child: str


@dataclass(frozen=True)
class UrdfModel:
    """The kinematic skeleton of a URDF: link names and joints, in document order."""

    robot_name: str | None
    links: tuple[str, ...]
    joints: tuple[UrdfJoint, ...]

    def joint_into(self, link: str) -> UrdfJoint | None:
        """The joint whose child is `link`, `None` for the root link."""
        return next((j for j in self.joints if j.child == link), None)


def parse_urdf(text: str) -> UrdfModel:
    """Links and joints of the URDF in `text`; raises `UrdfError` when it cannot be read."""
    if len(text) > MAX_URDF_CHARS:
        raise UrdfError(f"the robot description is larger than {MAX_URDF_CHARS} characters")
    lowered = text.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        raise UrdfError("the robot description declares a DOCTYPE or an ENTITY: not read")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise UrdfError(f"the robot description is not well-formed XML ({exc})") from exc
    if root.tag != "robot":
        raise UrdfError(f"the root element is <{root.tag}>, not <robot>: not a URDF")
    links = tuple(
        name for link in root.findall("link") if (name := (link.get("name") or "").strip())
    )
    if not links:
        raise UrdfError("the robot description has no <link>")
    return UrdfModel(
        robot_name=root.get("name"),
        links=links,
        joints=tuple(j for j in map(_joint, root.findall("joint")) if j is not None),
    )


def _joint(element: ET.Element) -> UrdfJoint | None:
    parent = element.find("parent")
    child = element.find("child")
    parent_link = (parent.get("link") if parent is not None else None) or ""
    child_link = (child.get("link") if child is not None else None) or ""
    if not parent_link or not child_link:
        return None
    return UrdfJoint(
        name=element.get("name") or "",
        type=(element.get("type") or "").strip(),
        parent=parent_link.strip(),
        child=child_link.strip(),
    )


def _bare(frame: str) -> str:
    return frame.lstrip("/")


def link_has_frame(link: str, frames: Collection[str]) -> bool:
    """True when a frame is named `link` or `<frame_prefix>/<link>`."""
    suffix = "/" + link
    return any(_bare(f) == link or _bare(f).endswith(suffix) for f in frames)


def frame_in_urdf(frame: str, links: Collection[str]) -> bool:
    """True when `frame` is a link name, with or without a `frame_prefix`."""
    name = _bare(frame)
    return any(name == link or name.endswith("/" + link) for link in links)


def declared_frames_missing(model: UrdfModel, frames: Collection[str]) -> list[str]:
    """Links with no TF frame, in URDF order."""
    return [link for link in model.links if not link_has_frame(link, frames)]


def frames_not_in_urdf(model: UrdfModel, frames: Collection[str]) -> list[str]:
    """TF frames that are not a link of the URDF, sorted (`map` and `odom` are legitimate)."""
    return sorted(f for f in frames if not frame_in_urdf(f, model.links))


__all__ = [
    "FIXED_JOINT",
    "MAX_URDF_CHARS",
    "UrdfError",
    "UrdfJoint",
    "UrdfModel",
    "declared_frames_missing",
    "frame_in_urdf",
    "frames_not_in_urdf",
    "link_has_frame",
    "parse_urdf",
]
