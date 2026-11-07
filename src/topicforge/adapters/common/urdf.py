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

Joint types. `robot_state_publisher` puts fixed joints on `/tf_static` and the others on
`/tf`. `joint_type_mismatches` reports a fixed joint whose frame is only on `/tf`, and a
movable one whose frame is only on `/tf_static`. It judges revolute, continuous and
prismatic joints only (floating and planar ones are published by anything), needs the
joint's own parent and child to match the edge, and stays silent when no fixed joint is on
`/tf_static` at all (`use_tf_static` false, or a static stream that was not read).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Collection, Iterable
from dataclasses import dataclass

# Longest URDF read, in characters; the live adapter drops a larger message anyway.
MAX_URDF_CHARS = 4_000_000

FIXED_JOINT = "fixed"
MOVABLE_JOINTS = frozenset({"revolute", "continuous", "prismatic"})


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


def _root(text: str) -> ET.Element:
    """The parsed root of `text`; refuses what `parse_urdf` refuses, before any parsing."""
    if len(text) > MAX_URDF_CHARS:
        raise UrdfError(f"the robot description is larger than {MAX_URDF_CHARS} characters")
    lowered = text.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        raise UrdfError("the robot description declares a DOCTYPE or an ENTITY: not read")
    try:
        return ET.fromstring(text)
    except ET.ParseError as exc:
        raise UrdfError(f"the robot description is not well-formed XML ({exc})") from exc


def canonical_urdf(text: str) -> str:
    """A form of the URDF that ignores comments, attribute order and layout whitespace.

    Raises `UrdfError` like `parse_urdf`. Two texts with the same canonical form describe the
    same robot, so a hash of it changes only when the model does.
    """
    return _canonical(_root(text))


def _canonical(element: ET.Element) -> str:
    attrs = "".join(f" {k}={v!r}" for k, v in sorted(element.attrib.items()))
    text = (element.text or "").strip()
    tails = "".join((child.tail or "").strip() for child in element)
    inner = "".join(_canonical(child) for child in element)
    return f"<{element.tag}{attrs}>{text}{inner}{tails}</{element.tag}>"


_XACRO_NS = "xacro"


def xacro_reason(text: str) -> str | None:
    """Why `text` is xacro that was never expanded, else `None`.

    Read from the parsed tree: an element or attribute in a xacro namespace (whatever prefix
    it is bound to), an unbound prefix, or a `${...}` or `$(...)` in an attribute or text.
    A comment that mentions them does not count.
    """
    try:
        root = _root(text)
    except UrdfError as exc:
        if "unbound prefix" in str(exc):
            return "it uses a namespace prefix that is never declared (xacro?)"
        return None
    for element in root.iter():
        names = [element.tag, *element.attrib]
        if any(_XACRO_NS in n.partition("}")[0].lower() for n in names if n.startswith("{")):
            return "it holds xacro elements"
        values = [element.text or "", *element.attrib.values()]
        if any("${" in v or "$(" in v for v in values):
            return "it holds xacro expressions"
    return None


def parse_urdf(text: str) -> UrdfModel:
    """Links and joints of the URDF in `text`; raises `UrdfError` when it cannot be read."""
    root = _root(text)
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


@dataclass(frozen=True)
class JointMismatch:
    """A joint whose published frame is on the stream its type does not use."""

    joint: UrdfJoint
    fixed_seen_on_tf: bool
    child_frame: str
    """The frame as published (with its `frame_prefix`, if any)."""
    """`True`: a fixed joint seen only on `/tf`; `False`: a movable one seen only on `/tf_static`."""


def _same_frame(frame: str, link: str) -> bool:
    name = _bare(frame)
    return name == link or name.endswith("/" + link)


def joint_type_mismatches(
    model: UrdfModel, edges: Iterable[tuple[str, str, bool, bool]]
) -> list[JointMismatch]:
    """Joints whose transform is on the wrong stream; `edges` are `(parent, child, static, dynamic)`.

    An edge belongs to a joint when its parent and child both match the joint's links (a
    `frame_prefix` is ignored). A joint with no such edge is not judged: an unpublished
    frame is `declared_frames_missing`, a frame on both streams is `static_and_dynamic`.
    """
    seen = list(edges)
    fixed: list[JointMismatch] = []
    movable: list[JointMismatch] = []
    any_fixed_static = False
    for joint in model.joints:
        mine = [
            (static, dynamic, child)
            for parent, child, static, dynamic in seen
            if _same_frame(child, joint.child) and _same_frame(parent, joint.parent)
        ]
        on_static = any(s for s, _, _ in mine)
        on_dynamic = any(d for _, d, _ in mine)
        frame = mine[0][2] if mine else joint.child
        if joint.type == FIXED_JOINT:
            any_fixed_static = any_fixed_static or on_static
            if on_dynamic and not on_static:
                fixed.append(JointMismatch(joint, True, frame))
        elif joint.type in MOVABLE_JOINTS and on_static and not on_dynamic:
            movable.append(JointMismatch(joint, False, frame))
    return [*(fixed if any_fixed_static else []), *movable]


__all__ = [
    "FIXED_JOINT",
    "MAX_URDF_CHARS",
    "MOVABLE_JOINTS",
    "JointMismatch",
    "UrdfError",
    "UrdfJoint",
    "UrdfModel",
    "canonical_urdf",
    "declared_frames_missing",
    "frame_in_urdf",
    "frames_not_in_urdf",
    "joint_type_mismatches",
    "link_has_frame",
    "parse_urdf",
    "xacro_reason",
]
