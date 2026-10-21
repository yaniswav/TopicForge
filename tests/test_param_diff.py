"""`adapters/common/param_diff.py`: a parameter file compared with a node's live parameters."""

from __future__ import annotations

import pytest

from topicforge.adapters.common.nodes import build_parameters
from topicforge.adapters.common.param_diff import (
    MAX_PARAM_DIFF,
    ParamFileError,
    build_param_diff,
    compare_parameters,
    declared_parameters,
    load_params_document,
    values_equal,
)
from topicforge.models import NodeParameter, ParamDiff

NODE = "/lidar_driver"


def _live(raw: dict[str, object]) -> list[NodeParameter]:
    return build_parameters(raw)[0]


def _diff(
    text: str, live: dict[str, object], node: str = NODE
) -> tuple[list[ParamDiff] | None, str | None, str | None]:
    return build_param_diff(text, node, _live(live))


# ----- the file -----


def test_a_plain_section_is_flattened_with_dots() -> None:
    text = "lidar_driver:\n  ros__parameters:\n    frame_id: laser\n    qos:\n      depth: 5\n"
    declared, note = declared_parameters(load_params_document(text), NODE)
    assert declared == {"frame_id": "laser", "qos.depth": 5} and note is None


def test_full_name_bare_name_and_wildcard_keys_all_apply() -> None:
    for key in ("/lidar_driver", "lidar_driver", "/**", "**"):
        text = f'"{key}":\n  ros__parameters:\n    a: 1\n'
        declared, _ = declared_parameters(load_params_document(text), NODE)
        assert declared == {"a": 1}, key


def test_the_most_specific_section_wins_whatever_the_file_order() -> None:
    text = (
        "/lidar_driver:\n  ros__parameters:\n    a: specific\n"
        "/**:\n  ros__parameters:\n    a: generic\n    b: generic\n"
    )
    declared, _ = declared_parameters(load_params_document(text), NODE)
    assert declared == {"a": "specific", "b": "generic"}


def test_a_namespace_wildcard_applies_only_inside_its_namespace() -> None:
    document = load_params_document("/robot1/**:\n  ros__parameters:\n    a: 1\n")
    assert declared_parameters(document, "/robot1/lidar_driver")[0] == {"a": 1}
    assert declared_parameters(document, "/robot2/lidar_driver")[0] is None


def test_no_section_for_the_node_names_the_keys_it_has() -> None:
    declared, note = declared_parameters(
        load_params_document("other:\n  ros__parameters:\n    a: 1\n"), NODE
    )
    assert declared is None and "no section for /lidar_driver" in (note or "")
    assert "`other`" in (note or "")


def test_a_pattern_the_module_does_not_evaluate_is_named_not_guessed() -> None:
    text = "/lidar_*:\n  ros__parameters:\n    a: 1\n/lidar_driver:\n  ros__parameters:\n    b: 2\n"
    declared, note = declared_parameters(load_params_document(text), NODE)
    assert declared == {"b": 2} and "`/lidar_*`" in (note or "")


def test_a_section_without_ros_parameters_is_noted() -> None:
    declared, note = declared_parameters(
        load_params_document("/lidar_driver:\n  frame_id: laser\n"), NODE
    )
    assert declared is None and "no `ros__parameters`" in (note or "")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("a: [unclosed", "not valid YAML"),
        ("- just\n- a list\n", "not a mapping"),
        ("", "not a mapping"),
    ],
)
def test_an_unusable_file_is_a_param_file_error(text: str, message: str) -> None:
    with pytest.raises(ParamFileError, match=message):
        load_params_document(text)


def test_yaml_python_tags_are_not_executed() -> None:
    with pytest.raises(ParamFileError, match="not valid YAML"):
        load_params_document("a: !!python/object/apply:os.system ['echo no']\n")


def test_an_error_message_never_repeats_the_file_content() -> None:
    with pytest.raises(ParamFileError) as caught:
        load_params_document("password: hunter2\nbad: [unclosed\n")
    assert "hunter2" not in str(caught.value)


def test_aliases_that_expand_past_the_limit_are_refused() -> None:
    lines = ["a: &a [x, x, x, x, x, x, x, x, x, x]"]
    previous = "a"
    for i in range(1, 8):
        name = f"b{i}"
        lines.append(f"{name}: &{name} [" + ", ".join([f"*{previous}"] * 10) + "]")
        previous = name
    with pytest.raises(ParamFileError, match="expands to more than"):
        load_params_document("\n".join(lines) + "\n")


