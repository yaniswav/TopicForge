"""Suggested fixes on QoS mismatch reports and partition splits (text only, never applied)."""

from __future__ import annotations

import asyncio
import re

import pytest

from topicforge.adapters.common import scan_endpoints
from topicforge.config import Settings
from topicforge.models import EndpointInfo, MismatchScan, QosProfile
from topicforge.server import build_app

_SEQ = iter(range(1, 100_000))

# Words a suggestion must never contain (CONTRACT.md section 3.1), matched as whole words.
FORBIDDEN_VERBS = (
    "kill",
    "pkill",
    "delete",
    "rm",
    "shutdown",
    "deactivate",
    "cleanup",
    "set",
    "publish",
    "call",
    "launch",
    "restart",
    "reset",
)
_FORBIDDEN = re.compile(r"\b(" + "|".join(FORBIDDEN_VERBS) + r")\b", re.IGNORECASE)


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


def _ep(role: str, topic: str, name: str, **qos: object) -> EndpointInfo:
    n = next(_SEQ)
    ros = "/" + topic[3:] if topic.startswith("rt/") else None
    return EndpointInfo(
        guid=f"{n:08x}.00000000.00000000.00000000",
        role=role,  # type: ignore[arg-type]
        participant_guid=f"p{n:08d}",
        participant_name=name,
        dds_topic=topic,
        ros_topic=ros,
        type_name="pkg/T",
        qos=_qos(**qos),
        is_observer=False,
    )


def _pair(topic: str, reader: dict[str, object], writer: dict[str, object]) -> MismatchScan:
    return scan_endpoints(
        [
            _ep("reader", topic, "/reader_node", **reader),
            _ep("writer", topic, "/writer_node", **writer),
        ]
    )


def _all_text(scan: MismatchScan) -> list[str]:
    fixes = [f for r in scan.reports for f in r.suggested_fixes]
    fixes += [f for p in scan.not_matched for f in p.suggested_fixes]
    return [t for f in fixes for t in (f.text, f.target, f.tradeoff, f.verify_with)]


def test_ros_reliability_gives_qos_override_for_each_side() -> None:
    scan = _pair("rt/scan", {"reliability": "RELIABLE"}, {"reliability": "BEST_EFFORT"})
    fixes = scan.reports[0].suggested_fixes
    assert [f.kind for f in fixes] == ["qos_override", "qos_override"]
    writer, reader = fixes
    assert writer.target.startswith("writer /writer_node on /scan")
    assert writer.text == (
        "/writer_node:\n  ros__parameters:\n    qos_overrides./scan.publisher.reliability: reliable"
    )
    assert reader.text == (
        "/reader_node:\n  ros__parameters:\n"
        "    qos_overrides./scan.subscription.reliability: best_effort"
    )
    assert "QosOverridingOptions" in reader.tradeoff
    assert "miss samples" in reader.tradeoff and "bandwidth" in writer.tradeoff
    assert reader.verify_with == "detect_qos_mismatches(topic='rt/scan')"


def test_plain_dds_reliability_gives_a_profile_with_both_vendors() -> None:
    scan = _pair("/dds/x", {"reliability": "RELIABLE"}, {"reliability": "BEST_EFFORT"})
    fixes = scan.reports[0].suggested_fixes
    assert [f.kind for f in fixes] == ["dds_profile", "dds_profile"]
    assert "<data_writer" in fixes[0].text and "<kind>RELIABLE</kind>" in fixes[0].text
    assert "<data_reader" in fixes[1].text and "<kind>BEST_EFFORT</kind>" in fixes[1].text
    assert "Policy.Reliability.BestEffort" in fixes[1].text
    assert fixes[0].text.startswith("<?xml")


def test_durability_writer_first_with_the_readers_value() -> None:
    scan = _pair("rt/map", {"durability": "TRANSIENT_LOCAL"}, {"durability": "VOLATILE"})
    writer, reader = scan.reports[0].suggested_fixes
    assert "publisher.durability: transient_local" in writer.text
    assert "subscription.durability: volatile" in reader.text
    assert "memory" in writer.tradeoff and "late joiners" in reader.tradeoff


def test_deadline_gives_profiles_with_the_other_sides_period() -> None:
    scan = _pair("rt/scan", {"deadline_ns": 100_000_000}, {"deadline_ns": 500_000_000})
    reader, writer = scan.reports[0].suggested_fixes
    assert [f.kind for f in (reader, writer)] == ["dds_profile", "dds_profile"]
    assert reader.target.startswith("reader")
    assert "<nanosec>500000000</nanosec>" in reader.text
    assert "<nanosec>100000000</nanosec>" in writer.text
    assert "rmw_fastrtps" in reader.tradeoff  # ROS topic: explains where the profile counts


