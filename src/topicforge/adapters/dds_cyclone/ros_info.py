"""Cyclone reader for the `ros_discovery_info` topic: raw bytes, decoded elsewhere.

`ParticipantEntitiesInfo` has no fixed shape across ROS 2 distros (the gid is
24 bytes on Humble, 16 later), and Cyclone's matching can reject a reader whose
declared type differs from the writer's. So the reader declares a stand-in
struct and never lets the binding decode: `deserialize` keeps the serialized
bytes, which `common.ros_discovery` decodes by hand. The binding sets
`sample_info` on whatever `deserialize` returns, so the bytes travel in a small
object rather than as bare `bytes`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cyclonedds.core import Policy, Qos
from cyclonedds.idl import IdlStruct
from cyclonedds.idl.types import int32
from cyclonedds.sub import DataReader
from cyclonedds.topic import Topic
from cyclonedds.util import duration

from topicforge.adapters.common.ros_discovery import ROS_DISCOVERY_TOPIC, ROS_DISCOVERY_TYPE


class RawInfo:
    """The serialized payload of one `ros_discovery_info` sample (header included)."""

    def __init__(self, raw: bytes = b"") -> None:
        self.raw = raw


@dataclass
class ParticipantEntitiesInfoRaw(IdlStruct, typename=ROS_DISCOVERY_TYPE):  # type: ignore[call-arg]
    """Stand-in for `ParticipantEntitiesInfo`: keyless, deserializes to raw bytes."""

    unused: int32

    @classmethod
    def deserialize(cls, data: bytes, has_header: bool = True, use_version_2: Any = None) -> Any:
        return RawInfo(bytes(data))

    @classmethod
    def deserialize_key(
        cls, data: bytes, has_header: bool = True, use_version_2: Any = None
    ) -> Any:
        return RawInfo(b"")


def ros_info_qos() -> Qos:
    """QoS of ROS 2's participant-info publishers: reliable, transient local, keep all."""
    return Qos(
        Policy.Reliability.Reliable(duration(seconds=1)),
        Policy.Durability.TransientLocal,
        Policy.History.KeepAll,
    )


def make_ros_info_reader(dp: Any) -> Any:
    """A reader of `ros_discovery_info` on participant `dp` (keyless, tracker thread only)."""
    qos = ros_info_qos()
    topic = Topic(dp, ROS_DISCOVERY_TOPIC, ParticipantEntitiesInfoRaw, qos=qos)
    return DataReader(dp, topic, qos=qos)


def take_raw(reader: Any, limit: int) -> list[bytes]:
    """Take up to `limit` samples; the serialized payloads of the valid ones."""
    return [s.raw for s in reader.take(N=limit) if isinstance(s, RawInfo)]
