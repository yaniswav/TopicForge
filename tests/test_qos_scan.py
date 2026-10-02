"""Tests for `common.qos_scan.scan_endpoints` with fake `EndpointInfo` records.

Pins the order partition -> type -> RxO, the `not_matched` category, the
near-name orphan hints, the type id note and the checked/unchecked lists.
"""

from __future__ import annotations

from topicforge.adapters.common import scan_endpoints
from topicforge.adapters.common.qos_scan import levenshtein
from topicforge.models import EndpointInfo, QosProfile

_SEQ = iter(range(1, 10_000))


def _qos(**extra: object) -> QosProfile:
    base: dict[str, object] = {
        "reliability": "RELIABLE",
        "durability": "VOLATILE",
        "history": "KEEP_LAST",
        "history_depth": 10,
        "liveliness_kind": "AUTOMATIC",
        "ownership_kind": "SHARED",
        "latency_budget_ns": 0,
        "destination_order": "BY_RECEPTION_TIMESTAMP",
        "data_representation": ["XCDR2"],
    }
    base.update(extra)
    return QosProfile(**base)  # type: ignore[arg-type]


def _ep(
    role: str,
    topic: str = "/t",
    *,
    name: str | None = None,
    type_name: str | None = "pkg/T",
    type_id: str | None = None,
    qos: QosProfile | None = None,
    observer: bool = False,
    **qos_extra: object,
) -> EndpointInfo:
    n = next(_SEQ)
    return EndpointInfo(
        guid=f"{n:08x}.00000000.00000000.00000000",
        role=role,  # type: ignore[arg-type]
        participant_guid=f"p{n}",
        participant_name=name or f"node_{n}",
        topic=topic,
        type_name=type_name,
        type_id=type_id,
        qos=qos if qos is not None else _qos(**qos_extra),
        is_observer=observer,
        domain_id=0,
        mode_effective="live",
    )


def test_compatible_pair_is_clean() -> None:
    scan = scan_endpoints([_ep("reader"), _ep("writer")])
    assert scan.reports == [] and scan.not_matched == []
    assert scan.pairs_checked == 1 and scan.topics_scanned == 1


def test_partition_split_is_not_matched_and_skips_rxo() -> None:
    reader = _ep("reader", partitions=["a"])
    writer = _ep("writer", partitions=["b"], reliability="BEST_EFFORT")
    scan = scan_endpoints([reader, writer])
    assert scan.reports == []
    (pair,) = scan.not_matched
    assert pair.reason == "partition"
    assert "['a']" in pair.detail and "['b']" in pair.detail
    assert pair.reader_participant_name == reader.participant_name
    assert pair.writer_guid == writer.guid


def test_wildcard_writer_matches_then_rxo_applies() -> None:
    reader = _ep("reader", partitions=["robot1"])
    writer = _ep("writer", partitions=["robot*"], reliability="BEST_EFFORT")
    scan = scan_endpoints([reader, writer])
    assert scan.not_matched == []
    assert [r.incompatible_policies for r in scan.reports] == [["Reliability"]]


def test_wildcard_against_wildcard_is_not_matched() -> None:
    scan = scan_endpoints([_ep("reader", partitions=["r*"]), _ep("writer", partitions=["r*"])])
    assert [p.reason for p in scan.not_matched] == ["partition"]


def test_report_carries_names_types_and_values() -> None:
    reader = _ep("reader", name="lidar_driver", ownership_kind="EXCLUSIVE")
    writer = _ep("writer", name="nav_planner")
    (report,) = scan_endpoints([reader, writer]).reports
    assert report.reader_participant_name == "lidar_driver"
    assert report.writer_participant_name == "nav_planner"
    assert report.reader_type_name == report.writer_type_name == "pkg/T"
    assert report.severity == "incompatible"
    (detail,) = report.details
    assert (detail.policy, detail.requested, detail.offered) == ("Ownership", "EXCLUSIVE", "SHARED")