def test_deadline_infinite_writer_relaxes_reader_to_infinity() -> None:
    scan = _pair("/d", {"deadline_ns": 100_000_000}, {"deadline_ns": None})
    reader = scan.reports[0].suggested_fixes[0]
    assert "DURATION_INFINITY" in reader.text and "duration(infinite=True)" in reader.text


def test_liveliness_targets_the_stricter_kind_and_shorter_lease() -> None:
    scan = _pair(
        "/l",
        {"liveliness_kind": "MANUAL_BY_TOPIC", "liveliness_lease_ns": 1_000_000_000},
        {"liveliness_kind": "AUTOMATIC", "liveliness_lease_ns": 5_000_000_000},
    )
    reader, writer = scan.reports[0].suggested_fixes
    assert "<kind>AUTOMATIC</kind><lease_duration><sec>5</sec>" in reader.text
    assert "<kind>MANUAL_BY_TOPIC</kind><lease_duration><sec>1</sec>" in writer.text
    assert "Policy.Liveliness.ManualByTopic" in writer.text
    assert "MANUAL" in writer.tradeoff


def test_ownership_sides_adopt_each_others_kind() -> None:
    scan = _pair("/o", {"ownership_kind": "EXCLUSIVE"}, {"ownership_kind": "SHARED"})
    reader, writer = scan.reports[0].suggested_fixes
    assert "<ownership><kind>SHARED</kind>" in reader.text
    assert "<ownership><kind>EXCLUSIVE</kind>" in writer.text
    assert "Policy.Ownership.Exclusive" in writer.text


def test_partition_split_names_the_partition_to_add() -> None:
    scan = _pair("/p", {"partitions": ["left"]}, {"partitions": ["right"]})
    assert scan.reports == []
    reader, writer = scan.not_matched[0].suggested_fixes
    assert reader.kind == writer.kind == "dds_profile"
    assert (
        "<partition><names><name>left</name><name>right</name></names></partition>" in reader.text
    )
    assert "<name>right</name><name>left</name>" in writer.text
    assert "Policy.Partition(['left', 'right'])" in reader.text
    assert "'right'" in reader.target and "isolates" in reader.tradeoff


def test_partition_split_between_wildcards_has_no_fix_but_a_hint() -> None:
    scan = _pair("/p", {"partitions": ["a*"]}, {"partitions": ["b*"]})
    assert scan.not_matched[0].suggested_fixes == []
    assert any("wildcard" in h for h in scan.hints)


def test_type_name_split_gets_no_fix() -> None:
    reader, writer = _ep("reader", "/t", "r"), _ep("writer", "/t", "w")
    writer = writer.model_copy(update={"type_name": "pkg/Other"})
    scan = scan_endpoints([reader, writer])
    assert scan.not_matched[0].reason == "type_name"
    assert scan.not_matched[0].suggested_fixes == []


def test_unsupported_policy_returns_no_fix_and_a_hint() -> None:
    scan = _pair(
        "/u",
        {"destination_order": "BY_SOURCE_TIMESTAMP"},
        {"destination_order": "BY_RECEPTION_TIMESTAMP"},
    )
    assert scan.reports[0].incompatible_policies == ["DestinationOrder"]
    assert scan.reports[0].suggested_fixes == []
    assert any("No suggested fix for DestinationOrder on /u" in h for h in scan.hints)


def test_risky_history_only_gets_no_fix() -> None:
    scan = _pair("/h", {"history": "KEEP_ALL"}, {"history": "KEEP_LAST"})
    assert scan.reports[0].severity == "risky"
    assert scan.reports[0].suggested_fixes == []


def test_mixed_supported_and_unsupported_fixes_the_supported_part() -> None:
    scan = _pair(
        "/m",
        {"reliability": "RELIABLE", "destination_order": "BY_SOURCE_TIMESTAMP"},
        {"reliability": "BEST_EFFORT", "destination_order": "BY_RECEPTION_TIMESTAMP"},
    )
    fixes = scan.reports[0].suggested_fixes
    assert len(fixes) == 2 and "destinationOrder" not in fixes[0].text
    assert any("DestinationOrder" in h for h in scan.hints)


def test_multiple_policies_share_one_fix_per_side() -> None:
    scan = _pair(
        "rt/map",
        {"reliability": "RELIABLE", "durability": "TRANSIENT_LOCAL"},
        {"reliability": "BEST_EFFORT", "durability": "VOLATILE"},
    )
    writer, reader = scan.reports[0].suggested_fixes
    assert "publisher.reliability: reliable" in writer.text
    assert "publisher.durability: transient_local" in writer.text
    assert "subscription.durability: volatile" in reader.text


