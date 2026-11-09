"""`TOPICFORGE_DDS_DECODE` against the real binding: a writer on a domain of this test's own.

Skipped without `cyclonedds`; runs in `.venv-demo` and `demo.yml`. The writer is a plain Cyclone
writer with a ROS 2 topic and type name, which is enough for the path through the binding. The
comparison with real ROS 2 writers is the bench's (`tests/integration/ros2/decode_check.py`).
"""

import threading
import time
from dataclasses import dataclass
from typing import Any

import pytest

cyclonedds = pytest.importorskip("cyclonedds")
pytestmark = pytest.mark.requires_cyclonedds

from cyclonedds.core import Policy, Qos
from cyclonedds.domain import DomainParticipant
from cyclonedds.idl import IdlStruct
from cyclonedds.idl.annotations import final
from cyclonedds.pub import DataWriter
from cyclonedds.topic import Topic
from rosbags.interfaces import Nodetype
from rosbags.typesys import Stores, get_typestore

from topicforge.adapters.base import AdapterError
from topicforge.adapters.common.ros_decode import MAX_RAW_SAMPLE_BYTES, build_idl_type
from topicforge.adapters.dds_cyclone import CycloneDdsAdapter
from topicforge.adapters.dds_cyclone.user_read import _BASE, _BUILDERS, _drain, _Serialized

DOMAIN = 97
RATE_HZ = 20.0


@final
@dataclass
class String(IdlStruct, typename="std_msgs::msg::dds_::String_"):
    data: str


@final
@dataclass
class Custom(IdlStruct, typename="my_pkg::msg::dds_::Custom_"):
    data: str


