"""Tests for `topicforge.services.HealthService`."""

from __future__ import annotations

from topicforge.config import Settings
from topicforge.services import HealthService
from topicforge.services.inspector import MAX_SAMPLE_COUNT


def test_health_report_in_mock_mode() -> None:
    settings = Settings(mode="mock", log_level="INFO", ros2_executable="ros2")
    report = HealthService(settings).report()
    assert report.mode == "mock"
    assert report.requested_mode == "mock"
    assert report.server_version


def test_health_report_when_ros2_missing() -> None:
    settings = Settings(
        mode="auto",
        log_level="INFO",
        ros2_executable="definitely-not-a-real-binary-xyz",
    )
    report = HealthService(settings).report()
    assert report.ros2_available is False
    # auto with missing ros2 resolves to mock.
    assert report.mode == "mock"
    assert report.requested_mode == "auto"


def test_health_report_exposes_sample_cap() -> None:
    settings = Settings(mode="mock", log_level="INFO", ros2_executable="ros2")
    report = HealthService(settings).report()
    assert report.max_sample_count == MAX_SAMPLE_COUNT == 50


def test_health_report_serializes_to_dict() -> None:
    settings = Settings(mode="mock", log_level="INFO", ros2_executable="ros2")
    payload = HealthService(settings).report().model_dump()
    # Tool handlers rely on this shape — pin it.
    assert {
        "mode",
        "requested_mode",
        "ros2_available",
        "ros2_distro",
        "server_version",
        "max_sample_count",
    } <= payload.keys()
    assert "bag_tool_available" not in payload
