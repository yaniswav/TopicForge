"""`build_tf_report`: the pure analysis behind `inspect_tf`, with no ROS 2.

Every case builds a `TfCapture` by hand. Times are in seconds, converted to ns.
"""

from __future__ import annotations

from topicforge.adapters.common.tf_tree import build_tf_report
from topicforge.models import TfCapture, TfReport, TfSample

NS = 1_000_000_000


def _s(
    parent: str, child: str, t: float, received: float | None = None, stamp: float | None = None
) -> TfSample:
    """A sample received at wall time `t` (the stamp equals it unless `stamp` is given)."""
    return TfSample(
        parent=parent,
        child=child,
        stamp_ns=int((t if stamp is None else stamp) * NS),
        received_ns=int((t if received is None else received) * NS),
    )


def _series(parent: str, child: str, start: float, stop: float, hz: float) -> list[TfSample]:
    n = round((stop - start) * hz)
    return [_s(parent, child, start + i / hz) for i in range(n + 1)]


def _report(
    static: list[TfSample] | None = None,
    dynamic: list[TfSample] | None = None,
    *,
    stale_after_s: float = 2.0,
    source: str | None = None,
    target: str | None = None,
    **capture: object,
) -> TfReport:
    cap = TfCapture(static=static or [], dynamic=dynamic or [], window_actual_s=5.0, **capture)  # type: ignore[arg-type]
    return build_tf_report(cap, stale_after_s, source, target)


def _kinds(report: TfReport) -> list[str]:
    return [f.kind for f in report.findings]


def _frame(report: TfReport, name: str):  # type: ignore[no-untyped-def]
    return next(f for f in report.frames if f.name == name)


def test_a_sound_tree_has_one_root_and_no_finding() -> None:
    report = _report(
        static=[_s("base_link", "laser", 0)],
        dynamic=_series("map", "odom", 0, 5, 10) + _series("odom", "base_link", 0, 5, 50),
    )
    assert report.roots == ["map"]
    assert report.findings == [] and report.stale_count == 0
    assert (report.frame_count, report.static_count, report.dynamic_count) == (4, 1, 2)
    assert report.transform_count == 3
    assert report.message_count == 1 + 51 + 251
    assert _frame(report, "laser").kind == "static" and _frame(report, "laser").age_s is None
    assert _frame(report, "map").parent is None and _frame(report, "map").kind is None


def test_stale_frame_is_measured_against_the_newest_stamp() -> None:
    report = _report(
        dynamic=_series("odom", "base_link", 0, 5, 10) + _series("map", "odom", 0, 1, 1),
    )
    odom = _frame(report, "odom")
    assert odom.stale and odom.age_s == 4.0
    assert _kinds(report) == ["stale"] and report.findings[0].frames == ["map", "odom"]
    assert report.stale_count == 1 and not _frame(report, "base_link").stale
    assert report.reference_ns == 5 * NS
    assert any("stale" in h for h in report.hints)


def test_stale_threshold_is_respected() -> None:
    dynamic = _series("odom", "base_link", 0, 5, 10) + _series("map", "odom", 0, 4, 1)
    assert _report(dynamic=dynamic, stale_after_s=2.0).stale_count == 0
    assert _report(dynamic=dynamic, stale_after_s=0.5).stale_count == 1


def test_two_parents() -> None:
    report = _report(
        dynamic=_series("odom", "base_link", 0, 3, 10) + _series("map", "base_link", 0, 3, 10),
    )
    finding = next(f for f in report.findings if f.kind == "two_parents")
    assert finding.frames == ["base_link", "map", "odom"]
    assert len(report.roots) == 2 and "disconnected" in _kinds(report)
    assert _frame(report, "base_link").parent in {"map", "odom"}


def test_disconnected_roots() -> None:
    report = _report(static=[_s("a", "b", 0), _s("x", "y", 0)])
    finding = next(f for f in report.findings if f.kind == "disconnected")
    assert finding.frames == ["a", "x"] and report.roots == ["a", "x"]


