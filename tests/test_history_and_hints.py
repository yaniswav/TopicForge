"""History is reported only when discovery really announced it; typo hints skip ROS 2 plumbing.

Synthetic duck-typed samples only: no DDS binding required.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from topicforge.adapters.common import (
    cyclone_qos_to_profile,
    endpoint_record,
    fast_qos_to_profile,
    format_participant_key,
    scan_endpoints,
)
from topicforge.adapters.common.qos_analyzer import analyze_pair
from topicforge.adapters.common.qos_normalize import (
    HISTORY_NOT_ANNOUNCED_NOTE,
    apply_history_policy,
)
from topicforge.models import EndpointInfo, QosProfile

# ------------------------------- helpers -----------------------------------


def _policy(scoped_name: str, **attrs: Any) -> Any:
    obj = type(scoped_name, (), {})()
    for key, value in attrs.items():
        setattr(obj, key, value)
    return obj


def _qos_list(*history: Any) -> list[Any]:
    return [_policy("Reliability.Reliable"), _policy("Durability.Volatile"), *history]


def _sample(qos: list[Any], participant: int = 100) -> Any:
    return SimpleNamespace(
        key=uuid.UUID(int=1),
        participant_key=uuid.UUID(int=participant),
        topic_name="rt/scan",
        type_name="T",
        typeid=None,
        qos=qos,
        sample_info=None,
    )


def _record(qos: list[Any], vendor: str, observer: bool = False) -> dict[str, Any]:
    guid = format_participant_key(uuid.UUID(int=100))
    return endpoint_record(
        _sample(qos),
        "writer",
        {guid: "node"},
        guid if observer else None,
        vendors_by_guid={guid: vendor},
    )


# ------------------------------- history policy ----------------------------


def test_fast_dds_writer_history_is_not_reported() -> None:
    rec = _record(_qos_list(_policy("History.KeepLast", depth=10)), "fast")
    qos = rec["qos"]
    assert qos.history is None and qos.history_depth is None
    assert qos.history_note == HISTORY_NOT_ANNOUNCED_NOTE
    assert qos.reliability == "RELIABLE" and qos.durability == "VOLATILE"


@pytest.mark.parametrize("vendor", ["rti", "unknown", "dust"])
def test_other_vendors_do_not_report_history(vendor: str) -> None:
    rec = _record(_qos_list(_policy("History.KeepAll")), vendor)
    assert rec["qos"].history is None and rec["qos"].history_note


def test_cyclone_peer_keep_last_1_is_indistinguishable_from_default() -> None:
    rec = _record(_qos_list(_policy("History.KeepLast", depth=1)), "cyclone")
    assert rec["qos"].history is None and rec["qos"].history_note


def test_cyclone_peer_keep_last_10_is_reported() -> None:
    rec = _record(_qos_list(_policy("History.KeepLast", depth=10)), "cyclone")
    assert rec["qos"].history == "KEEP_LAST" and rec["qos"].history_depth == 10
    assert rec["qos"].history_note is None


def test_cyclone_peer_keep_all_is_reported() -> None:
    rec = _record(_qos_list(_policy("History.KeepAll")), "cyclone")
    assert rec["qos"].history == "KEEP_ALL"


@pytest.mark.parametrize("vendor", ["cyclone", "fast", "unknown"])
def test_observer_own_endpoint_is_authoritative(vendor: str) -> None:
    rec = _record(_qos_list(_policy("History.KeepLast", depth=1)), vendor, observer=True)
    assert rec["is_observer"] is True
    assert rec["qos"].history == "KEEP_LAST" and rec["qos"].history_depth == 1


def test_profile_without_history_is_kept() -> None:
    profile = cyclone_qos_to_profile(SimpleNamespace(qos=_qos_list()))
    assert profile is not None
    assert profile.reliability == "RELIABLE" and profile.durability == "VOLATILE"
    assert profile.history is None


def test_fast_profile_without_history_is_kept() -> None:
    kind = SimpleNamespace(kind=1)
    sample = SimpleNamespace(qos=SimpleNamespace(reliability=kind, durability=kind))
    profile = fast_qos_to_profile(
        sample,
        reliability_map={1: "RELIABLE"},
        durability_map={1: "VOLATILE"},
        history_map={},
    )
    assert profile is not None and profile.history is None


def test_apply_history_policy_passes_none_through() -> None:
    assert apply_history_policy(None, vendor="fast") is None


# ------------------------------- analyzer ----------------------------------


def _profile(history: str | None, depth: int | None = None) -> QosProfile:
    return QosProfile(
        reliability="RELIABLE", durability="VOLATILE", history=history, history_depth=depth
    )  # type: ignore[arg-type]


def test_known_history_still_gives_risky_finding() -> None:
    result = analyze_pair(_profile("KEEP_ALL"), _profile("KEEP_LAST", 5))
    assert [f.policy for f in result.risky] == ["History"]
    assert "History" not in result.unchecked


@pytest.mark.parametrize(
    "reader,writer",
    [
        (_profile("KEEP_ALL"), _profile(None)),
        (_profile(None), _profile("KEEP_LAST", 5)),
        (_profile(None), _profile(None)),
    ],
)
def test_unknown_history_is_flagged_not_a_finding(reader: QosProfile, writer: QosProfile) -> None:
    result = analyze_pair(reader, writer)
    assert result.risky == [] and result.incompatible == []
    assert result.history_unknown is True
    assert "History" not in result.unchecked


@pytest.mark.parametrize(
    "reader,writer",
    [
        (_profile("KEEP_LAST", 5), _profile(None)),
        (_profile(None), _profile("KEEP_ALL")),
        (_profile("KEEP_LAST", 5), _profile("KEEP_ALL")),
    ],
)
def test_history_that_cannot_matter_is_not_flagged(reader: QosProfile, writer: QosProfile) -> None:
    assert "History" not in analyze_pair(reader, writer).unchecked


_SEQ = iter(range(1, 10_000))


def _ep(
    role: str,
    topic: str,
    qos: QosProfile | None = None,
    announced_ns: int | None = None,
    name: str | None = None,
) -> EndpointInfo:
    n = next(_SEQ)
    return EndpointInfo(
        guid=f"{n:08x}.00000000.00000000.00000000",
        role=role,  # type: ignore[arg-type]
        participant_guid=f"p{n}",
        participant_name=name or f"node_{n}",
        topic=topic,
        type_name="pkg/T",
        qos=qos or _profile(None),
        announced_ns=announced_ns,
        is_observer=False,
        domain_id=0,
        mode_effective="live",
    )


def test_scan_with_unknown_history_has_no_report_and_one_hint() -> None:
    scan = scan_endpoints([_ep("reader", "rt/x"), _ep("writer", "rt/x")])
    assert scan.reports == [] and scan.matched_total == 1
    (hint,) = [h for h in scan.hints if "History" in h]
    assert "not carry History" in hint and "1 pair(s)" in hint
    assert all("History" not in r.unchecked for r in scan.reports)


def test_late_joiner_does_not_depend_on_history() -> None:
    writer = _ep("writer", "rt/x", announced_ns=1_000_000_000, name="pub")
    reader = _ep("reader", "rt/x", announced_ns=5_000_000_000, name="sub")
    hosts = {writer.participant_guid: "h", reader.participant_guid: "h"}
    scan = scan_endpoints([writer, reader], hostnames=hosts)
    (pair,) = scan.matched
    assert pair.late_joiner is True


# ------------------------------- typo hints --------------------------------


def _typo_hints(scan: Any) -> list[str]:
    return [h for h in scan.hints if "typo" in h]


def test_service_topics_never_give_typo_or_orphan_hints() -> None:
    eps = [
        _ep("reader", "rq/omnisim_clock/get_parametersRequest"),
        _ep("reader", "rq/omnisim_clock/set_parametersRequest"),
        _ep("writer", "rr/omnisim_clock/get_parametersReply"),
        _ep("writer", "rr/omnisim_clock/set_parametersReply"),
        _ep("reader", "rq/omnisim_clock/list_parametersRequest"),
        _ep("writer", "rr/omnisim_clock/list_parametersReply"),
    ]
    assert scan_endpoints(eps).hints == []


def test_service_names_on_opposite_sides_are_not_compared() -> None:
    eps = [
        _ep("reader", "rq/omnisim_odom/get_parametersRequest"),
        _ep("writer", "rq/omnisim_odom/set_parametersRequest"),
    ]
    assert scan_endpoints(eps).hints == []


def test_logging_parameter_events_and_discovery_topics_are_not_orphans() -> None:
    eps = [
        _ep("writer", "rt/rosout"),
        _ep("writer", "rt/parameter_events"),
        _ep("reader", "ros_discovery_info"),
    ]
    assert scan_endpoints(eps).hints == []


def test_action_topics_are_not_compared() -> None:
    base = "omnisim_simulation_interfaces/simulate_steps/_action/"
    eps = [
        _ep("writer", f"rq/{base}send_goalRequest"),
        _ep("reader", f"rq/{base}send_goalReques"),
        _ep("writer", f"rt/{base}feedback"),
        _ep("reader", f"rt/{base}feedbac"),
    ]
    assert scan_endpoints(eps).hints == []


def test_infrastructure_topic_is_not_a_typo_of_a_user_topic() -> None:
    scan = scan_endpoints([_ep("writer", "rt/rosout"), _ep("reader", "rt/rosoup")])
    assert _typo_hints(scan) == []


def test_complementary_user_topic_typo_still_fires() -> None:
    scan = scan_endpoints([_ep("writer", "rt/scan"), _ep("reader", "rt/sacn")])
    (hint,) = _typo_hints(scan)
    assert "'rt/scan'" in hint and "'rt/sacn'" in hint and "Likely a topic name typo" in hint


def test_genuine_orphan_is_still_reported() -> None:
    scan = scan_endpoints([_ep("writer", "rt/lonely_topic")])
    assert any("rt/lonely_topic" in h and "no pair to compare" in h for h in scan.hints)
