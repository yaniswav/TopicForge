"""Pure tests of the message timestamp rule shared by the live and bag readers."""

from __future__ import annotations

import pytest

import topicforge
from topicforge.adapters.common.stamps import payload_stamp, time_to_ns
from topicforge.adapters.ros2_live.echo_parser import parse_echo_document
from topicforge.config import Settings
from topicforge.server import build_app

NS = 1_000_000_000


def _time(sec: int, nanosec: int) -> dict[str, int]:
    return {"sec": sec, "nanosec": nanosec}


def test_header_message_uses_header_stamp() -> None:
    payload = {"header": {"stamp": _time(5, 7), "frame_id": "map"}, "data": 1}
    assert payload_stamp(payload) == (5 * NS + 7, "header")


def test_clock_uses_the_clock_field() -> None:
    assert payload_stamp({"clock": _time(12, 500)}) == (12 * NS + 500, "payload")


def test_tf_message_uses_the_first_transform() -> None:
    transforms = [
        {"header": {"stamp": _time(3, 1), "frame_id": "odom"}, "child_frame_id": "a"},
        {"header": {"stamp": _time(9, 9), "frame_id": "odom"}, "child_frame_id": "b"},
    ]
    assert payload_stamp({"transforms": transforms}) == (3 * NS + 1, "payload")


def test_tf_message_without_transforms_has_no_stamp() -> None:
    assert payload_stamp({"transforms": []}) is None


def test_log_uses_stamp_only_with_level_and_msg() -> None:
    log = {"stamp": _time(2, 0), "level": 20, "name": "n", "msg": "hi"}
    assert payload_stamp(log) == (2 * NS, "payload")
    assert payload_stamp({"stamp": _time(2, 0), "other": 1}) is None


def test_header_wins_over_body_time() -> None:
    payload = {"header": {"stamp": _time(1, 0)}, "clock": _time(8, 0)}
    assert payload_stamp(payload) == (NS, "header")


@pytest.mark.parametrize(
    "payload",
    [
        {"data": "hello"},
        {"linear": {"x": 1.0}},
        {"clock": "soon"},
        {"clock": {"sec": 1}},
        {"clock": {"sec": True, "nanosec": 0}},
        {"transforms": [{"child_frame_id": "a"}]},
        {"header": {"stamp": "soon"}},
    ],
)
def test_message_without_a_time_has_no_stamp(payload: dict) -> None:
    assert payload_stamp(payload) is None


def test_time_to_ns_rejects_non_time() -> None:
    assert time_to_ns(None) is None
    assert time_to_ns(_time(0, 0)) == 0


def test_echo_clock_document() -> None:
    message = parse_echo_document("clock:\n  sec: 35\n  nanosec: 250\n")
    assert message.timestamp_ns == 35 * NS + 250
    assert message.stamp_source == "payload"


def test_echo_tf_document_and_empty_tf() -> None:
    doc = (
        "transforms:\n- header:\n    stamp:\n      sec: 4\n      nanosec: 2\n"
        "    frame_id: odom\n  child_frame_id: base_link\n"
    )
    message = parse_echo_document(doc)
    assert (message.timestamp_ns, message.stamp_source) == (4 * NS + 2, "payload")
    empty = parse_echo_document("transforms: []\n")
    assert (empty.timestamp_ns, empty.stamp_source) == (0, "none")


def test_echo_log_document() -> None:
    doc = "stamp:\n  sec: 6\n  nanosec: 0\nlevel: 20\nname: n\nmsg: hi\n"
    message = parse_echo_document(doc)
    assert (message.timestamp_ns, message.stamp_source) == (6 * NS, "payload")


def test_echo_headerless_message_stays_none() -> None:
    message = parse_echo_document("data: hello\n")
    assert (message.timestamp_ns, message.stamp_source) == (0, "none")


def test_initialize_advertises_topicforge_version() -> None:
    app = build_app(
        Settings(mode="mock", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False)
    )
    options = app._lowlevel_server.create_initialization_options()
    assert options.server_name == "topicforge"
    assert options.server_version == topicforge.__version__