def test_static_and_dynamic_on_the_same_frame() -> None:
    report = _report(
        static=[_s("base_link", "laser", 0)],
        dynamic=_series("odom", "base_link", 0, 2, 10) + _series("base_link", "laser", 0, 2, 10),
    )
    laser = _frame(report, "laser")
    assert laser.kind == "both" and laser.parent == "base_link"
    finding = next(f for f in report.findings if f.kind == "static_and_dynamic")
    assert finding.frames == ["laser"]
    assert "two_parents" not in _kinds(report)


def test_cycle() -> None:
    report = _report(dynamic=[_s("a", "b", 1), _s("b", "c", 1), _s("c", "a", 1)])
    cycles = [f for f in report.findings if f.kind == "cycle"]
    assert len(cycles) == 1 and cycles[0].frames == ["a", "b", "c"]
    assert report.roots == []


def test_path_found_goes_up_and_down_the_tree() -> None:
    report = _report(
        static=[_s("base_link", "laser", 0), _s("base_link", "camera_link", 0)],
        dynamic=_series("odom", "base_link", 0, 2, 10),
        source="/laser",
        target="camera_link",
    )
    assert report.path == ["laser", "base_link", "camera_link"]
    assert report.path_note == "2 transform(s)." and "no_path" not in _kinds(report)


def test_path_crossing_a_stale_frame_says_so() -> None:
    report = _report(
        static=[_s("base_link", "laser", 0)],
        dynamic=_series("odom", "base_link", 0, 5, 10) + _series("map", "odom", 0, 1, 1),
        source="laser",
        target="map",
    )
    assert report.path == ["laser", "base_link", "odom", "map"]
    assert (
        report.path_note is not None and "stale" in report.path_note and "odom" in report.path_note
    )


def test_no_path_between_separate_trees_and_for_unknown_frames() -> None:
    report = _report(static=[_s("a", "b", 0), _s("x", "y", 0)], source="b", target="y")
    assert report.path is None and "no_path" in _kinds(report)
    assert report.path_note is not None and "No chain" in report.path_note
    unknown = _report(static=[_s("a", "b", 0)], source="b", target="ghost")
    assert unknown.path is None and unknown.path_note == "Unknown frame(s): ghost."
    assert "no_path" in _kinds(unknown)


def test_empty_capture_is_a_result_with_a_note() -> None:
    report = _report()
    assert report.frames == [] and report.roots == [] and report.findings == []
    assert report.reference_ns is None and report.real_time_factor is None
    assert report.note is not None and "No transform" in report.note


def test_capture_notes_and_losses_are_reported() -> None:
    report = _report(
        dynamic=_series("odom", "base_link", 0, 1, 10),
        dropped_count=3,
        notes=["/tf_static has no publisher."],
    )
    assert report.truncated and report.note is not None
    assert "/tf_static has no publisher." in report.note and "3 message(s)" in report.note
    assert _report(dynamic=[_s("a", "b", 0)], capped=True).truncated


def test_sim_time_gives_a_real_time_factor_and_two_frequencies() -> None:
    # 10 stamp-seconds go by in 20 wall seconds: a simulation at half speed.
    dynamic = [
        _s("odom", "base_link", i * 0.1, received=i * 0.2, stamp=i * 0.1) for i in range(101)
    ]
    report = _report(dynamic=dynamic)
    frame = _frame(report, "base_link")
    assert frame.sim_frequency_hz == 10.0 and frame.observed_frequency_hz == 5.0
    assert report.real_time_factor == 0.5 and report.stamp_span_s == 10.0
    assert any("0.5x" in h for h in report.hints)


def test_wall_time_robot_has_a_factor_of_one_and_no_hint() -> None:
    report = _report(dynamic=_series("odom", "base_link", 0, 5, 50))
    assert report.real_time_factor == 1.0
    assert _frame(report, "base_link").observed_frequency_hz == 50.0
    assert report.hints == []


def test_leading_slashes_in_frame_names_are_ignored() -> None:
    report = _report(dynamic=[_s("/odom", "/base_link", 1), _s("odom", "base_link", 2)])
    assert report.frame_count == 2 and report.transform_count == 1


def test_an_empty_capture_hints_no_frames_not_static_only() -> None:
    report = _report()
    assert any("No TF frames were received" in h for h in report.hints)
    assert not any("only static frames" in h for h in report.hints)
