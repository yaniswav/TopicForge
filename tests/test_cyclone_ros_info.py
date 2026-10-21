"""Real-binding tests of the `ros_discovery_info` reader (node names). Skipped without cyclonedds."""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

cyclonedds = pytest.importorskip("cyclonedds")
pytestmark = pytest.mark.requires_cyclonedds

from topicforge.adapters.dds_cyclone import CycloneDdsAdapter  # noqa: E402


@pytest.fixture(autouse=True)
def _close_adapters(monkeypatch: pytest.MonkeyPatch):
    created: list[CycloneDdsAdapter] = []
    original = CycloneDdsAdapter.__init__

    def tracking_init(self: CycloneDdsAdapter, *args: object, **kwargs: object) -> None:
        original(self, *args, **kwargs)
        created.append(self)

    monkeypatch.setattr(CycloneDdsAdapter, "__init__", tracking_init)
    yield
    for adapter in created:
        adapter.close()


# A writer process publishing a hand-encoded ParticipantEntitiesInfo. It declares the
# reader's stand-in type (a cyclonedds writer of the true struct has XTypes type
# information that the stand-in is not assignable to, which no ROS 2 RMW publishes)
# and overrides `serialize`, so the bytes on the wire are exactly what the test encodes.
_INFO_WRITER = """
import sys, time
from dataclasses import dataclass
from cyclonedds.core import Policy, Qos
from cyclonedds.domain import DomainParticipant
from cyclonedds.idl import IdlStruct
from cyclonedds.idl.types import int32
from cyclonedds.pub import DataWriter
from cyclonedds.topic import Topic
from cyclonedds.util import duration

BASE = bytes.fromhex(sys.argv[1])
TYPENAME = "rmw_dds_common::msg::dds_::ParticipantEntitiesInfo_"

@dataclass
class Info(IdlStruct, typename=TYPENAME):
    unused: int32

    def serialize(self, buffer=None, endianness=None, use_version_2=None):
        return PAYLOAD

dp = DomainParticipant(0)
raw = bytes(dp.guid.bytes)
PAYLOAD = BASE[:4] + raw[:12] + BASE[16:]
qos = Qos(Policy.Reliability.Reliable(duration(seconds=1)),
          Policy.Durability.TransientLocal, Policy.History.KeepAll)
w = DataWriter(dp, Topic(dp, "ros_discovery_info", Info, qos=qos), qos=qos)
print(raw[:12].hex(), flush=True)
end = time.time() + 15
while time.time() < end:
    w.write(Info(unused=0))
    time.sleep(1)
"""


@pytest.mark.parametrize("width", [24, 16])
def test_node_names_arrive_over_ros_discovery_info(width: int) -> None:
    from tests.test_ros_discovery import encode

    payload = encode([("/demo", "talker", 1, 1), ("/", "listener", 0, 1)], width=width)
    proc = subprocess.Popen(
        [sys.executable, "-c", _INFO_WRITER, payload.hex()], stdout=subprocess.PIPE, text=True
    )
    adapter = CycloneDdsAdapter(domain_id=0)
    try:
        assert proc.stdout is not None
        prefix = proc.stdout.readline().strip()
        assert prefix, "the writer process did not start"
        found = None
        for _ in range(150):
            found = next((p for p in adapter.list_participants() if p.node_names), None)
            if found is not None:
                break
            time.sleep(0.1)
        assert found is not None, "no participant got node names"
        assert found.node_names == ["/demo/talker", "/listener"]
        assert found.node_names_source == "ros_discovery_info"
        assert found.guid.replace(".", "").startswith(prefix)
    finally:
        proc.kill()
        proc.wait()


def test_the_raw_info_type_keeps_the_serialized_bytes() -> None:
    from topicforge.adapters.dds_cyclone.ros_info import ParticipantEntitiesInfoRaw, RawInfo

    sample = ParticipantEntitiesInfoRaw.deserialize(b"\x00\x01\x00\x00abcd")
    assert isinstance(sample, RawInfo) and sample.raw == b"\x00\x01\x00\x00abcd"
    sample.sample_info = object()  # the binding attaches this to what deserialize returns


def test_participant_pids_expose_the_announced_pid() -> None:
    adapter = CycloneDdsAdapter(domain_id=0)
    time.sleep(1.5)
    assert os.getpid() in adapter.participant_pids().values()


# Same writer, but the node's writer gid is the info writer's own GUID, so the endpoint
# shows up on the bus under the exact GUID the message claims for it.
_MARKER = bytes.fromhex("ee" * 16)
_SELF_GID_WRITER = _INFO_WRITER.replace(
    "print(raw[:12].hex(), flush=True)",
    "PAYLOAD = PAYLOAD.replace(bytes.fromhex(sys.argv[2]), bytes(w.guid.bytes))\n"
    "print(raw[:12].hex(), flush=True)",
)


@pytest.mark.parametrize("width", [24, 16])
def test_endpoints_get_the_node_that_claims_their_gid(width: int) -> None:
    from tests.test_ros_discovery import _encode_nodes

    payload = _encode_nodes([("/", "bench_robot", [], [_MARKER])], bytes(16), width)
    proc = subprocess.Popen(
        [sys.executable, "-c", _SELF_GID_WRITER, payload.hex(), _MARKER.hex()],
        stdout=subprocess.PIPE,
        text=True,
    )
    adapter = CycloneDdsAdapter(domain_id=0)
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip(), "the writer process did not start"
        found = None
        for _ in range(150):
            listing = adapter.list_endpoints(topic="ros_discovery_info", include_internal=True)
            found = next((e for e in listing.endpoints if e.node_name), None)
            if found is not None:
                break
            time.sleep(0.1)
        assert found is not None, "no endpoint got a node name"
        assert found.node_name == "/bench_robot" and found.role == "writer"
        assert listing.by_topic[0].writer_nodes == ["/bench_robot"]
        host = next(p for p in adapter.list_participants() if p.guid == found.participant_guid)
        assert host.endpoint_count is not None and host.endpoint_count >= 1
    finally:
        proc.kill()
        proc.wait()
