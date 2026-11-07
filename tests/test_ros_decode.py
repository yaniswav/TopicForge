"""Tests for `adapters/common/ros_decode.py`: no DDS binding needed (`rosbags` is core)."""

from __future__ import annotations

import ast
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from rosbags.typesys import Stores, get_typestore

from topicforge.adapters.common import DiscoveryCaches, apply_builtin_samples
from topicforge.adapters.common.dds_helpers import READER_PARTICIPANT_NAME, user_topic_result
from topicforge.adapters.common.ros_decode import (
    HEAVY_TYPES,
    MAX_RAW_SAMPLE_BYTES,
    DecodeRefused,
    IdlBuilders,
    RawRead,
    ReadOutcome,
    WriterFacts,
    build_idl_type,
    cap_decoded,
    dds_type_of,
    decode_cdr,
    fitting_reads,
    message_samples,
    plan_decode,
    rate_metrics,
    read_note,
    ros_type_of,
    typehash_from_user_data,
    writer_facts,
)
from topicforge.config import load_settings
from topicforge.models import HealthReport, TopicMetrics

STRING = "std_msgs::msg::dds_::String_"
JAZZY = get_typestore(Stores.ROS2_JAZZY)
SRC = Path(__file__).resolve().parents[1] / "src" / "topicforge"


def _plan(ros_type: str = "std_msgs/msg/String", **writer: Any) -> Any:
    facts = WriterFacts(dds_type_of(ros_type), **writer)
    return plan_decode("rt/x", [facts], distro="jazzy")


def _cdr(ros_type: str, **fields: Any) -> bytes:
    return bytes(JAZZY.serialize_cdr(JAZZY.types[ros_type](**fields), ros_type))


def _read(raw: bytes, source_ns: int = 5, received_ns: int = 7) -> RawRead:
    return RawRead(raw, source_ns, received_ns, len(raw))


def test_type_names_round_trip() -> None:
    assert ros_type_of(STRING) == "std_msgs/msg/String"
    assert dds_type_of("std_msgs/msg/String") == STRING
    assert ros_type_of("MyVendorType") is None


def test_typehash_comes_out_of_user_data() -> None:
    assert typehash_from_user_data(b"typehash=RIHS01_ab;") == "RIHS01_ab"
    assert typehash_from_user_data(b"other=1;typehash=RIHS01_cd;x=2;") == "RIHS01_cd"
    assert typehash_from_user_data(b"other=1;") is None
    assert typehash_from_user_data(None) is None


def test_writer_facts_read_a_discovery_sample() -> None:
    def policy(name: str, **attrs: Any) -> Any:
        return type(name, (), attrs)()

    sample = SimpleNamespace(
        type_name=STRING,
        qos=[
            policy("Reliable"),
            policy("TransientLocal"),
            policy("Userdata", data=b"typehash=RIHS01_ab;"),
        ],
    )
    facts = writer_facts(sample)
    assert facts == WriterFacts(STRING, "RIHS01_ab", reliable=True, transient_local=True)
    # An unreadable QoS must read as the volatile, history-less case.
    assert writer_facts(SimpleNamespace(type_name=STRING, qos=None)) == WriterFacts(STRING)


@pytest.mark.parametrize(
    ("topic", "writers", "reason"),
    [
        ("scan", [WriterFacts(STRING)], "not a ROS 2 topic"),
        ("rq/add_two_intsRequest", [WriterFacts(STRING)], "not a ROS 2 topic"),
        ("rt/x", [], "no writer"),
        ("rt/x", [WriterFacts(STRING), WriterFacts("a::msg::dds_::B_")], "disagree"),
        ("rt/x", [WriterFacts(None)], "disagree"),
        ("rt/x", [WriterFacts("VendorType")], "not a ROS 2 type name"),
        ("rt/x", [WriterFacts("my_pkg::msg::dds_::Custom_")], "custom type"),
        ("rt/x", [WriterFacts(STRING, "RIHS01_00")], "hash differs"),
        ("rt/x", [WriterFacts("my_pkg::msg::dds_::Custom_", "RIHS01_00")], "custom type"),
        ("rt/x", [WriterFacts(STRING, "RIHS01_00"), WriterFacts(STRING)], "different type hashes"),
    ],
)
def test_plan_refuses_with_the_reason(topic: str, writers: list[WriterFacts], reason: str) -> None:
    with pytest.raises(DecodeRefused, match=reason):
        plan_decode(topic, writers, distro="humble")


