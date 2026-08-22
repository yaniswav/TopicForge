"""Tests for `common.qos_endpoints.detect_mismatches_across_endpoints`.

The endpoint-pairing logic extracted from both DDS adapters (Lot 5): tested
in isolation with synthetic endpoint objects, and once through the real
Cyclone helpers to pin the exact call shape the adapter makes. No binding.
"""

from __future__ import annotations

from topicforge.adapters.common import detect_mismatches_across_endpoints
from topicforge.adapters.common.qos_normalize import cyclone_qos_to_profile
from topicforge.models import QosProfile


def _profile(
    *,
    reliability: str = "RELIABLE",
    durability: str = "VOLATILE",
    history: str = "KEEP_LAST",
) -> QosProfile:
    return QosProfile(
        reliability=reliability,  # type: ignore[arg-type]
        durability=durability,  # type: ignore[arg-type]
        history=history,  # type: ignore[arg-type]
        history_depth=10,
    )


class _Endpoint:
    """Minimal duck-typed discovery endpoint."""

    def __init__(self, topic: str | None, guid: bytes | None, profile: QosProfile | None) -> None:
        self._topic = topic
        self._guid = guid
        self._profile = profile


def _topic(e: _Endpoint) -> str | None:
    return e._topic


def _guid(e: _Endpoint) -> bytes | None:
    return e._guid


def _qos(e: _Endpoint) -> QosProfile | None:
    return e._profile


def _detect(subs: list[_Endpoint], pubs: list[_Endpoint], topic: str | None = None):
    return detect_mismatches_across_endpoints(
        subs=subs,
        pubs=pubs,
        topic=topic,
        qos_to_profile=_qos,
        extract_topic_name=_topic,
        extract_guid=_guid,
    )


def test_reliable_reader_best_effort_writer_reported() -> None:
    reader = _Endpoint("/t", b"\x01" * 16, _profile(reliability="RELIABLE"))
    writer = _Endpoint("/t", b"\x02" * 16, _profile(reliability="BEST_EFFORT"))
    reports = _detect([reader], [writer])
    assert len(reports) == 1
    r = reports[0]
    assert r.topic == "/t"
    assert "Reliability" in r.incompatible_policies
    assert r.severity == "incompatible"
    assert r.reader_guid and r.writer_guid


def test_compatible_pair_yields_no_report() -> None:
    reader = _Endpoint("/t", b"\x01" * 16, _profile())
    writer = _Endpoint("/t", b"\x02" * 16, _profile())
    assert _detect([reader], [writer]) == []


def test_topic_scoping_filters_other_topics() -> None:
    subs = [
        _Endpoint("/a", b"\x01" * 16, _profile(reliability="RELIABLE")),
        _Endpoint("/b", b"\x03" * 16, _profile(reliability="RELIABLE")),
    ]
    pubs = [
        _Endpoint("/a", b"\x02" * 16, _profile(reliability="BEST_EFFORT")),
        _Endpoint("/b", b"\x04" * 16, _profile(reliability="BEST_EFFORT")),
    ]
    reports = _detect(subs, pubs, topic="/a")
    assert {r.topic for r in reports} == {"/a"}


def test_endpoint_with_unresolvable_topic_skipped() -> None:
    reader = _Endpoint(None, b"\x01" * 16, _profile(reliability="RELIABLE"))
    writer = _Endpoint("/t", b"\x02" * 16, _profile(reliability="BEST_EFFORT"))
    # reader has no topic -> no pairing possible.
    assert _detect([reader], [writer]) == []


def test_endpoint_with_unresolvable_qos_skipped() -> None:
    reader = _Endpoint("/t", b"\x01" * 16, None)  # qos_to_profile -> None
    writer = _Endpoint("/t", b"\x02" * 16, _profile(reliability="BEST_EFFORT"))
    assert _detect([reader], [writer]) == []


def test_multiple_readers_and_writers_cartesian() -> None:
    subs = [
        _Endpoint("/t", b"\x01" * 16, _profile(reliability="RELIABLE")),
        _Endpoint("/t", b"\x03" * 16, _profile(reliability="RELIABLE")),
    ]
    pubs = [
        _Endpoint("/t", b"\x02" * 16, _profile(reliability="BEST_EFFORT")),
        _Endpoint("/t", b"\x04" * 16, _profile(reliability="BEST_EFFORT")),
    ]
    # 2 readers by 2 writers, all incompatible -> 4 reports.
    assert len(_detect(subs, pubs)) == 4


def test_end_to_end_with_real_cyclone_helpers() -> None:
    # Pin the exact call shape the Cyclone adapter makes: cyclone_qos_to_profile
    # + a topic_name attribute + a bytes `key`.
    class Reliable:
        pass

    class BestEffort:
        pass

    class Volatile:
        pass

    class KeepLast:
        depth = 10

    class _CycEndpoint:
        def __init__(self, topic: str, key: bytes, reliability_cls: type) -> None:
            self.topic_name = topic
            self.key = key
            self.qos = [reliability_cls(), Volatile(), KeepLast()]

    def _tname(e):
        return getattr(e, "topic_name", None)

    def _k(e):
        return getattr(e, "key", None)

    reader = _CycEndpoint("/scan", b"\x01" * 16, Reliable)
    writer = _CycEndpoint("/scan", b"\x02" * 16, BestEffort)
    reports = detect_mismatches_across_endpoints(
        subs=[reader],
        pubs=[writer],
        topic=None,
        qos_to_profile=cyclone_qos_to_profile,
        extract_topic_name=_tname,
        extract_guid=_k,
    )
    assert len(reports) == 1
    assert "Reliability" in reports[0].incompatible_policies
