"""Pure tests for the `ros2 topic echo` YAML decoder, on output captured from a ROS 2 Humble bench.

Fixtures under `tests/fixtures/ros2_echo/` are real `ros2 topic echo` output
(a simulated-time lidar robot publishing from time 0), cut to whole documents.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import yaml

from topicforge.adapters.ros2_live.echo_parser import (
    RAW_TEXT_KEY,
    parse_echo_document,
    split_echo_documents,
)
from topicforge.constants import TRUNCATED_FIELDS_KEY

_FIXTURES = Path(__file__).parent / "fixtures" / "ros2_echo"


def _docs(name: str) -> list[str]:
    return split_echo_documents((_FIXTURES / f"{name}.yaml").read_text(encoding="utf-8"))


# ---- splitting -------------------------------------------------------------


def test_split_returns_one_document_per_separator() -> None:
    assert len(_docs("twist")) == 2
    assert len(_docs("scan_trunc3")) == 2


def test_split_drops_a_message_still_being_printed() -> None:
    text = "data: a\n---\ndata: b\n---\ndata: c"
    assert split_echo_documents(text) == ["data: a\n", "data: b\n"]


def test_split_of_empty_output_is_empty() -> None:
    assert split_echo_documents("") == []
    assert split_echo_documents("---\n") == [""]


def test_split_ignores_indented_dashes_inside_a_value() -> None:
    text = "text: |\n  ---\n  more\n---\n"
    [doc] = split_echo_documents(text)
    assert yaml.safe_load(doc) == {"text": "---\nmore\n"}


# ---- structure and stamp ---------------------------------------------------


def test_twist_is_nested_and_headerless() -> None:
    message = parse_echo_document(_docs("twist")[0])
    assert message.payload == {
        "linear": {"x": 0.25, "y": 0.0, "z": 0.0},
        "angular": {"x": 0.0, "y": 0.0, "z": 0.5},
    }
    assert message.timestamp_ns == 0
    assert message.stamp_source == "none"


def test_string_message() -> None:
    message = parse_echo_document(_docs("string_latched")[0])
    assert message.payload == {"data": "bench_robot: differential drive, 2D lidar"}
    assert message.stamp_source == "none"


def test_sim_time_stamp_is_kept_not_rejected() -> None:
    message = parse_echo_document(_docs("scan_trunc128")[0])
    stamp = message.payload["header"]["stamp"]  # type: ignore[index]
    assert stamp["sec"] < 600  # simulated seconds, not an epoch date
    assert message.timestamp_ns == stamp["sec"] * 1_000_000_000 + stamp["nanosec"]
    assert message.stamp_source == "header"
    assert message.payload["header"]["frame_id"] == "base_laser"  # type: ignore[index]
    assert "col_0" not in message.payload


def test_a_zero_stamp_is_a_header_stamp() -> None:
    message = parse_echo_document("header:\n  stamp:\n    sec: 0\n    nanosec: 0\n  frame_id: ''\n")
    assert message.timestamp_ns == 0
    assert message.stamp_source == "header"


def test_a_nested_header_is_not_the_message_stamp() -> None:
    doc = "pose:\n  header:\n    stamp:\n      sec: 5\n      nanosec: 1\n"
    message = parse_echo_document(doc)
    assert message.timestamp_ns == 0 and message.stamp_source == "none"


def test_a_stamp_without_integer_parts_is_not_used() -> None:
    message = parse_echo_document("header:\n  stamp: soon\n")
    assert message.stamp_source == "none"


def test_timestamp_looking_strings_stay_strings() -> None:
    message = parse_echo_document("data: 2024-01-01\nother: 2024-01-01 10:00:00\n")
    assert message.payload == {"data": "2024-01-01", "other": "2024-01-01 10:00:00"}


# ---- truncation ------------------------------------------------------------


def test_default_truncation_drops_the_marker_and_lists_the_fields() -> None:
    message = parse_echo_document(_docs("scan_trunc128")[0], truncate_length=128)
    assert len(message.payload["ranges"]) == 128  # type: ignore[arg-type]
    assert len(message.payload["intensities"]) == 128  # type: ignore[arg-type]
    assert "..." not in message.payload["ranges"]  # type: ignore[operator]
    assert message.payload[TRUNCATED_FIELDS_KEY] == ["ranges", "intensities"]
    assert message.payload["ranges"][1] == 1.0010000467300415  # type: ignore[index]


def test_short_truncate_length() -> None:
    message = parse_echo_document(_docs("scan_trunc3")[0], truncate_length=3)
    assert message.payload["ranges"] == [1.0, 1.0010000467300415, 1.0019999742507935]
    assert message.payload[TRUNCATED_FIELDS_KEY] == ["header.frame_id", "ranges", "intensities"]
    assert message.payload["header"]["frame_id"] == "bas..."  # type: ignore[index]


def test_image_data_truncated_after_four_bytes() -> None:
    message = parse_echo_document(_docs("image_trunc4")[0], truncate_length=4)
    assert message.payload["data"] == [0, 0, 0, 0]
    assert TRUNCATED_FIELDS_KEY in message.payload
    assert "data" in message.payload[TRUNCATED_FIELDS_KEY]  # type: ignore[operator]


def test_no_marker_key_when_nothing_was_cut() -> None:
    message = parse_echo_document(_docs("twist")[0], truncate_length=128)
    assert TRUNCATED_FIELDS_KEY not in message.payload


def test_a_list_that_merely_ends_in_dots_is_not_cut() -> None:
    message = parse_echo_document("names:\n- a\n- '...'\n", truncate_length=128)
    assert message.payload["names"] == ["a", "..."]
    assert TRUNCATED_FIELDS_KEY not in message.payload


def test_cut_detection_needs_an_active_truncate_length() -> None:
    doc = "data: abcd...\nxs:\n- 1\n- '...'\n"
    message = parse_echo_document(doc, truncate_length=None)
    assert TRUNCATED_FIELDS_KEY not in message.payload
    assert message.payload["xs"] == [1, "..."]


def test_cuts_inside_lists_of_structs_use_dotted_field_paths() -> None:
    doc = "transforms:\n- child_frame_id: ab...\n  x: 1\n- child_frame_id: cd\n  x: 2\n- '...'\n"
    message = parse_echo_document(doc, truncate_length=2)
    assert message.payload[TRUNCATED_FIELDS_KEY] == ["transforms", "transforms.child_frame_id"]
    assert len(message.payload["transforms"]) == 2  # type: ignore[arg-type]


# ---- summaries, nan/inf ----------------------------------------------------


def test_no_arr_summaries_are_plain_strings() -> None:
    message = parse_echo_document(_docs("scan_noarr")[0])
    assert message.payload["ranges"] == "<sequence type: float, length: 541>"
    assert message.payload["intensities"] == "<sequence type: float, length: 541>"
    assert message.payload["angle_min"] == -2.356194496154785


def test_image_no_arr_keeps_scalars_aligned() -> None:
    message = parse_echo_document(_docs("image_noarr")[0])
    assert message.payload["width"] == 640 and message.payload["height"] == 480
    assert message.payload["encoding"] == "rgb8"
    assert message.payload["step"] == 1920
    assert message.payload["data"] == "<sequence type: uint8, length: 921600>"
    assert message.stamp_source == "header"


def test_nan_and_inf_become_strings_that_survive_json() -> None:
    message = parse_echo_document(_docs("scan_edge")[0])
    assert message.payload["ranges"] == ["inf", "nan", "-inf", 1.5]
    assert json.loads(json.dumps(message.payload))["ranges"][1] == "nan"
    assert math.isnan(float(message.payload["ranges"][1]))  # type: ignore[index]


def test_binary_scalar_becomes_a_list_of_ints() -> None:
    message = parse_echo_document("data: !!binary |\n  AAEC\n")
    assert message.payload["data"] == [0, 1, 2]


# ---- failure ---------------------------------------------------------------


def test_unparseable_document_keeps_its_text() -> None:
    message = parse_echo_document("a: [unclosed")
    assert message.payload == {RAW_TEXT_KEY: "a: [unclosed"}
    assert message.timestamp_ns == 0 and message.stamp_source == "none"


def test_a_non_mapping_document_keeps_its_text() -> None:
    message = parse_echo_document("just text")
    assert RAW_TEXT_KEY in message.payload


def test_raw_text_is_absent_on_success() -> None:
    assert RAW_TEXT_KEY not in parse_echo_document(_docs("twist")[0]).payload
