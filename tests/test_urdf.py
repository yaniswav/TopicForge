"""`adapters/common/urdf.py`: the pure URDF reader behind `inspect_tf`'s declared-against-real check."""

from __future__ import annotations

import pytest

from topicforge.adapters.common.urdf import (
    MAX_URDF_CHARS,
    UrdfError,
    declared_frames_missing,
    frame_in_urdf,
    frames_not_in_urdf,
    link_has_frame,
    parse_urdf,
)

ARM = """<?xml version="1.0"?>
<robot name="arm">
  <link name="base_link"/>
  <link name="shoulder"/>
  <link name="gripper_link"/>
  <joint name="j1" type="revolute">
    <parent link="base_link"/><child link="shoulder"/>
  </joint>
  <joint name="j2" type="fixed">
    <parent link="shoulder"/><child link="gripper_link"/>
  </joint>
</robot>
"""


def test_links_and_joints_are_read_in_document_order() -> None:
    model = parse_urdf(ARM)
    assert model.robot_name == "arm"
    assert model.links == ("base_link", "shoulder", "gripper_link")
    assert [(j.name, j.type, j.parent, j.child) for j in model.joints] == [
        ("j1", "revolute", "base_link", "shoulder"),
        ("j2", "fixed", "shoulder", "gripper_link"),
    ]


def test_joint_into_finds_the_joint_above_a_link_and_none_for_the_root() -> None:
    model = parse_urdf(ARM)
    joint = model.joint_into("shoulder")
    assert joint is not None and joint.type == "revolute"
    assert model.joint_into("base_link") is None


def test_a_xml_declaration_with_an_encoding_is_accepted() -> None:
    text = '<?xml version="1.0" encoding="utf-8"?><robot name="r"><link name="a"/></robot>'
    assert parse_urdf(text).links == ("a",)


def test_a_doctype_is_refused() -> None:
    text = '<!DOCTYPE robot SYSTEM "file:///etc/passwd"><robot><link name="a"/></robot>'
    with pytest.raises(UrdfError, match="DOCTYPE"):
        parse_urdf(text)


def test_an_entity_is_refused_in_any_case() -> None:
    text = '<!doctype robot [<!entity lol "lol">]><robot><link name="a"/></robot>'
    with pytest.raises(UrdfError, match="ENTITY"):
        parse_urdf(text)


def test_malformed_xml_is_a_urdf_error_not_a_parse_error() -> None:
    with pytest.raises(UrdfError, match="not well-formed"):
        parse_urdf("<robot><link name='a'></robot>")


def test_a_document_that_is_not_a_robot_is_refused() -> None:
    with pytest.raises(UrdfError, match="not a URDF"):
        parse_urdf("<sdf><model/></sdf>")


def test_a_robot_without_a_link_is_refused() -> None:
    with pytest.raises(UrdfError, match="no <link>"):
        parse_urdf("<robot name='empty'/>")


def test_an_oversized_document_is_refused_before_parsing() -> None:
    with pytest.raises(UrdfError, match="larger than"):
        parse_urdf("<robot>" + "x" * MAX_URDF_CHARS + "</robot>")


def test_only_direct_children_are_links_and_a_nameless_link_is_skipped() -> None:
    text = (
        "<robot><link name='a'/><link/><gazebo><link name='inner'/></gazebo>"
        "<joint name='j' type='fixed'><parent link='a'/></joint></robot>"
    )
    model = parse_urdf(text)
    assert model.links == ("a",) and model.joints == ()


def test_a_frame_prefix_still_matches_its_link() -> None:
    assert link_has_frame("laser", ["robot1/laser"])
    assert link_has_frame("laser", ["/laser"])
    assert not link_has_frame("laser", ["old_laser", "laserx"])
    assert frame_in_urdf("robot1/laser", ["laser"])
    assert not frame_in_urdf("laser_2", ["laser"])


def test_missing_and_extra_frames_follow_the_exact_or_prefix_rule() -> None:
    model = parse_urdf(ARM)
    frames = ["map", "odom", "base_link", "shoulder"]
    assert declared_frames_missing(model, frames) == ["gripper_link"]
    assert frames_not_in_urdf(model, frames) == ["map", "odom"]
    assert declared_frames_missing(model, ["ns/base_link", "ns/shoulder", "ns/gripper_link"]) == []