def test_unnamed_participant_uses_a_placeholder_node() -> None:
    writer = _ep("writer", "rt/scan", "/", reliability="BEST_EFFORT")
    reader = _ep("reader", "rt/scan", "/", reliability="RELIABLE")
    fixes = scan_endpoints([reader, writer]).reports[0].suggested_fixes
    assert fixes[0].text.startswith("<node_name>:")


def _everything() -> MismatchScan:
    cases: list[tuple[str, dict[str, object], dict[str, object]]] = [
        ("rt/a", {"reliability": "RELIABLE"}, {"reliability": "BEST_EFFORT"}),
        ("/b", {"durability": "TRANSIENT_LOCAL"}, {"durability": "VOLATILE"}),
        ("rt/c", {"deadline_ns": 1_000_000}, {"deadline_ns": None}),
        ("/d", {"liveliness_kind": "MANUAL_BY_TOPIC"}, {"liveliness_kind": "AUTOMATIC"}),
        ("/e", {"ownership_kind": "EXCLUSIVE"}, {"ownership_kind": "SHARED"}),
        ("/f", {"latency_budget_ns": 1_000_000}, {"latency_budget_ns": 9_000_000}),
        ("/g", {"partitions": ["x"]}, {"partitions": ["y"]}),
    ]
    eps: list[EndpointInfo] = []
    for topic, r, w in cases:
        eps += [_ep("reader", topic, "/r", **r), _ep("writer", topic, "/w", **w)]
    return scan_endpoints(eps)


def test_no_forbidden_verb_in_any_generated_text() -> None:
    scan = _everything()
    texts = _all_text(scan)
    assert len(texts) >= 7 * 2 * 4
    for text in texts:
        assert _FORBIDDEN.search(text) is None, (_FORBIDDEN.search(text), text)


def test_forbidden_regex_catches_what_it_should() -> None:
    for word in FORBIDDEN_VERBS:
        assert _FORBIDDEN.search(f"please {word} the node")
    assert _FORBIDDEN.search("qos_overrides./scan.publisher.reliability: reliable") is None


def test_twenty_identical_mismatches_share_one_group_of_fixes() -> None:
    eps: list[EndpointInfo] = []
    for i in range(20):
        eps.append(_ep("reader", "rt/scan", f"/reader_{i}", reliability="RELIABLE"))
    eps.append(_ep("writer", "rt/scan", "/driver", reliability="BEST_EFFORT"))
    scan = scan_endpoints(eps)
    assert scan.reports_total == 20
    with_fixes = [r for r in scan.reports if r.suggested_fixes]
    assert len(with_fixes) == 1 and len(with_fixes[0].suggested_fixes) == 2
    reader_fix = with_fixes[0].suggested_fixes[1]
    assert "20 readers" in reader_fix.target and "and 17 more" in reader_fix.target
    assert reader_fix.text.count("ros__parameters") == 3  # capped at three nodes
    assert any("19 other report(s)" in h for h in scan.hints)
    assert sum(len(f.text) for f in with_fixes[0].suggested_fixes) < 1500


def test_distinct_topics_each_keep_their_own_fixes() -> None:
    eps = []
    for topic in ("rt/a", "rt/b"):
        eps += [
            _ep("reader", topic, "/r", reliability="RELIABLE"),
            _ep("writer", topic, "/w", reliability="BEST_EFFORT"),
        ]
    scan = scan_endpoints(eps)
    assert all(len(r.suggested_fixes) == 2 for r in scan.reports)


def test_mcp_layer_mock_scenario_carries_suggested_fixes() -> None:
    app = build_app(
        Settings(mode="mock", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False)
    )
    result = asyncio.run(app.call_tool("detect_qos_mismatches", {}))
    payload = result.structured_content
    assert payload is not None
    report = payload["reports"][0]
    assert report["incompatible_policies"] == ["Reliability"]
    kinds = {f["kind"] for f in report["suggested_fixes"]}
    assert kinds == {"dds_profile"} and len(report["suggested_fixes"]) == 2
    assert all(
        f["verify_with"].startswith("detect_qos_mismatches") for f in report["suggested_fixes"]
    )


@pytest.mark.parametrize("field", ["kind", "target", "text", "tradeoff", "verify_with"])
def test_every_fix_fills_every_field(field: str) -> None:
    for report in _everything().reports:
        for fix in report.suggested_fixes:
            assert getattr(fix, field)
