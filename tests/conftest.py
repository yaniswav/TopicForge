"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from topicforge.adapters.ros2_mock import MockAdapter
from topicforge.config import Settings
from topicforge.services import HealthService, Inspector


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
