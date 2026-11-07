"""URDF through fields: `inspect_tf(urdf_path=)` and joints published on the wrong stream.

Everything is built from strings and `tmp_path` files, so the tests run from the sdist and
without ROS 2. Flagship: a URDF frame that is never published plus one joint of the wrong
type is reported; the healthy pair reports nothing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.test_diagnose import Scripted, _by, _calm, _no_logs, _run
from tests.test_tf_tools import _string_result
from topicforge.adapters.base import AdapterError
from topicforge.adapters.common.tf_tree import build_tf_report
from topicforge.adapters.common.urdf import joint_type_mismatches, parse_urdf
from topicforge.adapters.ros2_mock import MockAdapter
from topicforge.models import TfCapture, TfSample
from topicforge.services.inspector import Inspector
from topicforge.services.urdf_file import read_urdf_file, validate_urdf_path

_NS = 1_000_000_000


def _urdf(*joints: tuple[str, str, str, str]) -> str:
    """A URDF from `(name, type, parent, child)` joints; every named link is declared."""
    links = dict.fromkeys(n for _, _, p, c in joints for n in (p, c))
    body = "".join(f'<link name="{n}"/>' for n in links)
    body += "".join(
        f'<joint name="{n}" type="{t}"><parent link="{p}"/><child link="{c}"/></joint>'
        for n, t, p, c in joints
    )
    return f'<?xml version="1.0"?><robot name="r">{body}</robot>'


ROBOT = _urdf(
    ("laser_j", "fixed", "base_link", "laser"),
    ("cam_j", "fixed", "base_link", "camera"),
    ("arm_j", "revolute", "base_link", "arm"),
)


def _sample(parent: str, child: str, stamp: int = 0) -> TfSample:
    return TfSample(parent=parent, child=child, stamp_ns=stamp, received_ns=stamp)


def _capture(static: list[tuple[str, str]], dynamic: list[tuple[str, str]]) -> TfCapture:
    return TfCapture(
        static=[_sample(p, c) for p, c in static],
        dynamic=[_sample(p, c, i * _NS) for i in range(3) for p, c in dynamic],
        window_actual_s=3.0,
    )


def _kinds(capture: TfCapture, urdf: str = ROBOT) -> dict[str, list[str]]:
    report = build_tf_report(capture, 2.0, None, None, urdf=parse_urdf(urdf))
    return {f.kind: f.frames for f in report.findings}


HEALTHY = _capture(
    static=[("base_link", "laser"), ("base_link", "camera")],
    dynamic=[("odom", "base_link"), ("base_link", "arm")],
)


def test_a_healthy_pair_reports_nothing() -> None:
    report = build_tf_report(HEALTHY, 2.0, None, None, urdf=parse_urdf(ROBOT))
    assert report.findings == [] and report.declared_frames_missing == []


def test_a_fixed_joint_driven_on_tf_while_the_others_are_static() -> None:
    capture = _capture(
        static=[("base_link", "camera")],
        dynamic=[("odom", "base_link"), ("base_link", "laser"), ("base_link", "arm")],
    )
    assert _kinds(capture) == {"fixed_joint_on_tf": ["laser"]}


def test_no_fixed_joint_on_tf_static_means_use_tf_static_false_and_stays_silent() -> None:
    capture = _capture(
        static=[("odom", "tool")],
        dynamic=[("odom", "base_link"), ("base_link", "laser"), ("base_link", "arm")],
    )
    found = _kinds(capture)
    assert "fixed_joint_on_tf" not in found
    assert found["declared_not_published"] == ["camera"]


def test_flagship_unpublished_link_and_wrong_joint_type_together() -> None:
    urdf = _urdf(
        ("laser_j", "fixed", "base_link", "laser"),
        ("cam_j", "fixed", "base_link", "camera"),
        ("wheel_j", "continuous", "base_link", "wheel"),
        ("arm_j", "revolute", "base_link", "arm"),
    )
    capture = _capture(
        static=[("base_link", "camera"), ("base_link", "wheel")],  # wheel frozen on /tf_static
        dynamic=[("odom", "base_link"), ("base_link", "laser")],  # laser is fixed, but on /tf
    )
    report = build_tf_report(capture, 2.0, None, None, urdf=parse_urdf(urdf))
    kinds = {f.kind: f for f in report.findings}
    assert kinds["declared_not_published"].frames == ["arm"]
    assert kinds["fixed_joint_on_tf"].frames == ["laser"]
    assert kinds["movable_joint_on_tf_static"].frames == ["wheel"]
    assert "wheel_j" in kinds["movable_joint_on_tf_static"].text
    assert "/tf_static" in kinds["fixed_joint_on_tf"].text


def test_a_frame_on_both_streams_is_not_a_type_mismatch() -> None:
    capture = _capture(
        static=[("base_link", "camera"), ("base_link", "arm"), ("base_link", "laser")],
        dynamic=[("odom", "base_link"), ("base_link", "arm"), ("base_link", "laser")],
    )
    report = build_tf_report(capture, 2.0, None, None, urdf=parse_urdf(ROBOT))
    kinds = [f.kind for f in report.findings]
    assert "fixed_joint_on_tf" not in kinds and "movable_joint_on_tf_static" not in kinds
    assert kinds.count("static_and_dynamic") == 2


def test_floating_planar_and_unknown_joints_are_never_judged() -> None:
    urdf = _urdf(
        ("a", "fixed", "base_link", "anchor"),
        ("f", "floating", "base_link", "free"),
        ("p", "planar", "base_link", "slide"),
        ("u", "weird", "base_link", "odd"),
    )
    capture = _capture(
        static=[
            ("base_link", "anchor"),
            ("base_link", "free"),
            ("base_link", "slide"),
            ("base_link", "odd"),
        ],
        dynamic=[("odom", "base_link")],
    )
    assert _kinds(capture, urdf) == {}


def test_a_frame_prefix_is_ignored_when_matching_a_joint() -> None:
    capture = _capture(
        static=[("r1/base_link", "r1/camera")],
        dynamic=[("r1/base_link", "r1/laser"), ("r1/base_link", "r1/arm")],
    )
    assert _kinds(capture) == {"fixed_joint_on_tf": ["r1/laser"]}  # the frame as published


def test_a_cut_or_dynamic_free_capture_judges_no_joint() -> None:
    base = _capture(
        static=[("base_link", "camera")],
        dynamic=[("odom", "base_link"), ("base_link", "laser")],
    )
    assert "fixed_joint_on_tf" in _kinds(base)
    for cut in ({"capped": True}, {"dropped_count": 2}):
        assert "fixed_joint_on_tf" not in _kinds(base.model_copy(update=cut))
    no_dynamic = _capture(static=[("base_link", "camera"), ("base_link", "arm")], dynamic=[])
    assert "movable_joint_on_tf_static" not in _kinds(no_dynamic)


def test_the_finding_text_does_not_capitalize_an_identifier() -> None:
    capture = _capture(
        static=[("base_link", "camera")],
        dynamic=[("odom", "base_link"), ("base_link", "laser")],
    )
    report = build_tf_report(capture, 2.0, None, None, urdf=parse_urdf(ROBOT))
    assert "robot_state_publisher" in report.findings[-1].text
    assert "Robot_state" not in report.findings[-1].text


def test_an_edge_under_another_parent_is_not_the_joint() -> None:
    # `laser` hangs from `mount`, not from the URDF parent `base_link`: another problem.
    capture = _capture(
        static=[("base_link", "camera")],
        dynamic=[("odom", "base_link"), ("mount", "laser")],
    )
    assert "fixed_joint_on_tf" not in _kinds(capture)


def test_a_partial_urdf_judges_only_its_own_joints() -> None:
    capture = _capture(
        static=[("base_link", "camera"), ("odom", "extra")],
        dynamic=[("odom", "base_link"), ("map", "odom"), ("base_link", "arm")],
    )
    assert _kinds(capture, _urdf(("cam_j", "fixed", "base_link", "camera"))) == {}


def test_no_frame_captured_compares_nothing() -> None:
    report = build_tf_report(
        TfCapture(window_actual_s=1.0), 2.0, None, None, urdf=parse_urdf(ROBOT)
    )
    assert report.findings == []


def test_joint_type_mismatches_is_a_pure_function() -> None:
    model = parse_urdf(ROBOT)
    edges = [
        ("base_link", "laser", True, False),
        ("base_link", "camera", False, True),
        ("base_link", "arm", True, False),
    ]
    got = [(m.joint.name, m.fixed_seen_on_tf) for m in joint_type_mismatches(model, edges)]
    assert got == [("cam_j", True), ("arm_j", False)]


# ----- urdf_path -----


def _file(tmp_path: Path, name: str, text: str) -> str:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_urdf_path_is_compared_instead_of_robot_description(tmp_path: Path) -> None:
    path = _file(tmp_path, "robot.urdf", _urdf(("j", "fixed", "base_link", "no_such_link")))
    report = Inspector(MockAdapter()).inspect_tf(urdf_path=path)
    assert report.urdf_source == "robot_description" and report.urdf_link_count == 2
    assert report.declared_frames_missing == ["no_such_link"]
    assert report.urdf_note is not None and "urdf_path" in report.urdf_note


def test_urdf_path_skips_the_robot_description_read(tmp_path: Path) -> None:
    topics: list[str] = []

    class Spy(MockAdapter):
        def sample_messages(self, topic: str, count: int, **kw: Any) -> Any:
            topics.append(topic)
            return super().sample_messages(topic, count, **kw)

    Inspector(Spy()).inspect_tf(urdf_path=_file(tmp_path, "r.urdf", ROBOT))
    assert topics == []


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("", "non-empty"),
        ("a" + chr(0) + ".urdf", "null bytes"),
        ("robot.urdf.xacro", "xacro"),
        ("robot.xacro", "xacro"),
        ("robot.yaml", ".urdf"),
        (None, "non-empty"),
    ],
)
def test_a_malformed_urdf_path_is_refused_before_any_io(value: Any, message: str) -> None:
    with pytest.raises(AdapterError, match=message):
        validate_urdf_path(value)


def test_an_unreadable_urdf_file_is_a_note_not_an_error(tmp_path: Path) -> None:
    report = Inspector(MockAdapter()).inspect_tf(urdf_path=str(tmp_path / "gone.urdf"))
    assert report.urdf_source == "none" and report.urdf_link_count is None
    assert "not a file" in (report.urdf_note or "")
    assert report.findings  # the TF tree is still the result


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ('<robot name="x"><xacro:macro name="m"/><link name="a"/></robot>', "xacro"),
        ('<robot name="x"><link name="a"/><joint name="${n}"/></robot>', "xacro"),
        ('<!DOCTYPE robot [<!ENTITY a "b">]><robot><link name="a"/></robot>', "DOCTYPE"),
        ("<robot><joint/></robot>", "no <link>"),
        ("not xml", "well-formed"),
        (
            '<robot xmlns:x="http://www.ros.org/wiki/xacro"><x:macro name="m"/><link name="a"/></robot>',
            "xacro",
        ),
        ('<robot name="x"><link name="a"/><u:thing/></robot>', "never declared"),
    ],
)
def test_a_urdf_file_that_cannot_be_read_says_why(tmp_path: Path, text: str, why: str) -> None:
    model, note = read_urdf_file(_file(tmp_path, "r.urdf", text))
    assert model is None and why in note


def test_a_comment_mentioning_xacro_does_not_trip_the_check(tmp_path: Path) -> None:
    text = ROBOT.replace("<link", "<!-- <xacro:macro> ${x} --><link", 1)
    model, note = read_urdf_file(_file(tmp_path, "r.urdf", text))
    assert model is not None and "urdf_path" in note


def test_an_oversized_urdf_file_is_not_read(tmp_path: Path) -> None:
    big = _file(tmp_path, "big.urdf", "<robot>" + "x" * (1024 * 1024) + "</robot>")
    model, note = read_urdf_file(big)
    assert model is None and "larger than" in note


def test_the_mock_robot_has_no_joint_type_finding() -> None:
    report = Inspector(MockAdapter()).inspect_tf()
    assert {f.kind for f in report.findings} == {"stale", "declared_not_published"}


# ----- diagnose -----


def _with_urdf(adapter: Scripted, urdf: str, capture: TfCapture) -> Scripted:
    adapter.over["capture_tf"] = lambda _w: capture

    def sample(topic: str, count: int, **kw: Any) -> Any:
        if topic == "/robot_description":
            return _string_result(urdf)
        return _no_logs(topic, count, **kw)

    adapter.over["sample_messages"] = sample
    return adapter


def test_diagnose_warns_on_a_joint_on_the_wrong_stream() -> None:
    capture = _capture(
        static=[("base_link", "camera")],
        dynamic=[("odom", "base_link"), ("base_link", "laser"), ("base_link", "arm")],
    )
    diagnosis = _run(_with_urdf(_calm(Scripted()), ROBOT, capture))
    assert _by(diagnosis, "tf", "warning") == []  # a fixed joint on /tf: lookups still work
    found = [f for f in _by(diagnosis, "tf", "info") if f.subject == "laser"]
    fix = found[0].suggested_fixes[0]
    assert fix.text == "ros2 topic info /tf --verbose" and fix.verify_with == "inspect_tf"


def test_diagnose_warns_on_a_movable_joint_frozen_on_tf_static() -> None:
    capture = _capture(
        static=[("base_link", "camera"), ("base_link", "laser"), ("base_link", "arm")],
        dynamic=[("odom", "base_link")],
    )
    found = _by(_run(_with_urdf(_calm(Scripted()), ROBOT, capture)), "tf", "warning")
    assert [f.subject for f in found] == ["arm"]
    assert found[0].suggested_fixes[0].text == "ros2 topic info /tf_static --verbose"


def test_diagnose_stays_quiet_on_the_healthy_pair() -> None:
    diagnosis = _run(_with_urdf(_calm(Scripted()), ROBOT, HEALTHY))
    assert _by(diagnosis, "tf", "warning") == []
    assert _by(diagnosis, "tf", "critical") == []
