"""The reason a DDS tool gives when no DDS backend serves next to the ROS 2 CLI."""

from __future__ import annotations

import sys

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
    assert report.dds_inactive_note == "no DDS backend is selected."


def test_health_check_has_no_reason_when_dds_serves(monkeypatch: pytest.MonkeyPatch) -> None:
    from topicforge.adapters.ros2_mock import MockAdapter

    report = HealthService(_settings("mock"), MockAdapter()).report()
    assert report.dds_backend == "mock" and report.dds_inactive_note is None


def _platform(
    monkeypatch: pytest.MonkeyPatch, system: str, machine: str, python: tuple[int, int]
) -> None:
    """Pretend to run on `system`/`machine`/`python`, where no wheel means no binding.

    The real binding may be importable in the test environment, so its absence is
    simulated too: a `None` entry in `sys.modules` makes `import cyclonedds` fail.
    """
    for name in {"cyclonedds", *(m for m in sys.modules if m.startswith("cyclonedds."))}:
        monkeypatch.setitem(sys.modules, name, None)
    for name in [m for m in sys.modules if m.startswith("topicforge.adapters.dds_cyclone")]:
        monkeypatch.delitem(sys.modules, name)  # force a re-import, which now fails
    monkeypatch.setattr(f"{_FACTORY_MODULE}.platform.system", lambda: system)
    monkeypatch.setattr(f"{_FACTORY_MODULE}.platform.machine", lambda: machine)
    monkeypatch.setattr(f"{_FACTORY_MODULE}.sys.version_info", (*python, 0, "final", 0))


@pytest.mark.parametrize(
    ("system", "machine", "python"),
    [
        ("Linux", "x86_64", (3, 12)),
        ("Windows", "AMD64", (3, 13)),
        ("Darwin", "arm64", (3, 11)),
        ("Darwin", "x86_64", (3, 10)),
    ],
)
def test_wheel_platforms_get_the_plain_install_hint(
    monkeypatch: pytest.MonkeyPatch, system: str, machine: str, python: tuple[int, int]
) -> None:
    _platform(monkeypatch, system, machine, python)
    _installed(monkeypatch)
    assert factory.cyclone_wheel_gap() is None
    reason = factory._dds_inactive_reason(_settings("cyclone"))
    assert "no prebuilt wheel" not in reason and "topicforge[dds-cyclone]" in reason


@pytest.mark.parametrize(
    ("system", "machine", "python", "label"),
    [
        ("Linux", "aarch64", (3, 12), "linux-aarch64"),
        ("Linux", "x86_64", (3, 14), "Python 3.14"),
        ("Linux", "aarch64", (3, 14), "linux-aarch64 / Python 3.14"),
        ("Windows", "ARM64", (3, 12), "windows-arm64"),
    ],
)
def test_unsupported_platform_says_why_in_plain_words(
    monkeypatch: pytest.MonkeyPatch,
    system: str,
    machine: str,
    python: tuple[int, int],
    label: str,
) -> None:
    _platform(monkeypatch, system, machine, python)
    _installed(monkeypatch)
    assert factory.cyclone_wheel_gap() == label
    reason = factory._dds_inactive_reason(_settings("cyclone"))
    assert f"no prebuilt wheel for {label}" in reason
    assert "Cyclone C library" in reason and "mock" in reason


def test_unsupported_platform_message_reaches_health_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _platform(monkeypatch, "Linux", "aarch64", (3, 12))
    _installed(monkeypatch)
    monkeypatch.setattr(Ros2CliAdapter, "is_available", lambda self: True)
    adapter = factory.build_adapter(_settings("cyclone"))
    report = HealthService(_settings("cyclone"), adapter).report()
    assert report.dds_backend == "none"
    assert "linux-aarch64" in (report.dds_inactive_note or "")


def test_cyclone_requested_without_ros2_falls_back_to_mock_with_the_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from topicforge.adapters.ros2_mock import MockAdapter

    _platform(monkeypatch, "Linux", "aarch64", (3, 12))
    _installed(monkeypatch)
    monkeypatch.setattr(Ros2CliAdapter, "is_available", lambda self: False)
    adapter = factory.build_adapter(_settings("cyclone"))
    assert isinstance(adapter, MockAdapter)
    report = HealthService(_settings("cyclone"), adapter).report()
    assert "no prebuilt wheel for linux-aarch64" in (report.dds_inactive_note or "")


def test_deliberate_mock_has_no_inactive_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Ros2CliAdapter, "is_available", lambda self: False)
    adapter = factory.build_adapter(_settings("mock"))
    assert HealthService(_settings("mock"), adapter).report().dds_inactive_note is None
