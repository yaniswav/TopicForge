"""Unit tests for examples/dds/nodes/spec.py (pure Python, no DDS binding)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples" / "dds" / "nodes"))

import spec


def test_parse_defaults() -> None:
    ep = spec.parse_endpoint("scan:LidarScan")
    assert ep == spec.Endpoint("scan", "LidarScan", spec.QosSpec())
    assert ep.qos.history_depth == 1


def test_parse_all_options() -> None:
    ep = spec.parse_endpoint("scan:LidarScan:best_effort,transient_local,keep_last=5,deadline=200")
    assert ep.qos == spec.QosSpec("best_effort", "transient_local", 5, 200)


def test_parse_keep_all() -> None:
    assert spec.parse_endpoint("t:Imu:keep_all").qos.history_depth is None


def test_parse_empty_options_segment() -> None:
    assert spec.parse_endpoint("t:Imu:").qos == spec.QosSpec()


@pytest.mark.parametrize(
    "text",
    [
        "scan",
        "a:b:c:d",
        ":LidarScan",
        "scan:Nope",
        "scan:LidarScan:fast",
        "scan:LidarScan:keep_last=0",
        "scan:LidarScan:keep_last=x",
        "scan:LidarScan:keep_last",
        "scan:LidarScan:deadline=0",
        "scan:LidarScan:reliable,best_effort",
        "scan:LidarScan:volatile,transient_local",
        "scan:LidarScan:keep_all,keep_last=3",
    ],
)
def test_parse_errors(text: str) -> None:
    with pytest.raises(ValueError):
        spec.parse_endpoint(text)


def test_describe() -> None:
    ep = spec.parse_endpoint("scan:LidarScan:best_effort")
    assert spec.describe(ep) == "scan (LidarScan, BEST_EFFORT, VOLATILE, KEEP_LAST 1)"
    ep = spec.parse_endpoint("odom:Odom:keep_all,deadline=100")
    assert spec.describe(ep) == "odom (Odom, RELIABLE, VOLATILE, KEEP_ALL), deadline 100 ms"


@pytest.mark.parametrize("type_name", list(spec.TYPES))
def test_sample_values_cover_every_field(type_name: str) -> None:
    values = spec.sample_values(type_name, 7)
    assert list(values) == [name for name, _ in spec.TYPES[type_name]]
    assert values["seq"] == 7
    for name, kind in spec.TYPES[type_name]:
        expected = {"uint32": int, "float32": float, "float64": float, "string": str}[kind]
        assert isinstance(values[name], expected)
    assert values == spec.sample_values(type_name, 7)


def test_types_start_with_seq() -> None:
    assert all(fields[0] == ("seq", "uint32") for fields in spec.TYPES.values())


def test_parser_requires_an_endpoint_and_name() -> None:
    with pytest.raises(SystemExit):
        spec.parse_args("X", ["--name", "n"])
    with pytest.raises(SystemExit):
        spec.parse_args("X", ["--write", "t:Imu"])


@pytest.mark.parametrize("domain", ["-1", "233", "abc"])
def test_parser_rejects_bad_domain(domain: str) -> None:
    with pytest.raises(SystemExit):
        spec.parse_args("X", ["--name", "n", "--write", "t:Imu", "--domain", domain])


def test_parser_rejects_bad_endpoint() -> None:
    with pytest.raises(SystemExit):
        spec.parse_args("X", ["--name", "n", "--write", "t:Nope"])


def test_start_line() -> None:
    args = spec.parse_args(
        "X",
        [
            "--name",
            "lidar_driver",
            "--domain",
            "3",
            "--write",
            "scan:LidarScan:best_effort",
            "--read",
            "odom:Odom",
        ],
    )
    assert args.rate_hz == 10.0
    assert spec.start_line("cyclone", args) == (
        "[lidar_driver] cyclone domain 3: "
        "writes scan (LidarScan, BEST_EFFORT, VOLATILE, KEEP_LAST 1); "
        "reads odom (Odom, RELIABLE, VOLATILE, KEEP_LAST 1)"
    )


def test_start_line_nothing() -> None:
    args = spec.parse_args("X", ["--name", "n", "--read", "t:Imu"])
    assert "writes nothing; reads t (Imu" in spec.start_line("dust", args)


def test_parse_safety_options() -> None:
    ep = spec.parse_endpoint(
        "scan:LidarScan:reliable,partition=left|right,liveliness=manual_topic,lease=500,"
        "ownership=exclusive,strength=10",
        "write",
    )
    assert ep.qos.partition == ("left", "right")
    assert ep.qos.liveliness == "manual_topic"
    assert ep.qos.lease_ms == 500
    assert ep.qos.ownership == "exclusive"
    assert ep.qos.strength == 10


def test_safety_defaults() -> None:
    qos = spec.parse_endpoint("t:Imu").qos
    assert (qos.partition, qos.liveliness, qos.lease_ms) == ((), "automatic", None)
    assert (qos.ownership, qos.strength) == ("shared", 0)


def test_lease_without_liveliness_is_automatic() -> None:
    qos = spec.parse_endpoint("t:Imu:lease=300").qos
    assert (qos.liveliness, qos.lease_ms) == ("automatic", 300)


def test_strength_zero_allowed_on_writer() -> None:
    assert spec.parse_endpoint("t:Imu:strength=0", "write").qos.strength == 0


def test_strength_on_reader_rejected() -> None:
    with pytest.raises(ValueError, match="writers"):
        spec.parse_endpoint("t:Imu:ownership=exclusive,strength=3", "read")


@pytest.mark.parametrize(
    "text",
    [
        "t:Imu:partition=",
        "t:Imu:partition=a||b",
        "t:Imu:liveliness=sometimes",
        "t:Imu:liveliness",
        "t:Imu:lease=0",
        "t:Imu:lease=x",
        "t:Imu:ownership=both",
        "t:Imu:strength=-1",
        "t:Imu:strength=x",
    ],
)
def test_safety_option_errors(text: str) -> None:
    with pytest.raises(ValueError):
        spec.parse_endpoint(text)


def test_describe_safety_options() -> None:
    ep = spec.parse_endpoint(
        "scan:LidarScan:partition=a|b,liveliness=manual_topic,lease=500,ownership=exclusive,"
        "strength=10",
        "write",
    )
    assert spec.describe(ep) == (
        "scan (LidarScan, RELIABLE, VOLATILE, KEEP_LAST 1), partition a|b, "
        "liveliness MANUAL_TOPIC lease 500 ms, ownership EXCLUSIVE strength 10"
    )


def test_stop_asserting_after_option() -> None:
    args = spec.parse_args("X", ["--name", "n", "--write", "t:Imu", "--stop-asserting-after", "3"])
    assert args.stop_asserting_after == 3.0
    assert spec.parse_args("X", ["--name", "n", "--write", "t:Imu"]).stop_asserting_after is None
    with pytest.raises(SystemExit):
        spec.parse_args("X", ["--name", "n", "--read", "t:Imu:strength=1"])