@pytest.mark.parametrize("ros_type", sorted(HEAVY_TYPES))
def test_heavy_types_are_refused(ros_type: str) -> None:
    with pytest.raises(DecodeRefused, match="too heavy"):
        plan_decode("rt/x", [WriterFacts(dds_type_of(ros_type))], distro="humble")


def test_the_writer_hash_selects_the_exact_store() -> None:
    typehash = JAZZY.hash_rihs01("std_msgs/msg/String")
    plan = plan_decode("rt/x", [WriterFacts(STRING, typehash)], distro="humble")
    assert plan.store_label == "ROS 2 Jazzy"
    assert plan.store_note == ""


def test_no_hash_uses_the_distro_and_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "Humble" in plan_decode("rt/x", [WriterFacts(STRING)], distro="humble").store_note
    assert "Jazzy" in plan_decode("rt/x", [WriterFacts(STRING)], distro="jazzy").store_note
    assert "Humble" in plan_decode("rt/x", [WriterFacts(STRING)], distro="unknown").store_note
    monkeypatch.delenv("ROS_DISTRO", raising=False)
    assert "Humble" in plan_decode("rt/x", [WriterFacts(STRING)]).store_note
    monkeypatch.setenv("ROS_DISTRO", "jazzy")
    assert "Jazzy" in plan_decode("rt/x", [WriterFacts(STRING)]).store_note


def test_latched_only_when_every_writer_is_reliable_and_transient_local() -> None:
    latched = WriterFacts(STRING, reliable=True, transient_local=True)
    assert plan_decode("rt/x", [latched], distro="jazzy").latched
    assert not plan_decode("rt/x", [latched, WriterFacts(STRING)], distro="jazzy").latched
    best_effort = WriterFacts(STRING, transient_local=True)
    assert not plan_decode("rt/x", [best_effort], distro="jazzy").latched


def _tuple_builders() -> IdlBuilders:
    return IdlBuilders(
        base={"float64": "f64", "uint32": "u32", "int32": "i32", "uint8": "u8", "float32": "f32"},
        string="str",
        bounded_string=lambda bound: ("bounded", bound),
        array=lambda element, size: ("array", element, size),
        sequence=lambda element, size: ("sequence", element, size),
        struct=lambda name, typename, fields: (name, typename, fields),
    )


def test_idl_tree_follows_the_definition_with_ros_type_names() -> None:
    name, typename, fields = build_idl_type(JAZZY, "nav_msgs/msg/Odometry", _tuple_builders(), {})
    assert (name, typename) == ("Odometry_", "nav_msgs::msg::dds_::Odometry_")
    assert list(fields) == ["header", "child_frame_id", "pose", "twist"]
    assert fields["child_frame_id"] == "str"
    assert fields["header"][1] == "std_msgs::msg::dds_::Header_"
    pose = fields["pose"][2]
    assert pose["covariance"] == ("array", "f64", 36)
    scan = build_idl_type(JAZZY, "sensor_msgs/msg/LaserScan", _tuple_builders(), {})
    assert scan[2]["ranges"] == ("sequence", "f32", 0)


def test_idl_tree_refuses_a_field_it_cannot_build() -> None:
    builders = _tuple_builders()
    builders = IdlBuilders(
        base={},
        string=builders.string,
        bounded_string=builders.bounded_string,
        array=builders.array,
        sequence=builders.sequence,
        struct=builders.struct,
    )
    with pytest.raises(DecodeRefused, match="not supported"):
        build_idl_type(JAZZY, "builtin_interfaces/msg/Time", builders, {})


def test_decode_gives_the_fields() -> None:
    raw = _cdr("std_msgs/msg/String", data="hello")
    payload = decode_cdr(_plan(), raw)
    assert payload["data"] == "hello"
    assert payload["_decode_status"] == "full"


@pytest.mark.parametrize("padding", [1, 2, 3])
def test_trailing_padding_is_tolerated(padding: int) -> None:
    raw = _cdr("std_msgs/msg/String", data="hello") + b"\0" * padding
    assert decode_cdr(_plan(), raw)["data"] == "hello"


