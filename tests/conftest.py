"""Shared pytest fixtures."""

from __future__ import annotations

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
    return Settings(mode="mock", log_level="INFO", ros2_executable="ros2")


@pytest.fixture
def health_service(mock_settings: Settings) -> HealthService:
    return HealthService(mock_settings)
