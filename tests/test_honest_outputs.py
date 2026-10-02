"""Honest outputs: no internal history in tool text, explicit notes and statuses.

Covers the user-topic result, the topic_metrics status and declared rate, the
participant `vendor_source` / `is_observer`, the health_check flags, the DDS-only
error pointer and the bounded warm-up wait.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import pytest

from topicforge.adapters.base import AdapterError
from topicforge.adapters.common import (
    DDS_ONLY_ERROR_MSG,
    USER_TOPIC_NOTE,
    DiscoveryCaches,
    DiscoveryTracker,
    LifecycleBuffer,
    declared_hz_from_endpoints,
    metrics_status,
    user_topic_result,
)
from topicforge.adapters.ros2_mock import MockAdapter
from topicforge.config import Settings
from topicforge.models import EndpointInfo, QosProfile
from topicforge.server import build_app
from topicforge.services import HealthService, Inspector

_HISTORY = re.compile(
    r"v0\.\d|Phase \d|\bMVP\b|ceiling|\b9th\b|\b11th\b|Lot \d|[Aa]udit|Added in|20\d\d-\d\d-\d\d"
)


def _app() -> Any:
    return build_app(
        Settings(mode="mock", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False)
    )


def _strings(node: Any) -> list[str]:
    """Every string value in a nested JSON-like structure."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [t for v in node.values() for t in _strings(v)]
    if isinstance(node, list):
        return [t for v in node for t in _strings(v)]
    return []


def test_no_tool_or_parameter_description_carries_internal_history() -> None:
    tools = asyncio.run(_app().list_tools())
    assert len(tools) == 12
    for tool in tools:
        texts = [tool.description or "", *_strings(tool.inputSchema)]
        for text in texts:
            assert not _HISTORY.search(text), (tool.name, _HISTORY.search(text).group(0))


def test_ros_tools_say_ros2_only_and_point_to_list_endpoints() -> None:
    tools = {t.name: t for t in asyncio.run(_app().list_tools())}
    for name in ("list_topics", "get_topic_info", "sample_messages"):
        assert tools[name].description.startswith("ROS 2 graph only")
        assert "list_endpoints" in tools[name].description


def test_domain_and_topic_params_are_explained_plainly() -> None:
    tools = {t.name: t for t in asyncio.run(_app().list_tools())}
    for name in ("list_participants", "participant_events", "topic_metrics", "list_endpoints"):
        desc = tools[name].inputSchema["properties"]["domain_id"]["description"]
        assert "does not switch domains" in desc
    topic = tools["peek_dds_samples"].inputSchema["properties"]["topic"]["description"]
    assert "`scan`" in topic and "`rt/scan`" in topic and "DCPSParticipant" in topic


def test_dds_only_error_points_to_the_dds_tools() -> None:
    assert "list_endpoints" in DDS_ONLY_ERROR_MSG
    assert "DCPSPublication" in DDS_ONLY_ERROR_MSG
    assert "DDS observability only" in DDS_ONLY_ERROR_MSG


def test_user_topic_result_is_empty_with_a_note() -> None:
    result = user_topic_result("scan", "live")
    assert (result.count, result.samples) == (0, [])
    assert result.note == USER_TOPIC_NOTE
    assert "list_endpoints" in result.note


def test_metrics_status_by_topic_kind() -> None:
    assert metrics_status("scan", 0) == "unsupported_user_topic"
    assert metrics_status("scan", 9) == "unsupported_user_topic"
    assert metrics_status("DCPSParticipant", 0) == "no_samples_yet"
    assert metrics_status("DCPSParticipant", 3) == "ok"


def _endpoint(role: str, topic: str, deadline_ns: int | None) -> EndpointInfo:
    return EndpointInfo(
        guid="g",
        role=role,  # type: ignore[arg-type]
        participant_guid="p",
        topic=topic,
        qos=QosProfile(
            reliability="RELIABLE",
            durability="VOLATILE",
            history="KEEP_LAST",
            history_depth=1,
            deadline_ns=deadline_ns,
        ),
        is_observer=False,
        domain_id=0,
        mode_effective="live",
    )


def test_declared_rate_is_the_shortest_writer_deadline() -> None:
    eps = [
        _endpoint("writer", "scan", 200_000_000),
        _endpoint("writer", "scan", 100_000_000),
        _endpoint("reader", "scan", 10_000_000),  # a requirement, not a promise
        _endpoint("writer", "other", 1_000_000),
    ]
    assert declared_hz_from_endpoints(eps, "scan") == pytest.approx(10.0)


