"""Tests for `topicforge.services.HealthService`.

`HealthService` reports the adapter that was actually built, so most tests
inject a minimal adapter stand-in carrying only the two attributes it reads
(`name`, `effective_mode`).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from typing import Any

import pytest

from topicforge.adapters.base import AdapterName, EffectiveMode, MiddlewareAdapter
from topicforge.adapters.ros2_mock import MockAdapter
from topicforge.config import Settings
from topicforge.constants import MAX_SAMPLE_COUNT
from topicforge.server import build_app
from topicforge.services import HealthService


class _NamedAdapter:
    """Adapter stand-in exposing just what `HealthService` reads."""

    def __init__(self, name: AdapterName, effective_mode: EffectiveMode = "live") -> None:
        self.name = name
        self._effective_mode = effective_mode

    @property
    def effective_mode(self) -> EffectiveMode:
        return self._effective_mode


def _adapter(name: AdapterName, effective_mode: EffectiveMode = "live") -> MiddlewareAdapter:
    return _NamedAdapter(name, effective_mode)  # type: ignore[return-value]


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "mode": "mock",
        "log_level": "INFO",
        "ros2_executable": "ros2",
        "telemetry_enabled": False,
    }
    base.update(overrides)
    return Settings(**base)


def test_health_report_in_mock_mode() -> None:
    report = HealthService(_settings(), MockAdapter()).report()
    assert report.mode == "mock"
    assert report.requested_mode == "mock"
    assert report.server_version


def test_health_report_when_ros2_missing() -> None:
    settings = _settings(mode="auto", ros2_executable="definitely-not-a-real-binary-xyz")
    report = HealthService(settings, MockAdapter()).report()
    assert report.ros2_available is False
    # auto with missing ros2 resolves to mock.
    assert report.mode == "mock"
    assert report.requested_mode == "auto"


def test_health_report_exposes_sample_cap() -> None:
    report = HealthService(_settings(), MockAdapter()).report()
    assert report.max_sample_count == MAX_SAMPLE_COUNT == 50


def test_health_report_serializes_to_dict() -> None:
    payload = HealthService(_settings(), MockAdapter()).report().model_dump()
    # Tool handlers rely on this shape: pin it.
    assert {
        "mode",
        "requested_mode",
        "ros2_available",
        "ros2_distro",
        "server_version",
        "max_sample_count",
        "dds_backend",
        "dds_domain_id",
        "middleware_available",
        "ros_backend",
    } <= payload.keys()
    assert "bag_tool_available" not in payload


# ---------------------------------------------------------------------------
# The report follows the adapter that was built, not the requested settings
# ---------------------------------------------------------------------------


def test_mode_follows_adapter_not_requested_mode() -> None:
    """`live` requested but the factory fell back to mock: report mock."""
    settings = _settings(mode="live", ros2_executable="definitely-not-a-real-binary-xyz")
    report = HealthService(settings, MockAdapter()).report()
    assert report.mode == "mock"
    assert report.requested_mode == "live"


def test_mock_adapter_reports_mock_on_both_halves() -> None:
    report = HealthService(_settings(), MockAdapter()).report()
    assert report.ros_backend == "mock"
    assert report.dds_backend == "mock"
    assert report.middleware_available is True


@pytest.mark.parametrize(
    ("name", "ros_backend", "dds_backend"),
    [
        ("ros2_cli", "ros2_cli", "none"),
        ("cyclone", "none", "cyclone"),
        ("fast", "none", "fast"),
        ("ros2_cli+cyclone", "ros2_cli", "cyclone"),
        ("ros2_cli+fast", "ros2_cli", "fast"),
    ],
)
def test_backends_are_deduced_from_adapter_name(
    name: AdapterName, ros_backend: str, dds_backend: str
) -> None:
    settings = _settings(mode="live", dds_domain_id=42)
    report = HealthService(settings, _adapter(name)).report()
    assert report.mode == "live"
    assert report.ros_backend == ros_backend
    assert report.dds_backend == dds_backend
    assert report.dds_domain_id == 42


def test_middleware_available_when_a_dds_backend_serves() -> None:
    """A serving DDS adapter means its binding loaded: no find_spec probe needed."""
    report = HealthService(_settings(mode="live"), _adapter("cyclone")).report()
    assert report.middleware_available is True


def test_middleware_unavailable_when_requested_binding_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requested `fast`, fell back to ROS2 CLI alone, binding absent: stay visible."""
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *a, **k: None if name == "fastdds" else real_find_spec(name, *a, **k),
    )
    settings = _settings(mode="live", dds_backend="fast")
    report = HealthService(settings, _adapter("ros2_cli")).report()
    assert report.dds_backend == "none"
    assert report.middleware_available is False


def test_middleware_available_when_requested_binding_present_but_not_serving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Binding importable but the adapter did not come up: the probe still says True."""
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a, **k: object())
    settings = _settings(mode="live", dds_backend="cyclone")
    report = HealthService(settings, _adapter("ros2_cli")).report()
    assert report.middleware_available is True


def test_middleware_unavailable_without_dds_module() -> None:
    report = HealthService(_settings(mode="live"), _adapter("ros2_cli")).report()
    assert report.dds_backend == "none"
    assert report.middleware_available is False


# ---------------------------------------------------------------------------
# End to end through build_app
# ---------------------------------------------------------------------------


def _call_health_check(app: Any) -> dict[str, Any]:
    result = asyncio.run(app.call_tool("health_check", {}))
    # FastMCP returns either (content, structured) or a content list
    # depending on the SDK version.
    if isinstance(result, tuple):
        structured = result[1]
        if isinstance(structured, dict):
            return structured
        result = result[0]
    return json.loads(result[0].text)


def test_live_without_ros2_reports_the_mock_that_was_built() -> None:
    """Regression: `TOPICFORGE_MODE=live` without `ros2` builds MockAdapter,
    and `health_check` must say so rather than echoing the requested mode."""
    settings = _settings(mode="live", ros2_executable="definitely-not-a-real-binary-xyz")
    payload = _call_health_check(build_app(settings))
    assert payload["mode"] == "mock"
    assert payload["requested_mode"] == "live"
    assert payload["ros_backend"] == "mock"
    assert payload["dds_backend"] == "mock"