def test_uninitialized_padding_is_tolerated() -> None:
    # Fast DDS does not zero padding: 6 bytes of it sit between the 5-character frame id and
    # the first float64 of the pose (measured on `LaserScan` and `Odometry`).
    types = JAZZY.types
    header = types["std_msgs/msg/Header"](
        stamp=types["builtin_interfaces/msg/Time"](sec=1, nanosec=2), frame_id="frame"
    )
    point = types["geometry_msgs/msg/Point"](x=1.5, y=2.5, z=3.5)
    quaternion = types["geometry_msgs/msg/Quaternion"](x=0.0, y=0.0, z=0.0, w=1.0)
    pose = types["geometry_msgs/msg/Pose"](position=point, orientation=quaternion)
    message = types["geometry_msgs/msg/PoseStamped"](header=header, pose=pose)
    raw = bytearray(JAZZY.serialize_cdr(message, "geometry_msgs/msg/PoseStamped"))
    assert raw[22:28] == bytes(6)
    raw[22:28] = bytes([0xAB]) * 6
    payload = decode_cdr(_plan("geometry_msgs/msg/PoseStamped"), bytes(raw))
    assert payload["_decode_status"] == "full"
    assert payload["pose"]["position"]["x"] == 1.5


def test_big_endian_cdr_decodes_with_the_same_values() -> None:
    little = JAZZY.types["std_msgs/msg/String"](data="hello")
    raw = bytes(JAZZY.serialize_cdr(little, "std_msgs/msg/String", little_endian=False))
    assert raw[:2] == b"\x00\x00"
    assert decode_cdr(_plan(), raw)["data"] == "hello"


def test_xcdr2_is_raw_not_a_guess() -> None:
    raw = b"\x00\x09\x00\x00" + _cdr("std_msgs/msg/String", data="hello")[4:]
    payload = decode_cdr(_plan(), raw)
    assert payload["_decode_status"] == "raw"
    assert "encapsulation" in payload["_decode_note"]
    assert "data" not in payload


def test_a_wrong_definition_never_shows_a_value() -> None:
    header = JAZZY.types["std_msgs/msg/Header"](
        stamp=JAZZY.types["builtin_interfaces/msg/Time"](sec=5, nanosec=5), frame_id="frame"
    )
    raw = bytes(JAZZY.serialize_cdr(header, "std_msgs/msg/Header"))
    payload = decode_cdr(_plan("std_msgs/msg/UInt32"), raw)
    assert payload["_decode_status"] == "raw"
    assert "data" not in payload


def test_garbage_is_raw() -> None:
    payload = decode_cdr(_plan(), b"\x00\x01\x00\x00\xff\xff\xff\xff")
    assert payload["_decode_status"] == "raw"


def test_samples_have_the_bag_shape_with_stamps_and_non_finite_strings() -> None:
    plan = _plan("sensor_msgs/msg/LaserScan")
    types = JAZZY.types
    header = types["std_msgs/msg/Header"](
        stamp=types["builtin_interfaces/msg/Time"](sec=3, nanosec=4), frame_id="laser"
    )
    import numpy as np

    scan = types["sensor_msgs/msg/LaserScan"](
        header=header,
        angle_min=0.0,
        angle_max=1.0,
        angle_increment=0.5,
        time_increment=0.0,
        scan_time=0.1,
        range_min=0.1,
        range_max=10.0,
        ranges=np.array([1.0, float("inf"), float("nan")], dtype=np.float32),
        intensities=np.array([], dtype=np.float32),
    )
    raw = bytes(JAZZY.serialize_cdr(scan, "sensor_msgs/msg/LaserScan"))
    sample = message_samples("/scan", plan, [_read(raw)])[0]
    assert sample.topic == "/scan"
    assert sample.message_type == "sensor_msgs/msg/LaserScan"
    assert (sample.timestamp_ns, sample.stamp_source) == (3_000_000_004, "header")
    assert sample.received_ns == 7
    assert sample.payload["ranges"] == [1.0, "inf", "nan"]
    assert sample.summary is not None


def test_headerless_sample_is_stamped_by_the_writer() -> None:
    raw = _cdr("std_msgs/msg/String", data="x")
    sample = message_samples("/x", _plan(), [_read(raw, source_ns=11)])[0]
    assert (sample.timestamp_ns, sample.stamp_source) == (11, "dds_source")


