"""Shared pytest fixtures."""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from topicforge.adapters.ros2_mock import MockAdapter
from topicforge.config import Settings
from topicforge.services import HealthService, Inspector


@pytest.fixture(autouse=True)
def _close_cyclone_adapters(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Stop every real Cyclone adapter a test builds, so its tracker thread cannot leak.

    A real `CycloneDdsAdapter` starts a `topicforge-discovery` thread and registers
    `close` with `atexit`, which is right for a server (one adapter per process) but
    leaves a thread behind for each adapter a test builds. A no-op without the binding.
    """
    if importlib.util.find_spec("cyclonedds") is None:
        yield
        return
    from topicforge.adapters.dds_cyclone import CycloneDdsAdapter

    built: list[CycloneDdsAdapter] = []
    original = CycloneDdsAdapter.__init__

    def tracking_init(self: CycloneDdsAdapter, *args: Any, **kwargs: Any) -> None:
        original(self, *args, **kwargs)
        built.append(self)

    monkeypatch.setattr(CycloneDdsAdapter, "__init__", tracking_init)
    yield
    for adapter in built:
        adapter.close()


@pytest.fixture
def mock_adapter() -> MockAdapter:
    return MockAdapter()


@pytest.fixture
def inspector(mock_adapter: MockAdapter) -> Inspector:
    return Inspector(mock_adapter)


@pytest.fixture
def mock_settings() -> Settings:
    return Settings(mode="mock", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False)


@pytest.fixture
def health_service(mock_settings: Settings, mock_adapter: MockAdapter) -> HealthService:
    return HealthService(mock_settings, mock_adapter)


# The recorded bag is left out of the sdist; the modules that read it skip there.
_BAG_FIXTURE = Path(__file__).parent / "fixtures" / "bags" / "omnisim_humble"
_BAG_FIXTURE_MODULES = {"test_bag_omnisim_humble", "test_bag_stats", "test_live_adapter_graph"}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if _BAG_FIXTURE.exists():
        return
    skip = pytest.mark.skip(reason="tests/fixtures/bags is not in the sdist")
    for item in items:
        if item.module.__name__.rsplit(".", 1)[-1] in _BAG_FIXTURE_MODULES:
            item.add_marker(skip)
