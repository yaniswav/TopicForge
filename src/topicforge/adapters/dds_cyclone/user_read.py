"""Bounded read of one ROS 2 user topic, on a short-lived participant of its own.

A participant per read because a Fast DDS writer keeps deleted readers matched for as long as
their participant lives (21 subscribers after 20 reads on one participant, 0 with one per read,
on both RMWs). It is registered as TopicForge's own under the binding lock, before a tracker
pass can take its announcement, so it never reaches the lifecycle, the endpoint listings or the
restart counts. It creates one reader and no writer of its own.

The struct is the full IDL shape of the message, which is what matched on both RMWs; only its
`deserialize` is replaced, so public `take()` hands back the serialized bytes for
`common.ros_decode` to decode. No wait set: holding the binding lock through a wait would
starve the tracker, so the loop polls in short slices.
"""

from __future__ import annotations

import gc
import threading
import time
from collections.abc import Callable
from typing import Any

import cyclonedds.idl.annotations as annotations
import cyclonedds.idl.types as idl_types
from cyclonedds.core import Policy, Qos
from cyclonedds.domain import DomainParticipant
from cyclonedds.idl import make_idl_struct
from cyclonedds.sub import DataReader
from cyclonedds.topic import Topic
from cyclonedds.util import duration

from topicforge.adapters.common.dds_helpers import READER_PARTICIPANT_NAME
from topicforge.adapters.common.endpoints import format_participant_key
from topicforge.adapters.common.ros_decode import (
    MAX_RAW_SAMPLE_BYTES,
    DecodePlan,
    IdlBuilders,
    RawRead,
    ReadOutcome,
    build_idl_type,
)

_POLL_S = 0.02
_RATE_HISTORY = 256

_BASE: dict[str, Any] = {
    "bool": bool,
    "byte": idl_types.byte,
    "int8": idl_types.int8,
    "uint8": idl_types.uint8,
    "int16": idl_types.int16,
    "uint16": idl_types.uint16,
    "int32": idl_types.int32,
    "uint32": idl_types.uint32,
    "int64": idl_types.int64,
    "uint64": idl_types.uint64,
    "float32": idl_types.float32,
    "float64": idl_types.float64,
    "char": idl_types.uint8,
}

_types: dict[Any, Any] = {}


class _Serialized:
    """What `take()` returns for one sample; the binding sets `sample_info` on it."""

    def __init__(self, raw: bytes = b"") -> None:
        self.raw = raw


def _keep_serialized(
    cls: Any, data: bytes, has_header: bool = True, use_version_2: Any = None
) -> Any:
    return _Serialized(bytes(data))


def _keep_nothing(cls: Any, data: bytes, has_header: bool = True, use_version_2: Any = None) -> Any:
    return _Serialized()


def _struct(class_name: str, typename: str, fields: dict[str, Any]) -> Any:
    """A final IDL struct whose samples deserialize to their serialized bytes."""
    cls = annotations.final(make_idl_struct(class_name, typename, fields))
    cls.deserialize = classmethod(_keep_serialized)
    cls.deserialize_key = classmethod(_keep_nothing)
    return cls


_BUILDERS = IdlBuilders(
    base=_BASE,
    string=str,
    bounded_string=lambda bound: idl_types.bounded_str[bound],
    array=lambda element, size: idl_types.array[element, size],
    sequence=lambda element, size: (
        idl_types.sequence[element, size] if size else idl_types.sequence[element]
    ),
    struct=_struct,
)


def _reader_qos(latched: bool, depth: int) -> Qos:
    """Best effort and volatile match any writer but get no history; latched writers give it."""
    if latched:
        return Qos(
            Policy.Reliability.Reliable(duration(seconds=1)),
            Policy.Durability.TransientLocal,
            Policy.History.KeepLast(depth),
        )
    return Qos(
        Policy.Reliability.BestEffort,
        Policy.Durability.Volatile,
        Policy.History.KeepLast(depth),
    )


def _drain(reader: Any, room: int, keep_payload: bool, sink: list[RawRead]) -> bool:
    """Move what the reader holds, up to `room` samples, into `sink`; whether one is oversize."""
    now_ns = time.time_ns()
    arrival_ns = time.monotonic_ns()
    oversize = False
    for sample in reader.take(N=room):
        info = getattr(sample, "sample_info", None)
        if not isinstance(sample, _Serialized) or info is None or not info.valid_data:
            continue
        size = len(sample.raw)
        oversize = oversize or size > MAX_RAW_SAMPLE_BYTES
        fits = keep_payload and size <= MAX_RAW_SAMPLE_BYTES
        raw = sample.raw if fits else b""
        sink.append(RawRead(raw, info.source_timestamp, now_ns, size, arrival_ns))
    return oversize


def read_user_topic(
    domain_id: int,
    plan: DecodePlan,
    *,
    max_samples: int,
    budget_s: float,
    lock: threading.RLock,
    own: Callable[[str], None],
    keep_payload: bool = True,
    volatile: bool = False,
) -> ReadOutcome:
    """At most `max_samples` samples within `budget_s`, then every entity is deleted.

    The first sample over `MAX_RAW_SAMPLE_BYTES` ends the read: such a topic costs the writer.

    `own` receives the participant's guid while `lock` is held. `volatile` refuses a latched
    writer's history, which would read as a burst of old samples in a rate.
    """
    started = time.monotonic()
    sink: list[RawRead] = []
    latched = plan.latched and not volatile
    depth = max_samples if latched else max(min(max_samples, _RATE_HISTORY), 1)
    participant = topic = reader = None
    try:
        with lock:
            data_type = build_idl_type(plan.store, plan.ros_type, _BUILDERS, _types)
            qos = _reader_qos(latched, depth)
            participant = DomainParticipant(
                domain_id, qos=Qos(Policy.EntityName(READER_PARTICIPANT_NAME))
            )
            own(format_participant_key(participant.guid))
            topic = Topic(participant, plan.dds_topic, data_type, qos=qos)
            reader = DataReader(participant, topic, qos=qos)
        oversize = False
        while not oversize and len(sink) < max_samples and time.monotonic() - started < budget_s:
            time.sleep(_POLL_S)
            with lock:
                oversize = _drain(reader, max_samples - len(sink), keep_payload, sink)
        with lock:
            matched = reader.get_subscription_matched_status().total_count
            incompatible = reader.get_requested_incompatible_qos_status().total_count
        return ReadOutcome(sink[:max_samples], matched, incompatible, time.monotonic() - started)
    finally:
        with lock:
            del reader, topic, participant
            gc.collect()
