"""The reason a DDS tool gives when no DDS backend serves next to the ROS 2 CLI."""

from __future__ import annotations

import pytest

from topicforge.adapters.base import AdapterError
from topicforge.adapters.ros2_live import Ros2CliAdapter
from topicforge.config import Settings
from topicforge.services import HealthService, factory

_SETTINGS_MODULE = "topicforge.config.settings"
_FACTORY_MODULE = "topicforge.services.factory"


def _settings(dds_backend: str) -> Settings:
    return Settings(
        mode="live",
        log_level="INFO",
        ros2_executable="ros2",
        telemetry_enabled=False,
        dds_backend=dds_backend,  # type: ignore[arg-type]
    )


def _installed(monkeypatch: pytest.MonkeyPatch, *modules: str) -> None:
    monkeypatch.setattr(f"{_FACTORY_MODULE}.module_is_importable", lambda name: name in modules)
    monkeypatch.setattr(f"{_SETTINGS_MODULE}.module_is_importable", lambda name: name in modules)


def test_not_selected_with_binding_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    _installed(monkeypatch, "cyclonedds")
    reason = factory._dds_inactive_reason(_settings("mock"))
    assert "no DDS backend is selected" in reason
    assert "`cyclonedds` binding is installed" in reason
    assert "TOPICFORGE_DDS_BACKEND=cyclone" in reason


def test_not_selected_without_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    _installed(monkeypatch)
    reason = factory._dds_inactive_reason(_settings("mock"))
    assert "no DDS backend is selected" in reason and "topicforge[dds-cyclone]" in reason


def test_auto_that_finds_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _installed(monkeypatch)
    reason = factory._dds_inactive_reason(_settings("auto"))
    assert "auto" in reason and "no DDS binding" in reason


def test_selected_but_binding_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _installed(monkeypatch)
    reason = factory._dds_inactive_reason(_settings("cyclone"))
    assert "TOPICFORGE_DDS_BACKEND=cyclone" in reason and "not installed" in reason


def test_fast_binding_missing_does_not_suggest_pip(monkeypatch: pytest.MonkeyPatch) -> None:
    _installed(monkeypatch)
    reason = factory._dds_inactive_reason(_settings("fast"))
    assert "`fastdds`" in reason and "not installed" in reason and "pip install" not in reason


def test_selected_binding_installed_but_adapter_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    _installed(monkeypatch, "cyclonedds")
    reason = factory._dds_inactive_reason(_settings("cyclone"))
    assert "installed" in reason and "failed to load or start" in reason


def test_stub_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    _installed(monkeypatch)
    assert "stub" in factory._dds_inactive_reason(_settings("opendds"))


def test_factory_hands_the_reason_to_the_cli_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    _installed(monkeypatch, "cyclonedds")
    monkeypatch.setattr(Ros2CliAdapter, "is_available", lambda self: True)
    adapter = factory.build_adapter(_settings("mock"))
    assert isinstance(adapter, Ros2CliAdapter)
    with pytest.raises(AdapterError, match="no DDS backend is selected"):
        adapter.list_endpoints()


def test_health_check_reports_the_reason_when_dds_is_none() -> None:
    adapter = Ros2CliAdapter(dds_inactive_reason="no DDS backend is selected.")
    report = HealthService(_settings("mock"), adapter).report()
    assert report.dds_backend == "none"
    assert report.dds_inactive_reason == "no DDS backend is selected."


def test_health_check_has_no_reason_when_dds_serves(monkeypatch: pytest.MonkeyPatch) -> None:
    from topicforge.adapters.ros2_mock import MockAdapter

    report = HealthService(_settings("mock"), MockAdapter()).report()
    assert report.dds_backend == "mock" and report.dds_inactive_reason is None