def test_long_arrays_are_cut_and_named() -> None:
    import numpy as np

    types = JAZZY.types
    msg = types["std_msgs/msg/Float64MultiArray"](
        layout=types["std_msgs/msg/MultiArrayLayout"](dim=[], data_offset=0),
        data=np.arange(5000, dtype=np.float64),
    )
    raw = bytes(JAZZY.serialize_cdr(msg, "std_msgs/msg/Float64MultiArray"))
    sample = message_samples("/x", _plan("std_msgs/msg/Float64MultiArray"), [_read(raw)])[0]
    assert len(sample.payload["data"]) == 4096
    assert sample.payload["_truncated_fields"] == ["data"]


def test_response_cap_drops_and_says_so() -> None:
    raw = _cdr("std_msgs/msg/String", data="x" * 1000)
    samples = message_samples("/x", _plan(), [_read(raw)] * 5)
    kept, note = cap_decoded(samples, max_bytes=3000)
    assert 0 < len(kept) < 5
    assert note is not None
    assert "dropped" in note
    assert cap_decoded(samples)[1] is None


def test_oversize_reads_are_not_decoded() -> None:
    big = RawRead(b"", 1, 2, MAX_RAW_SAMPLE_BYTES + 1)
    kept, note = fitting_reads(ReadOutcome([big, _read(b"\x00\x01")]))
    assert len(kept) == 1
    assert note is not None


def test_read_notes_tell_silent_from_unmatched_from_incompatible() -> None:
    assert read_note(ReadOutcome([_read(b"")] * 2, 1, 0, 1.0), 2) is None
    assert "1 of 2" in str(read_note(ReadOutcome([_read(b"")], 1, 0, 1.0), 2))
    assert "silent" in str(read_note(ReadOutcome([], 1, 0, 5.0), 1))
    assert "no writer matched" in str(read_note(ReadOutcome([], 0, 0, 5.0), 1))
    assert "incompatible" in str(read_note(ReadOutcome([], 0, 1, 5.0), 1))


def _stamped(*stamps_s: float) -> list[RawRead]:
    return [RawRead(b"", int(s * 1e9), 0, 0) for s in stamps_s]


def test_rate_comes_from_the_writer_timestamps() -> None:
    outcome = ReadOutcome(_stamped(10.0, 10.1, 10.2, 10.3), 1, 0, 2.0)
    metrics = rate_metrics("/t", 60, outcome, declared_hz=None)
    assert metrics.frequency_basis == "source_timestamps"
    assert metrics.status == "ok"
    assert metrics.samples_observed == 4
    assert metrics.observed_frequency_hz == pytest.approx(10.0)
    assert metrics.window_actual_s == pytest.approx(0.3)
    assert metrics.latency_available is False
    assert metrics.sequence_numbers_available is False


def test_a_clock_jump_gives_no_rate() -> None:
    # Measured on WSL: 30 samples of a 10 Hz writer spanning 5.8 s in a 3 s read read as 5 Hz.
    outcome = ReadOutcome(_stamped(10.0, 10.1, 13.1, 13.2), 1, 0, 1.0)
    metrics = rate_metrics("/t", 60, outcome, declared_hz=None)
    assert metrics.observed_frequency_hz is None
    assert metrics.samples_observed == 4
    assert "clock jumped" in str(metrics.note)


def test_silent_topic_has_no_rate_and_says_why() -> None:
    metrics = rate_metrics("/t", 60, ReadOutcome([], 1, 0, 5.0), declared_hz=2.0)
    assert metrics.status == "no_samples_yet"
    assert metrics.observed_frequency_hz is None
    assert metrics.declared_frequency_hz == 2.0
    assert "silent" in str(metrics.note)


def test_one_sample_has_no_rate() -> None:
    metrics = rate_metrics("/t", 60, ReadOutcome(_stamped(10.0), 1, 0, 5.0), declared_hz=None)
    assert metrics.status == "ok"
    assert metrics.observed_frequency_hz is None


def test_refused_peek_names_the_reason_and_keeps_the_default_note_otherwise() -> None:
    refused = user_topic_result("/x", "live", refused="custom type")
    assert refused.count == 0
    assert "custom type" in str(refused.note)
    assert "does not mean the topic is silent" in str(refused.note)
    assert "payload decoding is disabled" in str(user_topic_result("/x", "live").note)


# ----- the tracker never reports TopicForge's own read participants -----


