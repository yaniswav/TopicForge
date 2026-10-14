"""Agent-friction fixes found by the blind evaluation: restarts by name, honest `domain_id`,
`fields` compact, a readable scan summary, relative event ages, and the listing size.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from mcp.types import Tool

from topicforge.adapters.common.qos_scan import scan_endpoints
from topicforge.adapters.ros2_mock import MockAdapter
from topicforge.config import Settings
from topicforge.models import EndpointInfo, ParticipantEvent, ParticipantInfo, QosProfile
from topicforge.server import build_app
from topicforge.services import Inspector
from topicforge.services.dds_views import (
    domain_note,
    qos_one_line,
    restarts_from_events,
    restarts_from_participants,
)

NS = 1_000_000_000


def _event(
    guid: str, kind: str, name: str | None, t: int = 100 * NS, domain: int = 0
) -> ParticipantEvent:
    return ParticipantEvent(
        guid=guid,
        event_type=kind,  # type: ignore[arg-type]
        vendor="cyclone",
        timestamp_ns=t,
        name=name,
        domain_id=domain,
    )


def _restart_loop_events() -> list[ParticipantEvent]:
    """nav_planner started four times (3 departures); lidar_driver once; two unnamed."""
    events = [_event("lidar", "discovered", "lidar_driver", 10 * NS)]
    for i in range(4):
        events.append(_event(f"nav{i}", "discovered", "nav_planner", (20 + 10 * i) * NS))
        if i < 3:
            events.append(_event(f"nav{i}", "lost", "nav_planner", (25 + 10 * i) * NS))
    events += [_event("anon1", "discovered", None), _event("anon2", "lost", None)]
    return events


# ---- restarts_by_name ------------------------------------------------------


def test_restarts_from_events_counts_lives_and_departures() -> None:
    table, note = restarts_from_events(_restart_loop_events())
    assert set(table) == {"nav_planner"}
    assert table["nav_planner"].lives == 4 and table["nav_planner"].departures == 3
    assert note is not None and "2 unnamed" in note


def test_a_stable_name_is_absent_and_a_single_departure_is_listed() -> None:
    events = [_event("a", "discovered", "stable"), _event("b", "lost", "gone")]
    table, note = restarts_from_events(events)
    assert "stable" not in table
    assert table["gone"].lives == 1 and table["gone"].departures == 1
    assert note is None


def test_restarts_from_participants_uses_status_left() -> None:
    def info(guid: str, name: str | None, status: str) -> ParticipantInfo:
        return ParticipantInfo(
            guid=guid,
            vendor="cyclone",
            name=name,
            domain_id=0,
            status=status,  # type: ignore[arg-type]
        )

    table, note = restarts_from_participants(
        [
            info("g1", "nav_planner", "left"),
            info("g2", "nav_planner", "left"),
            info("g3", "nav_planner", "active"),
            info("g4", "lidar_driver", "active"),
            info("g5", None, "active"),
        ]
    )
    assert table["nav_planner"].lives == 3 and table["nav_planner"].departures == 2
    assert "lidar_driver" not in table
    assert note is not None and "1 unnamed" in note


class _RestartLoop(MockAdapter):
    @property
    def effective_mode(self):  # type: ignore[no-untyped-def]
        return "live"

    def participant_events(self, domain_id: int = 0, lookback_s: int = 300):  # type: ignore[no-untyped-def]
        return _restart_loop_events()

    @property
    def observed_domain_id(self) -> int:
        return 3


def test_participant_events_carry_restarts_and_ages() -> None:
    inspector = Inspector(_RestartLoop(), clock_ns=lambda: 130 * NS)
    listing = inspector.participant_events(0, 300)
    assert listing.restarts_by_name["nav_planner"].lives == 4
    assert listing.restarts_by_name["nav_planner"].departures == 3
    assert listing.restarts_note is not None
    ages = {e.guid + e.event_type: e.age_s for e in listing.events}
    assert ages["lidardiscovered"] == 120.0
    assert ages["nav0discovered"] == 110.0 and ages["nav0lost"] == 105.0


def test_event_age_is_clamped_at_zero() -> None:
    inspector = Inspector(_RestartLoop(), clock_ns=lambda: 1 * NS)
    assert all(e.age_s == 0 for e in inspector.participant_events(0, 300).events)


def test_mock_events_have_no_age_and_an_empty_restart_table(inspector: Inspector) -> None:
    listing = inspector.participant_events(0, 300)
    assert all(e.age_s is None for e in listing.events)
    assert listing.restarts_by_name == {}
    # the Dust participant announces no name: it is excluded and said so
    assert listing.restarts_note is not None and "unnamed" in listing.restarts_note


def test_mock_participants_have_an_empty_restart_table(inspector: Inspector) -> None:
    listing = inspector.list_participants(0)
    assert listing.restarts_by_name == {}
    assert listing.restarts_note is not None and "1 unnamed participant" in listing.restarts_note


# ---- honest domain_id ------------------------------------------------------


def test_domain_note_only_for_a_different_non_default_value() -> None:
    assert domain_note(0, 3) is None  # the default cannot be told from "not given"
    assert domain_note(3, 3) is None
    assert domain_note(5, None) is None
    note = domain_note(5, 3)
    assert note is not None and "ignored" in note and "domain 3" in note
    assert "TOPICFORGE_DDS_DOMAIN_ID=5" in note


def test_every_domain_tool_says_when_the_requested_domain_was_ignored() -> None:
    inspector = Inspector(_RestartLoop())
    for note in (
        inspector.list_participants(5).note,
        inspector.participant_events(5, 300).note,
        inspector.topic_metrics("DCPSParticipant", 60, 5).note,
        inspector.list_endpoints(domain_id=5).note,
    ):
        assert note is not None and "domain_id 5 was ignored" in note
    listing = inspector.list_participants(5)
    assert listing.domain_id == 3  # the envelope carries the domain actually observed


def test_no_note_when_the_domain_is_not_contradicted() -> None:
    inspector = Inspector(_RestartLoop())
    assert inspector.list_participants(3).note is None
    assert inspector.list_participants(0).note is None
    assert inspector.topic_metrics("DCPSParticipant", 60, 3).note is None


def test_domain_param_description_leads_with_the_ignore_statement(tools: dict[str, Tool]) -> None:
    for name in ("list_participants", "participant_events", "topic_metrics", "list_endpoints"):
        desc = tools[name].input_schema["properties"]["domain_id"]["description"]
        assert desc.startswith("Ignored") and "does not switch domains" in desc


# ---- summary on the QoS scan ----------------------------------------------


def _ep(role: str, partitions: list[str], n: int) -> EndpointInfo:
    return EndpointInfo(
        guid=f"{n:08x}.00000000.00000000.00000000",
        role=role,  # type: ignore[arg-type]
        participant_guid=f"p{n}",
        participant_name=f"node_{n}",
        dds_topic="rt/x",
        type_name="T",
        qos=QosProfile(reliability="RELIABLE", durability="VOLATILE", partitions=partitions),
        is_observer=False,
    )


def test_summary_cannot_be_read_as_all_clear_when_pairs_are_not_matched() -> None:
    scan = scan_endpoints([_ep("reader", ["a"], 1), _ep("writer", ["b"], 2)])
    assert scan.reports == [] and len(scan.not_matched) == 1
    assert scan.summary is not None
    assert "0 incompatible, 0 risky" in scan.summary
    assert "1 pair will never match (1 by partition)" in scan.summary
    assert "no data flows between them" in scan.summary and "`not_matched`" in scan.summary
    assert "0 pairs will match" in scan.summary


def test_summary_of_a_clean_scan_and_of_the_mock(inspector: Inspector) -> None:
    clean = scan_endpoints([_ep("reader", [""], 1), _ep("writer", [""], 2)])
    assert (
        clean.summary
        == "0 incompatible, 0 risky QoS pair(s); 1 pair will match given the announced QoS."
    )
    mock = inspector.detect_qos_mismatches()
    assert mock.summary is not None and "incompatible" in mock.summary


# ---- list_endpoints fields -------------------------------------------------


def test_compact_endpoints_are_smaller_and_default_is_unchanged(inspector: Inspector) -> None:
    full = inspector.list_endpoints()
    compact = inspector.list_endpoints(fields="compact")
    assert full.fields == "full" and compact.fields == "compact"
    assert all(e.qos is not None and e.qos_summary is None for e in full.endpoints)
    assert all(e.qos is None and e.qos_summary for e in compact.endpoints)
    assert all(e.type_id is None and e.announced_ns is None for e in compact.endpoints)
    assert len(compact.model_dump_json()) < len(full.model_dump_json())
    # the roll-up is identical
    assert compact.by_topic == full.by_topic and compact.total == full.total


def test_compact_is_much_smaller_on_a_large_bus() -> None:
    from topicforge.adapters.common import build_endpoint_listing, endpoint_record

    records = []
    for n in range(60):
        ep = _ep("writer" if n % 2 else "reader", [""], n + 1).model_copy(
            update={"dds_topic": f"rt/topic{n // 6}", "type_id": "COMPLETE:" + "ab" * 16}
        )
        records.append(ep.model_dump())
    del endpoint_record
    listing = build_endpoint_listing(
        records, domain_id=0, mode_effective="mock", observer_guid=None, include_internal=True
    )

    class _Big(MockAdapter):
        def list_endpoints(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            return listing

    inspector = Inspector(_Big())
    full = len(inspector.list_endpoints().model_dump_json())
    compact = len(inspector.list_endpoints(fields="compact").model_dump_json())
    assert compact < 0.6 * full, (full, compact)


def test_bad_fields_value_is_rejected(inspector: Inspector) -> None:
    from topicforge.adapters.base import AdapterError

    with pytest.raises(AdapterError):
        inspector.list_endpoints(fields="tiny")


def test_qos_one_line_leaves_out_defaults() -> None:
    line = qos_one_line(
        QosProfile(
            reliability="BEST_EFFORT",
            durability="TRANSIENT_LOCAL",
            history="KEEP_LAST",
            history_depth=10,
            deadline_ns=100_000_000,
            liveliness_kind="AUTOMATIC",
            ownership_kind="SHARED",
            partitions=[""],
            latency_budget_ns=0,
        )
    )
    assert line == "BEST_EFFORT TRANSIENT_LOCAL KEEP_LAST(10) deadline=100ms"
    assert qos_one_line(None) == "qos unresolved"


# ---- listing size ----------------------------------------------------------


@pytest.fixture(scope="module")
def tools() -> dict[str, Tool]:
    app = build_app(
        Settings(mode="mock", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False)
    )
    return {t.name: t for t in asyncio.run(app.list_tools())}


def test_tool_listing_stays_small(tools: dict[str, Tool]) -> None:
    """tools/list was about 178 KB compact in 0.7.0 and truncated by some clients."""
    total = sum(
        len(json.dumps(t.model_dump(by_alias=True, exclude_none=True), separators=(",", ":")))
        for t in tools.values()
    )
    assert total < 120_000, total


def test_schemas_carry_no_generated_titles_but_keep_the_output_type_name(
    tools: dict[str, Tool],
) -> None:
    def titles(node: object) -> list[str]:
        if isinstance(node, dict):
            found = [v for k, v in node.items() if k == "title" and isinstance(v, str)]
            for k, v in node.items():
                if k != "properties":
                    found += titles(v)
                else:
                    for sub in v.values():
                        found += titles(sub)
            return found
        if isinstance(node, list):
            return [t for item in node for t in titles(item)]
        return []

    for tool in tools.values():
        assert titles(tool.input_schema) == [], tool.name
        assert titles(tool.output_schema) == [tool.output_schema["title"]], tool.name
