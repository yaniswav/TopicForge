"""Tests for `topicforge.services.HealthService`."""

from __future__ import annotations

from topicforge.config import Settings
from topicforge.services import HealthService
from topicforge.services.inspector import MAX_SAMPLE_COUNT


def test_health_report_in_mock_mode() -> None:
    settings = Settings(
        mode="mock", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False
    )
    report = HealthService(settings).report()
    assert report.mode == "mock"
    assert report.requested_mode == "mock"
    assert report.server_version


def test_health_report_when_ros2_missing() -> None:
    settings = Settings(
        mode="auto",
        log_level="INFO",
        ros2_executable="definitely-not-a-real-binary-xyz",
        telemetry_enabled=False,
    )
    report = HealthService(settings).report()
    assert report.ros2_available is False
    # auto with missing ros2 resolves to mock.
    assert report.mode == "mock"
    assert report.requested_mode == "auto"


def test_health_report_exposes_sample_cap() -> None:
    settings = Settings(
        mode="mock", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False
    )
    report = HealthService(settings).report()
    assert report.max_sample_count == MAX_SAMPLE_COUNT == 50


def test_health_report_serializes_to_dict() -> None:
    settings = Settings(
        mode="mock", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False
    )
    payload = HealthService(settings).report().model_dump()
    # Tool handlers rely on this shape — pin it.
    assert {
        "mode",
        "requested_mode",
        "ros2_available",
        "ros2_distro",
        "server_version",
        "max_sample_count",
        # v0.3.0: DDS fields now populated by HealthService (v0.2.0 latent bug).
        "dds_backend",
        "dds_domain_id",
        "middleware_available",
        # v0.4.0 Phase 1: ros_backend symmetric to dds_backend.
        "ros_backend",
    } <= payload.keys()
    assert "bag_tool_available" not in payload


# ---------------------------------------------------------------------------
# DDS fields (v0.3.0 — previously defaults regardless of configuration)
# ---------------------------------------------------------------------------


def test_health_report_populates_dds_backend_mock_by_default() -> None:
    settings = Settings(
        mode="mock", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False
    )
    report = HealthService(settings).report()
    # Global mock mode collapses DDS backend to mock.
    assert report.dds_backend == "mock"
    assert report.dds_domain_id == 0


def test_health_report_populates_dds_backend_fast_when_settings_say_so() -> None:
    """If settings say fast and we're in live mode, health reports fast."""
    settings = Settings(
        mode="live",
        log_level="INFO",
        ros2_executable="ros2",
        telemetry_enabled=False,
        dds_backend="fast",
        dds_domain_id=42,
    )
    report = HealthService(settings).report()
    assert report.dds_backend == "fast"
    assert report.dds_domain_id == 42


def test_health_report_middleware_available_for_mock() -> None:
    """Mock backend is always available — no Python bindings needed."""
    settings = Settings(
        mode="mock", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False
    )
    report = HealthService(settings).report()
    assert report.middleware_available is True


def test_health_report_middleware_available_false_without_binding() -> None:
    """fast/cyclone without bindings reports middleware_available=False.

    On this test host neither `fastdds` nor `cyclonedds` is expected to be
    installed (CI installs them only for the requires_* gated tests). If
    they happen to be installed, this assertion still holds because we
    explicitly request fast and find_spec checks for the right module.
    """
    import importlib.util

    if importlib.util.find_spec("fastdds") is not None:
        pytest.skip("fastdds installed — this test asserts the negative path")

    settings = Settings(
        mode="live",
        log_level="INFO",
        ros2_executable="ros2",
        telemetry_enabled=False,
        dds_backend="fast",
    )
    report = HealthService(settings).report()
    assert report.middleware_available is False


import pytest  # noqa: E402 — used above only in the skipped path

# ---------------------------------------------------------------------------
# ros_backend (v0.4.0 Phase 1 — symmetric to dds_backend, supports composite)
# ---------------------------------------------------------------------------


def test_health_report_ros_backend_mock_in_mock_mode() -> None:
    settings = Settings(
        mode="mock", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False
    )
    report = HealthService(settings).report()
    assert report.ros_backend == "mock"


def test_health_report_ros_backend_none_when_live_but_no_ros2() -> None:
    """Live mode requested but `ros2` not on PATH — ros_backend == 'none'.

    The composite path may still build a DDS-only adapter ; the health
    field is purely a description of which ROS half resolves, not which
    adapter actually runs.
    """
    settings = Settings(
        mode="live",
        log_level="INFO",
        ros2_executable="definitely-not-a-real-binary-xyz",
        telemetry_enabled=False,
    )
    report = HealthService(settings).report()
    assert report.ros_backend == "none"


def test_health_report_ros_backend_ros2_cli_when_live_and_ros2_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When `ros2` is on PATH in live mode, ros_backend reports 'ros2_cli'."""
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/ros2")
    settings = Settings(
        mode="live", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False
    )
    report = HealthService(settings).report()
    assert report.ros_backend == "ros2_cli"
