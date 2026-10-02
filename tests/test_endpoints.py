"""Tests for `list_endpoints`: the pure normalizer, the QoS extensions, the mock.

Synthetic duck-typed samples only: no DDS binding required. Policy classes are
named with their scope ("Liveliness.ManualByTopic"), as cyclonedds 11.0.1 does.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from topicforge.adapters.base import AdapterError
from topicforge.adapters.common import (
    MAX_LISTED_ENDPOINTS,
    build_endpoint_listing,
    builtin_payload,
    cyclone_qos_to_profile,
    duration_to_ns,
    endpoint_record,
    format_participant_key,
    listing_from_samples,
)
from topicforge.adapters.ros2_mock import MockAdapter
from topicforge.config import Settings
from topicforge.models import EndpointListing
from topicforge.server import build_app
from topicforge.services import Inspector

INF = 9_223_372_036_854_775_807


def _policy(scoped_name: str, **attrs: Any) -> Any:
    """A fake binding policy whose class name is scoped, like cyclonedds 11.0.1."""
    obj = type(scoped_name, (), {})()
    for key, value in attrs.items():
        setattr(obj, key, value)
    return obj


def _core() -> list[Any]:
    return [
        _policy("Reliability.Reliable"),
        _policy("Durability.Volatile"),
        _policy("History.KeepLast", depth=5),
    ]


def _endpoint_sample(
    *,
    key: int = 1,
    participant: int = 100,
    topic: str = "/t",
    type_name: str = "T",
    qos: list[Any] | None = None,
    ts: int | None = 1_700_000_000_000_000_000,
) -> Any:
    return SimpleNamespace(
        key=uuid.UUID(int=key),
        participant_key=uuid.UUID(int=participant),
        topic_name=topic,
        type_name=type_name,
        typeid=None,
        qos=_core() if qos is None else qos,
        sample_info=SimpleNamespace(source_timestamp=ts) if ts is not None else None,
    )


def _pguid(n: int) -> str:
    return format_participant_key(uuid.UUID(int=n))


# ------------------------------- QoS extensions ----------------------------


def test_extended_policies_with_scoped_class_names() -> None:
    qos = [
        *_core(),
        _policy("Liveliness.ManualByTopic", lease_duration=500000000),
        _policy("Ownership.Exclusive"),
        _policy("OwnershipStrength", strength=10),
        _policy("Partition", partitions=("left", "right")),
        _policy("LatencyBudget", budget=1000),
        _policy("DestinationOrder.BySourceTimestamp"),
        _policy(
            "DataRepresentation", use_cdrv0_representation=True, use_xcdrv2_representation=True
        ),
        _policy("Deadline", deadline=250000000),
    ]
    profile = cyclone_qos_to_profile(SimpleNamespace(qos=qos))
    assert profile is not None
    assert profile.liveliness_kind == "MANUAL_BY_TOPIC"
    assert profile.liveliness_lease_ns == 500_000_000
    assert profile.ownership_kind == "EXCLUSIVE"
    assert profile.ownership_strength == 10
    assert profile.partitions == ["left", "right"]
    assert profile.latency_budget_ns == 1_000
    assert profile.destination_order == "BY_SOURCE_TIMESTAMP"
    assert profile.data_representation == ["XCDR1", "XCDR2"]
    assert profile.deadline_ns == 250_000_000


@pytest.mark.parametrize(
    "scoped,expected",
    [
        ("Liveliness.Automatic", "AUTOMATIC"),
        ("Liveliness.ManualByParticipant", "MANUAL_BY_PARTICIPANT"),
        ("Liveliness.ManualByTopic", "MANUAL_BY_TOPIC"),
    ],
)
def test_liveliness_kinds(scoped: str, expected: str) -> None:
    qos = [*_core(), _policy(scoped, lease_duration=1000)]
    profile = cyclone_qos_to_profile(SimpleNamespace(qos=qos))
    assert profile is not None and profile.liveliness_kind == expected


def test_infinite_durations_become_none() -> None:
    qos = [
        *_core(),
        _policy("Deadline", deadline=INF),
        _policy("Liveliness.Automatic", lease_duration=INF),
        _policy("LatencyBudget", budget=INF),
    ]
    profile = cyclone_qos_to_profile(SimpleNamespace(qos=qos))
    assert profile is not None
    assert profile.deadline_ns is None
    assert profile.liveliness_kind == "AUTOMATIC"
    assert profile.liveliness_lease_ns is None
    assert profile.latency_budget_ns is None


def test_absent_policies_stay_none_and_empty_partition_is_kept() -> None:
    plain = cyclone_qos_to_profile(SimpleNamespace(qos=_core()))
    assert plain is not None
    assert plain.liveliness_kind is None and plain.partitions is None
    assert plain.ownership_kind is None and plain.data_representation is None
    qos = [*_core(), _policy("Partition", partitions=())]
    empty = cyclone_qos_to_profile(SimpleNamespace(qos=qos))
    assert empty is not None and empty.partitions == []


def test_duration_to_ns() -> None:
    assert duration_to_ns(5) == 5
    assert duration_to_ns(INF) is None
    assert duration_to_ns(None) is None
    assert duration_to_ns(-1) is None
    assert duration_to_ns(SimpleNamespace(to_nanoseconds=lambda: 7)) == 7


# ------------------------------- endpoint_record ---------------------------


def test_endpoint_record_fields() -> None:
    sample = _endpoint_sample(key=7, participant=100)
    rec = endpoint_record(sample, "writer", {_pguid(100): "lidar"}, _pguid(999))
    assert rec["role"] == "writer"
    assert rec["participant_guid"] == _pguid(100)
    assert rec["participant_name"] == "lidar"
    assert rec["topic"] == "/t" and rec["type_name"] == "T"
    assert rec["type_id"] is None
    assert rec["qos"].reliability == "RELIABLE"
    assert rec["announced_ns"] == 1_700_000_000_000_000_000
    assert rec["is_observer"] is False
    assert rec["guid"].count(".") == 3


def test_endpoint_record_observer_unnamed_and_no_timestamp() -> None:
    sample = _endpoint_sample(participant=999, ts=None)
    rec = endpoint_record(sample, "reader", {}, _pguid(999))
    assert rec["is_observer"] is True
    assert rec["participant_name"] is None
    assert rec["announced_ns"] is None


def test_endpoint_record_type_id_and_missing_qos() -> None:
    sample = _endpoint_sample(qos=[])
    sample.typeid = SimpleNamespace(serialize=lambda: b"\x01\x02\xff")
    rec = endpoint_record(sample, "reader", {}, None)
    assert rec["type_id"] == "COMPLETE:0102ff"
    assert rec["qos"] is None
    assert rec["is_observer"] is False


# ------------------------------- listing builder ---------------------------


def _records() -> list[dict[str, Any]]:
    names: dict[str, str | None] = {_pguid(100): "pub", _pguid(200): "sub"}
    obs = _pguid(999)
    return [
        endpoint_record(_endpoint_sample(key=1, participant=100, topic="/a"), "writer", names, obs),
        endpoint_record(_endpoint_sample(key=2, participant=200, topic="/a"), "reader", names, obs),
        endpoint_record(_endpoint_sample(key=3, participant=100, topic="/b"), "writer", names, obs),
        endpoint_record(_endpoint_sample(key=4, participant=200, topic="/c"), "reader", names, obs),
        endpoint_record(_endpoint_sample(key=5, participant=999, topic="/c"), "writer", names, obs),
    ]


def _listing(**kw: Any) -> EndpointListing:
    return build_endpoint_listing(
        _records(), domain_id=3, mode_effective="live", observer_guid=_pguid(999), **kw
    )


def test_listing_excludes_observer_by_default_and_flags_orphans() -> None:
    listing = _listing()
    assert listing.total_discovered == 5 and listing.returned == 4
    assert all(not e.is_observer for e in listing.endpoints)
    orphans = {t.topic: t.orphan for t in listing.by_topic}
    assert orphans == {"/a": None, "/b": "no_reader", "/c": "no_writer"}


def test_listing_include_observer_removes_orphan() -> None:
    listing = _listing(include_observer=True)
    assert listing.returned == 5
    assert {t.topic: t.orphan for t in listing.by_topic}["/c"] is None


def test_listing_filters() -> None:
    assert [e.topic for e in _listing(topic="/b").endpoints] == ["/b"]
    only = _listing(participant_guid=_pguid(200).upper()).endpoints
    assert {e.participant_name for e in only} == {"sub"}


def test_listing_partitions_union_and_default() -> None:
    qos = [*_core(), _policy("Partition", partitions=("x",))]
    recs = [
        endpoint_record(_endpoint_sample(key=1, topic="/p", qos=qos), "writer", {}, None),
        endpoint_record(_endpoint_sample(key=2, topic="/p"), "reader", {}, None),
    ]
    listing = build_endpoint_listing(recs, domain_id=0, mode_effective="live", observer_guid=None)
    assert listing.by_topic[0].partitions == ["", "x"]
    assert listing.by_topic[0].type_names == ["T"]


def test_listing_truncates() -> None:
    recs = [
        endpoint_record(_endpoint_sample(key=i + 1, topic=f"/t{i}"), "writer", {}, None)
        for i in range(MAX_LISTED_ENDPOINTS + 3)
    ]
    listing = build_endpoint_listing(recs, domain_id=0, mode_effective="live", observer_guid=None)
    assert listing.truncated is True
    assert listing.returned == MAX_LISTED_ENDPOINTS
    assert len(listing.by_topic) == MAX_LISTED_ENDPOINTS + 3


# ------------------------------- mock + service + tool ---------------------


def test_mock_listing_is_coherent_with_other_mock_tools() -> None:
    adapter = MockAdapter()
    listing = adapter.list_endpoints()
    assert listing.mode_effective == "mock" and listing.returned == 7
    participants = {p.guid: p.name for p in adapter.list_participants()}
    for ep in listing.endpoints:
        assert ep.participant_guid in participants
        assert ep.participant_name == participants[ep.participant_guid]
    by_topic = {t.topic: t for t in listing.by_topic}
    assert by_topic["/dds/ddsforge/opaque"].orphan == "no_reader"
    assert by_topic["/dds/qos_mismatch"].orphan is None
    scan = adapter.detect_qos_mismatches("/dds/qos_mismatch")
    mismatch = scan.reports[0]
    readers = [e for e in listing.endpoints if e.topic == mismatch.topic and e.role == "reader"]
    assert readers[0].guid == mismatch.reader_guid
    assert readers[0].participant_guid == mismatch.reader_participant_guid
    assert readers[0].participant_name == mismatch.reader_participant_name


def test_mock_listing_filters_and_dust_qos() -> None:
    adapter = MockAdapter()
    one = adapter.list_endpoints(topic="/dds/ddsforge/opaque")
    assert one.returned == 1 and one.total_discovered == 7
    qos = one.endpoints[0].qos
    assert qos is not None
    assert (qos.liveliness_kind, qos.ownership_strength, qos.partitions) == (
        "MANUAL_BY_TOPIC",
        10,
        ["left"],
    )
    assert adapter.list_endpoints(topic="/nope").returned == 0


def test_inspector_validates() -> None:
    inspector = Inspector(MockAdapter())
    with pytest.raises(AdapterError):
        inspector.list_endpoints(topic="bad topic!")
    with pytest.raises(AdapterError):
        inspector.list_endpoints(participant_guid="  ")
    with pytest.raises(AdapterError):
        inspector.list_endpoints(domain_id=999)
    assert inspector.list_endpoints().returned == 7


def test_tool_call_returns_json_serializable_listing() -> None:
    app = build_app(
        Settings(mode="mock", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False)
    )
    result = asyncio.run(app.call_tool("list_endpoints", {"topic": "/dds/qos_mismatch"}))
    blocks = result[0] if isinstance(result, tuple) else result
    payload = json.loads(blocks[0].text)
    assert payload["returned"] == 2
    assert payload["endpoints"][0]["qos"]["reliability"] in {"RELIABLE", "BEST_EFFORT"}


def test_activity_is_reserved_and_explained() -> None:
    ep = MockAdapter().list_endpoints().endpoints[0]
    assert ep.activity is None
    assert "not observed" in ep.activity_note


def test_listing_from_samples_joins_names() -> None:
    part = SimpleNamespace(key=uuid.UUID(int=100), qos=[_policy("EntityName", name="lidar")])
    listing = listing_from_samples(
        [part],
        [_endpoint_sample(key=1, participant=100)],
        [_endpoint_sample(key=2, participant=100)],
        domain_id=0,
        mode_effective="live",
        observer_guid=None,
    )
    assert {e.participant_name for e in listing.endpoints} == {"lidar"}


def test_builtin_payload_drops_raw_text_when_structured() -> None:
    sample = _endpoint_sample(key=1, participant=100)
    payload = builtin_payload("DCPSPublication", sample, {}, None)
    assert "_raw_text" not in payload
    assert payload["role"] == "writer" and payload["topic_name"] == "/t"
    odd = builtin_payload("DCPSPublication", SimpleNamespace(), {}, None)
    assert len(str(odd["_raw_text"])) <= 320
