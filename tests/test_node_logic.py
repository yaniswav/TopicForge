"""Node listing, masking, cutting and `use_sim_time` extraction (pure, shared by live and mock)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from topicforge.adapters.base import AdapterError
from topicforge.adapters.common.nodes import (
    MASKED_VALUE,
    MAX_PARAM_VALUE_CHARS,
    MAX_PARAMETERS,
    build_node_listing,
    build_parameters,
    split_full_name,
    unknown_node_error,
    use_sim_time_of,
)
from topicforge.adapters.ros2_live.node_parsers import parse_param_dump
from topicforge.constants import DEFAULT_MAX_ARRAY_LENGTH

FIXTURES = Path(__file__).parent / "fixtures" / "ros2_node"


def test_split_full_name() -> None:
    assert split_full_name("/lidar_driver") == ("lidar_driver", "/")
    assert split_full_name("/robot1/base_controller") == ("base_controller", "/robot1")
    assert split_full_name("/a/b/c") == ("c", "/a/b")


def test_listing_merges_duplicates_and_names_them() -> None:
    names = ["/nav_planner", "/lidar_driver", "/lidar_driver", "/robot1/base_controller"]
    listing = build_node_listing(names, "live")
    assert [n.full_name for n in listing.nodes] == [
        "/lidar_driver",
        "/nav_planner",
        "/robot1/base_controller",
    ]
    assert [n.duplicate_count for n in listing.nodes] == [2, 1, 1]
    assert listing.duplicates == ["/lidar_driver"]
    assert (listing.returned, listing.total, listing.truncated) == (3, 3, False)
    assert listing.note is not None and "/lidar_driver" in listing.note
    namespaced = listing.nodes[2]
    assert (namespaced.name, namespaced.namespace) == ("base_controller", "/robot1")


def test_listing_without_duplicates_has_no_note() -> None:
    listing = build_node_listing(["/a", "/b"], "mock")
    assert listing.duplicates == [] and listing.note is None and listing.mode_effective == "mock"


def test_empty_listing_explains_itself() -> None:
    listing = build_node_listing([], "live")
    assert listing.nodes == [] and listing.total == 0
    assert listing.note is not None and "underscore" in listing.note


def test_unknown_node_error_lists_close_matches() -> None:
    err = unknown_node_error("/nav_plannr", ["/lidar_driver", "/nav_planner"])
    assert isinstance(err, AdapterError)
    assert "'/nav_plannr' is not on the ROS 2 graph" in str(err)
    assert "Close matches: /nav_planner" in str(err)
    assert "list_nodes" in str(err)


def test_unknown_node_error_matches_the_same_name_in_another_namespace() -> None:
    err = unknown_node_error("/lidar_driver", ["/robot1/lidar_driver", "/zzz"])
    assert "/robot1/lidar_driver" in str(err)


def test_unknown_node_error_without_a_close_match_says_nothing_misleading() -> None:
    assert "Close matches" not in str(unknown_node_error("/qqqq", ["/lidar_driver"]))


def test_secrets_are_masked_at_any_depth_and_in_any_case() -> None:
    raw = parse_param_dump((FIXTURES / "param_dump_secrets.yaml").read_text(encoding="utf-8"))
    assert raw is not None
    params, note = build_parameters(raw)
    by_name = {p.name: p for p in params}
    for name in (
        "API_KEY",
        "auth.token",
        "db_Password",
        "vendor_credentials.id",
        "vendor_credentials.secret_blob",
    ):
        assert by_name[name].masked is True, name
        assert by_name[name].value == MASKED_VALUE, name
    for name in ("auth.user", "frame_id", "use_sim_time"):
        assert by_name[name].masked is False, name
    assert by_name["frame_id"].value == "camera_link"
    assert note is not None and "5 value(s) masked" in note
    dumped = json.dumps([p.model_dump() for p in params])
    for secret in ("abcdef123456", "tok-secret-value", "hunter2", "xyz"):
        assert secret not in dumped


def test_parameters_are_sorted_and_nested_names_are_dotted() -> None:
    params, note = build_parameters({"b": 1, "a": {"c": {"d": 2}}, "qos": {"/scan": {"depth": 5}}})
    assert [p.name for p in params] == ["a.c.d", "b", "qos./scan.depth"]
    assert note is None


def test_a_long_string_is_cut_and_says_so() -> None:
    urdf = "<robot>" + "x" * 50_000 + "</robot>"
    params, note = build_parameters({"robot_description": urdf, "short": "ok"})
    cut = next(p for p in params if p.name == "robot_description")
    assert cut.truncated is True
    assert isinstance(cut.value, str) and len(cut.value) == MAX_PARAM_VALUE_CHARS
    assert cut.original_size == len(urdf)
    ok = next(p for p in params if p.name == "short")
    assert ok.truncated is False and ok.original_size is None
    assert note is not None and "1 value(s) cut" in note


def test_a_long_list_is_cut_at_the_array_length() -> None:
    params, note = build_parameters({"weights": list(range(1000))})
    (p,) = params
    assert p.truncated and p.original_size == 1000
    assert isinstance(p.value, list) and len(p.value) == DEFAULT_MAX_ARRAY_LENGTH
    assert note is not None


def test_non_finite_floats_become_strings() -> None:
    params, _ = build_parameters({"a": float("inf"), "b": float("-inf"), "c": float("nan")})
    assert [p.value for p in params] == ["inf", "-inf", "nan"]
    json.dumps([p.model_dump() for p in params], allow_nan=False)


def test_parameter_count_is_capped() -> None:
    raw = {f"p{i:04d}": i for i in range(MAX_PARAMETERS + 20)}
    params, note = build_parameters(raw)
    assert len(params) == MAX_PARAMETERS
    assert note is not None and f"first {MAX_PARAMETERS} of {MAX_PARAMETERS + 20}" in note


def test_use_sim_time_true_false() -> None:
    for value in (True, False):
        params, _ = build_parameters({"use_sim_time": value})
        assert use_sim_time_of(params) == (value, None)


def test_use_sim_time_unknown_always_says_why() -> None:
    assert use_sim_time_of(None)[0] is None and "not read" in (use_sim_time_of(None)[1] or "")
    absent, note = use_sim_time_of([])
    assert absent is None and note is not None and "does not declare" in note
    params, _ = build_parameters({"use_sim_time": "yes"})
    value, note = use_sim_time_of(params)
    assert value is None and note is not None and "not a boolean" in note


@pytest.mark.parametrize(
    "key", ["password", "my_secret", "auth_token", "api_key", "apiKey", "Credential"]
)
def test_every_documented_secret_word_masks(key: str) -> None:
    (p,), _ = build_parameters({key: "v"})
    assert p.masked and p.value == MASKED_VALUE