class Talker(threading.Thread):
    """Publishes `decoded N` at `RATE_HZ` on `rt/decode_test`, plain CDR like a ROS 2 writer."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.stopping = threading.Event()
        qos = Qos(Policy.DataRepresentation(use_cdrv0_representation=True))
        self.dp = DomainParticipant(DOMAIN, qos=Qos(Policy.EntityName("talker")))
        self.writer = DataWriter(self.dp, Topic(self.dp, "rt/decode_test", String), qos=qos)
        self.custom = DataWriter(self.dp, Topic(self.dp, "rt/decode_custom", Custom), qos=qos)
        self.plain = DataWriter(self.dp, Topic(self.dp, "not_ros", String), qos=qos)

    def run(self) -> None:
        n = 0
        while not self.stopping.wait(1 / RATE_HZ):
            self.writer.write(String(data=f"decoded {n}"))
            n += 1


@pytest.fixture(scope="module")
def talker() -> Any:
    thread = Talker()
    thread.start()
    yield thread
    thread.stopping.set()
    thread.join(2)


@pytest.fixture(scope="module")
def adapter(talker: Talker) -> Any:
    instance = CycloneDdsAdapter(domain_id=DOMAIN, decode=True)
    instance.await_discovery_ready()
    time.sleep(2)
    yield instance
    instance.close()


def test_samples_are_decoded(adapter: CycloneDdsAdapter) -> None:
    assert adapter.dds_decode_enabled
    result = adapter.peek_dds_samples("/decode_test", 3)
    assert result.count == 3
    for sample in result.samples:
        assert sample.message_type == "std_msgs/msg/String"
        assert sample.payload["_decode_status"] == "full"
        assert str(sample.payload["data"]).startswith("decoded ")
        assert sample.stamp_source == "dds_source"
        assert sample.timestamp_ns > 0


def test_count_zero_creates_no_reader(adapter: CycloneDdsAdapter) -> None:
    result = adapter.peek_dds_samples("rt/decode_test", 0)
    assert (result.count, result.samples) == (0, [])


def test_the_rate_is_the_writers(adapter: CycloneDdsAdapter) -> None:
    metrics = adapter.topic_metrics("/decode_test", 3, DOMAIN)
    assert metrics.frequency_basis == "reader_arrivals"
    assert metrics.status == "ok"
    assert metrics.observed_frequency_hz == pytest.approx(RATE_HZ, rel=0.25)


def test_builtin_topics_keep_their_basis(adapter: CycloneDdsAdapter) -> None:
    metrics = adapter.topic_metrics("DCPSParticipant", 3, DOMAIN)
    assert metrics.frequency_basis == "peek_cadence"


@pytest.mark.parametrize(
    ("topic", "reason"),
    [("not_ros", "not a ROS 2 topic"), ("rt/decode_custom", "custom type")],
)
def test_other_topics_keep_the_empty_result_with_the_reason(
    adapter: CycloneDdsAdapter, topic: str, reason: str
) -> None:
    result = adapter.peek_dds_samples(topic, 1)
    assert result.count == 0
    assert reason in str(result.note)
    metrics = adapter.topic_metrics(topic, 2, DOMAIN)
    assert metrics.status == "unsupported_user_topic"
    assert reason in str(metrics.note)


def test_a_topic_not_on_the_bus_still_raises(adapter: CycloneDdsAdapter) -> None:
    with pytest.raises(AdapterError, match="not discovered"):
        adapter.peek_dds_samples("rt/nobody", 1)
    assert adapter.topic_metrics("rt/nobody", 2, DOMAIN).status == "unsupported_user_topic"


def test_reads_leave_no_trace_in_any_listing(adapter: CycloneDdsAdapter) -> None:
    for _ in range(3):
        adapter.peek_dds_samples("/decode_test", 1)
    time.sleep(1.2)  # two tracker passes
    names = {p.name for p in adapter.list_participants(DOMAIN)}
    assert names == {"talker", "topicforge"}
    events = adapter.participant_events(DOMAIN, 3600)
    assert {e.name for e in events} <= {"talker", "topicforge"}
    assert all(e.event_type == "discovered" for e in events)
    listing = adapter.list_endpoints(include_observer=True, include_departed=True)
    assert {e.participant_name for e in listing.endpoints} <= {"talker", "topicforge"}


def test_flag_off_creates_no_reader_and_answers_as_before(talker: Talker) -> None:
    off = CycloneDdsAdapter(domain_id=DOMAIN)
    try:
        off.await_discovery_ready()
        time.sleep(1)
        assert not off.dds_decode_enabled
        result = off.peek_dds_samples("/decode_test", 1)
        assert result.count == 0
        assert "payload decoding is disabled" in str(result.note)
        metrics = off.topic_metrics("/decode_test", 2, DOMAIN)
        assert metrics.status == "unsupported_user_topic"
        assert metrics.frequency_basis is None
        assert off.topic_metrics("DCPSParticipant", 2, DOMAIN).frequency_basis is None
    finally:
        off.close()


@pytest.mark.parametrize("store_name", [Stores.ROS2_HUMBLE, Stores.ROS2_JAZZY])
def test_the_binding_has_every_base_type_the_stores_use(store_name: Stores) -> None:
    store = get_typestore(store_name)

    def bases(desc: Any) -> set[str]:
        kind, rest = desc
        if kind == Nodetype.BASE:
            return {rest[0]}
        return bases(rest[0]) if kind in (Nodetype.ARRAY, Nodetype.SEQUENCE) else set()

    used = {
        name for _, fields in store.fielddefs.values() for _, desc in fields for name in bases(desc)
    }
    assert used - {"string"} <= set(_BASE)


@pytest.mark.parametrize(
    "ros_type", ["diagnostic_msgs/msg/DiagnosticArray", "rcl_interfaces/msg/ParameterEvent"]
)
def test_types_with_a_byte_field_build(ros_type: str) -> None:
    store = get_typestore(Stores.ROS2_JAZZY)
    assert build_idl_type(store, ros_type, _BUILDERS, {}) is not None


def test_an_oversize_sample_is_reported_and_its_bytes_dropped() -> None:
    class Reader:
        def take(self, N: int) -> list[Any]:
            big = _Serialized(bytes(MAX_RAW_SAMPLE_BYTES + 1))
            big.sample_info = type("Info", (), {"valid_data": True, "source_timestamp": 1})()
            return [big]

    sink: list[Any] = []
    assert _drain(Reader(), 5, True, sink) is True
    assert sink[0].raw == b""
    assert sink[0].size == MAX_RAW_SAMPLE_BYTES + 1
    assert sink[0].arrival_ns > 0
