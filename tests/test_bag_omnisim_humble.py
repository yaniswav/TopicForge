"""Regression tests on a real Humble bag recorded by the OmniSim team.

The bag stores no message definitions, so reading it without the Humble type
definitions fails ("Bag contains no type definitions"). See
`fixtures/bags/omnisim_humble/README.txt`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from topicforge.services.bag_service import BagService

pytestmark = pytest.mark.requires_rosbags

BAG_DIR = Path(__file__).parent / "fixtures" / "bags" / "omnisim_humble"
BAG_DB3 = BAG_DIR / "omnisim_husky_topicforge_20261002_190546_0.db3"


@pytest.fixture(params=[BAG_DIR, BAG_DB3], ids=["directory", "db3-file"])
def bag_path(request: pytest.FixtureRequest) -> str:
    pytest.importorskip("rosbags")
    return str(request.param)


def test_peek_scan_decodes_541_ranges_with_the_right_geometry(bag_path: str) -> None:
    result = BagService().peek_samples(bag_path, "/scan", 2)
    assert result.count == 2
    scan = result.samples[0].payload
    assert scan["_decode_status"] == "full"
    ranges = scan["ranges"]
    assert len(ranges) == 541
    # Beams are indexed from angle_min (-135 deg): 90 -> -90 deg, 270 -> 0, 450 -> +90.
    assert ranges[90] == pytest.approx(1.50002, abs=1e-4)
    assert ranges[270] == pytest.approx(2.79881, abs=1e-4)
    assert ranges[450] == pytest.approx(2.00002, abs=1e-4)
    assert scan["angle_min"] == pytest.approx(-2.351834774, abs=1e-6)
    assert scan["header"]["frame_id"] == "base_laser"


def test_peek_says_which_typestore_was_assumed(bag_path: str) -> None:
    result = BagService().peek_samples(bag_path, "/scan", 1)
    assert result.note is not None
    assert "Humble" in result.note
    assert "no message definitions" in result.note


def test_peek_other_topics_decode(bag_path: str) -> None:
    for topic, field in [("/odom", "pose"), ("/imu/data", "orientation"), ("/tf", "transforms")]:
        result = BagService().peek_samples(bag_path, topic, 1)
        assert result.count == 1, topic
        assert field in result.samples[0].payload, topic


def test_peek_result_is_json_serializable(bag_path: str) -> None:
    result = BagService().peek_samples(bag_path, "/scan", 1)
    assert '"ranges"' in result.model_dump_json()


def test_analyze_counts_match_ros2_bag_info(bag_path: str) -> None:
    analysis = BagService().analyze(bag_path)
    counts = {t.name: t.message_count for t in analysis.topics}
    assert analysis.message_count == 1434
    assert counts["/scan"] == 177
    assert counts["/clock"] == 353
    assert counts["/tf"] == 266
    assert counts["/tf_static"] == 2
    assert counts["/rosout"] == 13
    assert len(counts) == 11
    assert analysis.duration_seconds == pytest.approx(35.358876725, abs=1e-6)


def test_analyze_rates_use_each_topics_own_span(bag_path: str) -> None:
    analysis = BagService().analyze(bag_path)
    by_name = {t.name: t for t in analysis.topics}
    assert by_name["/scan"].frequency_hz == pytest.approx(4.9974, abs=1e-4)
    assert by_name["/clock"].frequency_hz == pytest.approx(9.9965, abs=1e-4)
    assert by_name["/scan"].frequency_basis == "topic_span"


def test_analyze_flags_latched_topics(bag_path: str) -> None:
    by_name = {t.name: t for t in BagService().analyze(bag_path).topics}
    assert by_name["/tf_static"].latched is True
    assert by_name["/rosout"].latched is True
    assert by_name["/scan"].latched is False
    assert by_name["/tf_static"].frequency_hz is None
    assert by_name["/rosout"].frequency_hz is None


def test_peek_unknown_topic_lists_the_known_ones(bag_path: str) -> None:
    from topicforge.adapters.base import AdapterError

    with pytest.raises(AdapterError, match="not present in bag"):
        BagService().peek_samples(bag_path, "/nope", 1)


# ---- other containers and the array cap ------------------------------------


def _write_mcap_bag(directory: Path) -> Path:
    import numpy as np
    from rosbags.rosbag2 import StoragePlugin, Writer
    from rosbags.typesys import Stores, get_typestore

    typestore = get_typestore(Stores.ROS2_HUMBLE)
    bag = directory / "bag_mcap"
    with Writer(bag, version=Writer.VERSION_LATEST, storage_plugin=StoragePlugin.MCAP) as writer:
        fixed = writer.add_connection("/fast", "std_msgs/msg/Int32", typestore=typestore)
        big = writer.add_connection("/big", "std_msgs/msg/ByteMultiArray", typestore=typestore)
        for i in range(5):
            msg = typestore.types["std_msgs/msg/Int32"](data=i)
            writer.write(fixed, i * 500_000_000, typestore.serialize_cdr(msg, "std_msgs/msg/Int32"))
        layout = typestore.types["std_msgs/msg/MultiArrayLayout"](dim=[], data_offset=0)
        arr = typestore.types["std_msgs/msg/ByteMultiArray"](
            layout=layout, data=np.ones(5000, dtype=np.uint8)
        )
        writer.write(big, 0, typestore.serialize_cdr(arr, "std_msgs/msg/ByteMultiArray"))
    return bag


def test_mcap_bag_rates_come_from_scanned_timestamps(tmp_path: Path) -> None:
    pytest.importorskip("rosbags")
    bag = _write_mcap_bag(tmp_path)
    analysis = BagService().analyze(str(bag))
    by_name = {t.name: t for t in analysis.topics}
    assert by_name["/fast"].message_count == 5
    assert by_name["/fast"].frequency_hz == pytest.approx(2.0)
    assert by_name["/fast"].frequency_basis == "topic_span"
    assert by_name["/fast"].latched is None  # the writer recorded no QoS
    assert by_name["/big"].frequency_hz is None


def test_peek_cuts_arrays_over_4096_elements_and_says_so(tmp_path: Path) -> None:
    pytest.importorskip("rosbags")
    bag = _write_mcap_bag(tmp_path)
    result = BagService().peek_samples(str(bag), "/big", 1)
    data = result.samples[0].payload["data"]
    assert len(data) == 4096
    assert result.note is not None and "data" in result.note and "4096" in result.note


def test_peek_without_embedded_definitions_note_is_absent_for_self_describing_bags(
    tmp_path: Path,
) -> None:
    pytest.importorskip("rosbags")
    bag = _write_mcap_bag(tmp_path)
    result = BagService().peek_samples(str(bag), "/fast", 1)
    assert result.note is None or "no message definitions" not in result.note


def test_peek_labels_the_clock_of_each_timestamp(bag_path: str) -> None:
    scan = BagService().peek_samples(bag_path, "/scan", 1).samples[0]
    header = scan.payload["header"]["stamp"]
    assert scan.stamp_source == "header"
    assert scan.timestamp_ns == header["sec"] * 1_000_000_000 + header["nanosec"]
    assert scan.recorded_ns is not None

    log = BagService().peek_samples(bag_path, "/rosout", 1).samples[0]
    assert log.stamp_source == "payload"
    assert (
        log.timestamp_ns
        == log.payload["stamp"]["sec"] * 1_000_000_000 + log.payload["stamp"]["nanosec"]
    )


def test_peek_payload_has_no_message_type_markers(bag_path: str) -> None:
    payload = BagService().peek_samples(bag_path, "/scan", 1).samples[0].payload
    assert "msgtype" not in str(payload)


@pytest.mark.parametrize("topic", ["/clock", "/tf", "/tf_static", "/rosout"])
def test_peek_reads_the_time_from_the_body_of_clock_tf_and_log(bag_path: str, topic: str) -> None:
    samples = BagService().peek_samples(bag_path, topic, 3).samples
    assert samples
    for sample in samples:
        assert sample.stamp_source == "payload"
        assert sample.timestamp_ns != sample.recorded_ns
        if topic in ("/clock", "/tf"):
            # Sim time since the simulation started: seconds, not an epoch date.
            assert 0 < sample.timestamp_ns < 600 * 1_000_000_000
        else:
            # /tf_static and /rosout are stamped with the wall clock by their publishers.
            assert sample.timestamp_ns > 1_000_000_000_000_000_000


def test_peek_clock_stamp_equals_the_clock_field(bag_path: str) -> None:
    sample = BagService().peek_samples(bag_path, "/clock", 1).samples[0]
    clock = sample.payload["clock"]
    assert sample.timestamp_ns == clock["sec"] * 1_000_000_000 + clock["nanosec"]