# ----- equality -----


@pytest.mark.parametrize(
    ("declared", "live", "equal"),
    [
        (12, 12.0, True),
        (12.0, 12.5, False),
        (True, 1, False),
        (1, True, False),
        (False, False, True),
        ("laser", "laser", True),
        ("laser", "Laser", False),
        ("1", 1, False),
        ([1, 2], [1.0, 2.0], True),
        ([1, 2], [1, 2, 3], False),
        (["a"], ["b"], False),
        (None, None, True),
    ],
)
def test_values_equal_rules(declared: object, live: object, equal: bool) -> None:
    assert values_equal(declared, live) is equal  # type: ignore[arg-type]


# ----- the comparison -----


def test_different_and_declared_only_are_listed_and_the_rest_counted() -> None:
    text = (
        "lidar_driver:\n  ros__parameters:\n"
        "    max_range: 12.0\n    frame_id: laser\n    extra_gain: 1.5\n"
    )
    live = {"max_range": 10.0, "frame_id": "laser", "use_sim_time": False, "scan_period": 0.1}
    diff, summary, note = _diff(text, live)
    assert diff is not None
    assert [(d.name, d.status, d.declared, d.live) for d in diff] == [
        ("extra_gain", "declared_only", 1.5, None),
        ("max_range", "different", 12.0, 10.0),
    ]
    assert summary == "1 same, 2 live_only" and note is None


def test_everything_equal_gives_an_empty_list_not_none() -> None:
    diff, summary, _ = _diff("/**:\n  ros__parameters:\n    a: 1\n", {"a": 1.0})
    assert diff == [] and summary == "1 same, 0 live_only"


def test_a_secret_is_masked_on_both_sides_and_never_echoed_from_the_file() -> None:
    text = "/**:\n  ros__parameters:\n    rtsp:\n      password: from-the-file\n"
    diff, _, _ = _diff(text, {"rtsp": {"password": "live-secret"}})
    assert diff is not None and len(diff) == 1
    entry = diff[0]
    assert (entry.name, entry.status) == ("rtsp.password", "masked")
    assert entry.declared == "<masked>" and entry.live == "<masked>"
    assert "from-the-file" not in repr(diff)


def test_a_secret_absent_from_the_node_is_masked_with_no_live_value() -> None:
    diff, _, _ = _diff("/**:\n  ros__parameters:\n    api_key: abc\n", {"a": 1})
    assert diff is not None and diff[0].status == "masked" and diff[0].live is None


def test_a_long_value_the_node_cut_is_compared_cut_the_same_way() -> None:
    live = _live({"robot_description": "x" * 3000})
    assert live[0].truncated
    same = compare_parameters({"robot_description": "x" * 3000}, live)
    assert same.entries == [] and same.same == 1
    other = compare_parameters({"robot_description": "y" * 3000}, live)
    assert [e.status for e in other.entries] == ["different"]
    assert len(str(other.entries[0].declared)) == 2048  # the file's value is cut for display


def test_the_list_of_differences_is_capped_and_the_note_says_so() -> None:
    declared = "\n".join(f"    p{i:03d}: {i}" for i in range(MAX_PARAM_DIFF + 30))
    diff, _, note = _diff(f"/**:\n  ros__parameters:\n{declared}\n", {"zzz": 1})
    assert diff is not None and len(diff) == MAX_PARAM_DIFF
    assert "30 more difference(s)" in (note or "")


def test_a_full_live_list_warns_that_declared_only_may_lie() -> None:
    live = {f"p{i}": i for i in range(500)}
    _, _, note = _diff("/**:\n  ros__parameters:\n    missing: 1\n", live)
    assert "declared_only may lie" in (note or "")


def test_unreadable_live_parameters_give_no_diff_and_a_note() -> None:
    diff, summary, note = build_param_diff("/**:\n  ros__parameters:\n    a: 1\n", NODE, None)
    assert diff is None and summary is None and "were not read" in (note or "")


def test_a_bad_file_gives_a_note_not_an_exception() -> None:
    diff, summary, note = build_param_diff("a: [", NODE, [])
    assert diff is None and summary is None
    assert (note or "").startswith("params_file not compared")
