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
    # mcp 2.x returns a `CallToolResult`: structured output, with the JSON text as fallback.
    if isinstance(result.structured_content, dict):
        return result.structured_content
    return json.loads(result.content[0].text)


def test_live_without_ros2_reports_the_mock_that_was_built() -> None:
    """Regression: `TOPICFORGE_MODE=live` without `ros2` builds MockAdapter,
    and `health_check` must say so rather than echoing the requested mode."""
    settings = _settings(mode="live", ros2_executable="definitely-not-a-real-binary-xyz")
    payload = _call_health_check(build_app(settings))
    assert payload["mode"] == "mock"
    assert payload["requested_mode"] == "live"
    assert payload["ros_backend"] == "mock"
    assert payload["dds_backend"] == "mock"


def test_report_carries_observer_and_tracker_status_when_the_adapter_has_them() -> None:
    adapter = _NamedAdapter("cyclone")
    adapter.observer_status = lambda: {  # type: ignore[attr-defined]
        "observer_started_ns": 5,
        "running": True,
        "passes": 7,
        "errors": 1,
        "last_pass_ns": 9,
    }
    report = HealthService(_settings(), adapter).report()  # type: ignore[arg-type]

    assert (report.observer_started_ns, report.tracker_running) == (5, True)
    assert (report.tracker_passes, report.tracker_errors, report.tracker_last_pass_ns) == (7, 1, 9)
    assert report.now_ns is not None


def test_report_leaves_tracker_fields_empty_without_an_observer() -> None:
    report = HealthService(_settings(), _adapter("mock")).report()

    assert report.observer_started_ns is None and report.tracker_errors is None


def test_sim_clock_is_unknown_in_mock_mode() -> None:
    assert HealthService(_settings(), MockAdapter()).report().sim_clock_published is None


class _ClockAdapter(_NamedAdapter):
    def __init__(self, outcome: object) -> None:
        super().__init__("ros2_cli")
        self._outcome = outcome

    def sim_clock_published(self) -> bool | None:
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome  # type: ignore[return-value]


@pytest.mark.parametrize(
    ("outcome", "expected"), [(True, True), (False, False), (None, None), (RuntimeError(), None)]
)
def test_sim_clock_hint_comes_from_the_adapter_and_never_breaks_health(
    outcome: object, expected: bool | None
) -> None:
    adapter: Any = _ClockAdapter(outcome)
    assert HealthService(_settings(), adapter).report().sim_clock_published is expected


def test_cli_adapter_reads_publisher_count_of_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    from topicforge.adapters.ros2_live import Ros2CliAdapter

    adapter = Ros2CliAdapter()
    outputs = {
        "Type: rosgraph_msgs/msg/Clock\nPublisher count: 1\nSubscription count: 0\n": True,
        "Type: rosgraph_msgs/msg/Clock\nPublisher count: 0\nSubscription count: 2\n": False,
    }
    for text, expected in outputs.items():
        monkeypatch.setattr(adapter, "_run", lambda cmd, timeout=8.0, t=text: t)
        assert adapter.sim_clock_published() is expected


def test_cli_adapter_clock_probe_failure_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    from topicforge.adapters.base import AdapterError
    from topicforge.adapters.ros2_live import Ros2CliAdapter

    adapter = Ros2CliAdapter()

    def unknown(cmd: list[str], timeout: float = 8.0) -> str:
        raise AdapterError("failed (exit 1): Unknown topic '/clock'")

    def timed_out(cmd: list[str], timeout: float = 8.0) -> str:
        raise AdapterError("timed out after 8.0s")

    monkeypatch.setattr(adapter, "_run", unknown)
    assert adapter.sim_clock_published() is False
    monkeypatch.setattr(adapter, "_run", timed_out)
    assert adapter.sim_clock_published() is None