def _guid(n: int) -> uuid.UUID:
    return uuid.UUID(bytes=bytes([0x01, 0x10]) + n.to_bytes(14, "big"))


def _info(*, valid: bool = True, state: int = 16) -> Any:
    return SimpleNamespace(valid_data=valid, instance_state=state, source_timestamp=1_000)


def _named(n: int, name: str) -> Any:
    policy = type("EntityName", (), {"name": name})()
    return SimpleNamespace(key=_guid(n), qos=[policy], sample_info=_info())


def _gone(n: int) -> Any:
    return SimpleNamespace(key=_guid(n), sample_info=_info(valid=False, state=32))


def _reader_of(n: int, owner: int) -> Any:
    return SimpleNamespace(
        key=_guid(n),
        participant_key=_guid(owner),
        topic_name="rt/x",
        type_name=STRING,
        qos=[],
        sample_info=_info(),
    )


def _pass(caches: DiscoveryCaches, parts: list, subs: list, now: int = 2_000) -> None:
    apply_builtin_samples(caches, parts, [], subs, now, domain_id=0)


def _assert_nothing_recorded(caches: DiscoveryCaches) -> None:
    snap = caches.snapshot(0)
    assert snap.participants == []
    assert snap.subscriptions == []
    assert snap.departed == []
    assert snap.participant_infos == []
    assert caches.lifecycle.events_since(lookback_s=86400, domain_id=0, now_ns=2_000) == []


def test_a_read_named_participant_leaves_no_trace() -> None:
    caches = DiscoveryCaches()
    _pass(caches, [_named(7, READER_PARTICIPANT_NAME)], [_reader_of(70, 7)])
    _assert_nothing_recorded(caches)
    _pass(caches, [_gone(7)], [])
    _assert_nothing_recorded(caches)


def test_a_registered_guid_leaves_no_trace_even_without_its_name() -> None:
    caches = DiscoveryCaches()
    from topicforge.adapters.common.endpoints import format_participant_key

    caches.own_participants.add(format_participant_key(_guid(8)))
    _pass(caches, [SimpleNamespace(key=_guid(8), qos=[], sample_info=_info())], [_reader_of(80, 8)])
    _pass(caches, [_gone(8)], [])
    _assert_nothing_recorded(caches)


def test_other_participants_are_still_recorded_alongside() -> None:
    caches = DiscoveryCaches()
    _pass(
        caches,
        [_named(7, READER_PARTICIPANT_NAME), _named(9, "robot")],
        [_reader_of(70, 7), _reader_of(90, 9)],
    )
    snap = caches.snapshot(0)
    assert [p.name for p in snap.participant_infos] == ["robot"]
    assert len(snap.subscriptions) == 1
    # Its count of endpoints is the robot's alone, and the robot's leave is still an event.
    _pass(caches, [_gone(9)], [], now=3_000)
    types = [
        e.event_type
        for e in caches.lifecycle.events_since(lookback_s=86400, domain_id=0, now_ns=3_000)
    ]
    assert types == ["lost", "discovered"]


def test_memory_of_own_participants_is_bounded() -> None:
    caches = DiscoveryCaches()
    for n in range(1100):
        caches.own_participants.add(str(n))
    assert "0" not in caches.own_participants
    assert "1099" in caches.own_participants


# ----- the flag and what the read may create -----


@pytest.mark.parametrize(("value", "expected"), [("off", False), ("on", True), (" ON ", True)])
def test_flag_values(value: str, expected: bool) -> None:
    assert load_settings({"TOPICFORGE_DDS_DECODE": value}).dds_decode is expected


def test_flag_defaults_off_and_rejects_the_rest() -> None:
    assert load_settings({}).dds_decode is False
    with pytest.raises(ValueError, match="TOPICFORGE_DDS_DECODE"):
        load_settings({"TOPICFORGE_DDS_DECODE": "yes"})


def test_new_fields_default_to_the_flag_off_values() -> None:
    assert HealthReport.model_fields["dds_decode"].default is False
    assert TopicMetrics.model_fields["frequency_basis"].default is None


def test_the_read_module_imports_no_writer_and_no_private_symbol() -> None:
    tree = ast.parse((SRC / "adapters/dds_cyclone/user_read.py").read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.add(str(node.module))
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    assert not names & {"DataWriter", "Publisher", "cyclonedds.pub"}
    assert not {n for n in names if "_clayer" in n or n == "ddspy_take"}