def test_declared_rate_is_none_without_a_finite_writer_deadline() -> None:
    assert declared_hz_from_endpoints([_endpoint("writer", "scan", None)], "scan") is None
    assert declared_hz_from_endpoints([], "scan") is None


def test_lifecycle_participant_vendor_source() -> None:
    life = LifecycleBuffer()
    life.record_seen(guid="a", vendor="cyclone", hostname=None, domain_id=0)
    life.record_seen(guid="b", vendor="unknown", hostname=None, domain_id=0)
    by_guid = {p.guid: p for p in life.snapshot_participants(domain_id=0)}
    assert by_guid["a"].vendor_source == "guid_prefix"
    assert by_guid["b"].vendor_source == "none"
    assert not by_guid["a"].is_observer


def test_mock_participants_carry_the_new_fields() -> None:
    for p in MockAdapter().list_participants(0):
        assert p.vendor_source == "guid_prefix"
        assert p.is_observer is False


def test_mock_metrics_status_and_declared_rate() -> None:
    adapter = MockAdapter()
    busy = adapter.topic_metrics("/dds/heartbeat_10hz", 60, 0)
    assert busy.status == "ok" and busy.frequency_hz_declared == 10.0
    empty = adapter.topic_metrics("/dds/never_seen", 60, 0)
    assert empty.status == "no_samples_yet" and empty.frequency_hz_declared is None


def _health(adapter: Any) -> Any:
    return HealthService(
        Settings(mode="mock", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False),
        adapter,
    ).report()


class _Named:
    effective_mode = "live"

    def __init__(self, name: str) -> None:
        self.name = name


def test_health_reports_ros_tools_decoding_and_security() -> None:
    report = _health(MockAdapter())
    assert report.ros_tools_available is True
    assert report.payload_decoding == "disabled" and report.payload_decoding_reason
    assert report.dds_security == "not_supported"

    dds_only = _health(_Named("cyclone"))
    assert dds_only.mode == "live" and dds_only.ros_backend == "none"
    assert dds_only.ros_tools_available is False


def test_health_check_tool_exposes_the_new_fields() -> None:
    result = asyncio.run(_app().call_tool("health_check", {}))
    blocks = result[0] if isinstance(result, tuple) else result
    payload = json.loads(blocks[0].text)
    assert payload["ros_tools_available"] is True
    assert payload["dds_security"] == "not_supported"


# ------------------------------- warm-up wait -------------------------------


def _tracker() -> DiscoveryTracker:
    return DiscoveryTracker(lambda: ([], [], []), DiscoveryCaches(), domain_id=0, period_s=0.01)


def test_wait_warm_is_false_and_instant_before_start() -> None:
    assert _tracker().wait_warm(timeout_s=1.0) is False


def test_wait_warm_returns_once_passes_and_age_are_reached() -> None:
    tracker = _tracker()
    tracker.start()
    try:
        assert tracker.is_warm(min_passes=2, min_age_s=0.0) in (True, False)
        assert tracker.wait_warm(timeout_s=2.0, min_passes=2, min_age_s=0.1) is True
        assert tracker.status()["passes"] >= 2
    finally:
        tracker.stop()


def test_wait_warm_is_bounded_by_its_timeout() -> None:
    tracker = _tracker()
    tracker.start()
    try:
        # Age requirement far above the timeout: must give up, not hang.
        assert tracker.wait_warm(timeout_s=0.2, min_passes=2, min_age_s=60.0) is False
    finally:
        tracker.stop()


class _Warmable(MockAdapter):
    def __init__(self) -> None:
        self.waits = 0

    def await_discovery_ready(self) -> bool:
        self.waits += 1
        return True


def test_inspector_waits_for_discovery_on_dds_calls_only() -> None:
    adapter = _Warmable()
    inspector = Inspector(adapter)  # type: ignore[arg-type]
    inspector.list_participants()
    inspector.detect_qos_mismatches()
    inspector.peek_dds_samples("/dds/well_matched", 1)
    inspector.participant_events()
    inspector.topic_metrics("/dds/heartbeat_10hz")
    inspector.list_endpoints()
    assert adapter.waits == 6
    inspector.list_topics()
    inspector.analyze_bag("/tmp/x.mcap")
    assert adapter.waits == 6


def test_inspector_without_a_warmup_hook_still_answers() -> None:
    with pytest.raises(AdapterError):
        Inspector(MockAdapter()).peek_dds_samples("/nope", 1)  # type: ignore[arg-type]
