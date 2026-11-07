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

from topicforge.adapters.base import AdapterError
from topicforge.adapters.dds_cyclone import CycloneDdsAdapter

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
    assert metrics.frequency_basis == "source_timestamps"
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