def test_liveliness_report_has_values() -> None:
    reader = _ep("reader", liveliness_kind="MANUAL_BY_TOPIC", liveliness_lease_ns=500_000_000)
    (report,) = scan_endpoints([reader, _ep("writer")]).reports
    assert report.incompatible_policies == ["Liveliness"]
    assert report.details[0].requested == "MANUAL_BY_TOPIC lease 500 ms"


def test_type_name_difference_is_not_matched() -> None:
    scan = scan_endpoints([_ep("reader", type_name="a/A"), _ep("writer", type_name="b/B")])
    (pair,) = scan.not_matched
    assert pair.reason == "type_name" and "a/A" in pair.detail and "b/B" in pair.detail
    assert scan.reports == []


def test_partition_checked_before_type() -> None:
    scan = scan_endpoints(
        [
            _ep("reader", type_name="a/A", partitions=["x"]),
            _ep("writer", type_name="b/B", partitions=["y"]),
        ]
    )
    assert [p.reason for p in scan.not_matched] == ["partition"]


def test_type_id_difference_is_a_hint_only() -> None:
    scan = scan_endpoints(
        [_ep("reader", type_id="COMPLETE:aa"), _ep("writer", type_id="COMPLETE:bb")]
    )
    assert scan.reports == [] and scan.not_matched == []
    assert any("type ids differ" in h and "cannot confirm" in h for h in scan.hints)


def test_typo_orphans_produce_a_hint() -> None:
    scan = scan_endpoints([_ep("writer", "/scan"), _ep("reader", "/scna")])
    assert scan.pairs_checked == 0
    assert any("'/scan'" in h and "'/scna'" in h and "typo" in h for h in scan.hints)


def test_distant_orphans_get_plain_orphan_hints() -> None:
    scan = scan_endpoints([_ep("writer", "/alpha"), _ep("reader", "/omega_long")])
    assert not any("typo" in h for h in scan.hints)
    assert any("'/alpha'" in h and "no reader" in h for h in scan.hints)


def test_topic_scope_limits_pairs_but_still_finds_typo_partner() -> None:
    eps = [
        _ep("writer", "/scan"),
        _ep("reader", "/scna"),
        _ep("reader", "/other"),
        _ep("writer", "/other", reliability="BEST_EFFORT"),
    ]
    scan = scan_endpoints(eps, topic="/scan")
    assert scan.topics_scanned == 1 and scan.reports == []
    assert any("typo" in h for h in scan.hints)


def test_unknown_policy_counts_as_unchecked_hint() -> None:
    bare = QosProfile(reliability="RELIABLE", durability="VOLATILE", history="KEEP_LAST")
    scan = scan_endpoints([_ep("reader", qos=bare), _ep("writer", qos=bare)])
    assert any("Ownership" in h and "could not be checked" in h for h in scan.hints)


def test_observer_and_builtin_topics_ignored() -> None:
    eps = [
        _ep("reader", observer=True, reliability="RELIABLE"),
        _ep("writer", reliability="BEST_EFFORT"),
        _ep("reader", "DCPSParticipant"),
        _ep("writer", "DCPSParticipant", reliability="BEST_EFFORT"),
    ]
    scan = scan_endpoints(eps)
    assert scan.reports == [] and scan.pairs_checked == 0 and scan.topics_scanned == 1


def test_history_only_pair_is_risky() -> None:
    (report,) = scan_endpoints([_ep("reader", history="KEEP_ALL"), _ep("writer")]).reports
    assert report.severity == "risky" and report.incompatible_policies == ["History"]


def test_envelope_lists_what_is_and_is_not_checked() -> None:
    scan = scan_endpoints([])
    for name in ("Partition", "Liveliness", "Ownership", "Reliability", "DataRepresentation"):
        assert name in scan.policies_checked
    assert any(u.startswith("Presentation") for u in scan.policies_unchecked)
    assert any("not discoverable" in u for u in scan.policies_unchecked)
    assert scan.mode_effective == "live"


def test_levenshtein() -> None:
    assert levenshtein("", "abc") == 3
    assert levenshtein("/scan", "/scna") == 2
    assert levenshtein("kitten", "sitting") == 3
    assert levenshtein("same", "same") == 0


def test_matched_lists_pairs_dds_will_connect() -> None:
    reader, writer = _ep("reader", name="sub"), _ep("writer", name="pub")
    scan = scan_endpoints([reader, writer])
    assert len(scan.matched) == 1
    m = scan.matched[0]
    assert (m.reader_guid, m.writer_guid) == (reader.guid, writer.guid)
    assert (m.reader_participant_name, m.writer_participant_name) == ("sub", "pub")
    assert m.type_name == "pkg/T"


def test_matched_excludes_incompatible_and_separated_but_keeps_risky() -> None:
    bad = scan_endpoints([_ep("reader"), _ep("writer", reliability="BEST_EFFORT")])
    assert bad.matched == [] and len(bad.reports) == 1
    split = scan_endpoints([_ep("reader", partitions=["a"]), _ep("writer", partitions=["b"])])
    assert split.matched == [] and len(split.not_matched) == 1
    risky = scan_endpoints([_ep("reader", history="KEEP_ALL"), _ep("writer")])
    assert len(risky.matched) == 1 and risky.reports[0].severity == "risky"


def test_typo_hint_compares_orphan_against_non_orphan_topics() -> None:
    eps = [_ep("reader", "battery"), _ep("writer", "battery"), _ep("reader", "batery")]
    scan = scan_endpoints(eps)
    assert any(
        "'batery'" in h and "readers but no writer" in h and "'battery'" in h and "1 edit" in h
        for h in scan.hints
    )
    assert len([h for h in scan.hints if "batery" in h]) == 1


def test_path_suffix_hint_for_namespaced_orphan() -> None:
    eps = [_ep("reader", "lidar/scan"), _ep("reader", "scan"), _ep("writer", "scan")]
    scan = scan_endpoints(eps)
    hints = [h for h in scan.hints if "lidar/scan" in h]
    assert len(hints) == 1 and "namespaced/remapped" in hints[0]
    assert not any("typo" in h for h in hints)


def test_late_joiner_note_for_volatile_writer() -> None:
    writer = _ep("writer", name="pub").model_copy(update={"announced_ns": 1_000_000_000})
    late = _ep("reader", name="sub").model_copy(update={"announced_ns": 5_000_000_000})
    scan = scan_endpoints([writer, late])
    assert any(
        "reader sub joined after writer pub" in h and "VOLATILE" in h and "by design" in h
        for h in scan.hints
    )


def test_no_late_joiner_note_when_close_or_transient_local() -> None:
    w = _ep("writer").model_copy(update={"announced_ns": 1_000_000_000})
    close = _ep("reader").model_copy(update={"announced_ns": 1_500_000_000})
    assert not any("joined after" in h for h in scan_endpoints([w, close]).hints)
    tl_w = _ep("writer", durability="TRANSIENT_LOCAL").model_copy(
        update={"announced_ns": 1_000_000_000}
    )
    late = _ep("reader").model_copy(update={"announced_ns": 9_000_000_000})
    assert not any("joined after" in h for h in scan_endpoints([tl_w, late]).hints)


def test_not_matched_pair_carries_latent_rxo_findings() -> None:
    scan = scan_endpoints(
        [
            _ep("reader", partitions=["a"]),
            _ep("writer", partitions=["b"], reliability="BEST_EFFORT"),
        ]
    )
    (pair,) = scan.not_matched
    assert [d.policy for d in pair.latent_incompatible_policies] == ["Reliability"]
    clean = scan_endpoints([_ep("reader", partitions=["a"]), _ep("writer", partitions=["b"])])
    assert clean.not_matched[0].latent_incompatible_policies == []


def test_scan_topic_filter_accepts_the_alternate_form_and_explains_no_match() -> None:
    eps = [_ep("reader", "scan"), _ep("writer", "scan")]
    alt = scan_endpoints(eps, topic="rt/scan")
    assert len(alt.matched) == 1 and any("alternate name form" in h for h in alt.hints)
    none = scan_endpoints(eps, topic="/nope")
    assert none.matched == [] and any("known topics: scan" in h for h in none.hints)
