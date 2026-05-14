"""Tests for `topicforge.services.factory.build_adapter`.

The factory's decision tree is the single source of truth for which
adapter actually runs at startup. These tests pin every branch:

  * mock mode always returns MockAdapter (no composite).
  * live + no DDS + no `ros2` on PATH → fallback to MockAdapter.
  * live + ros2 on PATH + DDS backend mock → Ros2CliAdapter alone.
  * live + ros2 on PATH + DDS backend cyclone (binding missing) →
    Ros2CliAdapter alone (graceful degradation).
  * live + ros2 on PATH + DDS backend cyclone (binding installed) →
    CompositeAdapter wrapping both.
  * live + no ros2 on PATH + DDS backend installed → DDS adapter alone.
  * rti backend → warning + ROS2 CLI alone.

The DDS adapters are heavyweight to instantiate (they create real DDS
participants when imported), so we stub them via monkeypatching.
"""

from __future__ import annotations

import pytest

from topicforge.adapters.base import AdapterError, AdapterName, EffectiveMode
from topicforge.adapters.composite import CompositeAdapter
from topicforge.adapters.ros2_live import Ros2CliAdapter
from topicforge.adapters.ros2_mock import MockAdapter
from topicforge.config import Settings
from topicforge.models import (
    BagAnalysis,
    MessageSample,
    MismatchReport,
    ParticipantInfo,
    SampleResult,
    TopicInfo,
)
from topicforge.services import factory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _live_settings(*, dds_backend: str = "mock") -> Settings:
    return Settings(
        mode="live",
        log_level="INFO",
        ros2_executable="ros2",
        telemetry_enabled=False,
        dds_backend=dds_backend,  # type: ignore[arg-type]
        dds_domain_id=0,
    )


class _StubDdsAdapter:
    """Minimal stand-in for a DDS adapter satisfying MiddlewareAdapter."""

    name: AdapterName = "cyclone"

    def __init__(self, *, available: bool = True) -> None:
        self._available = available

    @property
    def effective_mode(self) -> EffectiveMode:
        return "live"

    def is_available(self) -> bool:
        return self._available

    def list_topics(self) -> list[TopicInfo]:
        raise AdapterError("dds-only")

    def get_topic_info(self, topic: str) -> TopicInfo:
        raise AdapterError("dds-only")

    def sample_messages(self, topic: str, count: int) -> list[MessageSample]:
        raise AdapterError("dds-only")

    def analyze_bag(self, path: str) -> BagAnalysis:
        raise AdapterError("dds-only")

    def list_participants(self, domain_id: int = 0) -> list[ParticipantInfo]:
        return []

    def detect_qos_mismatches(self, topic: str | None = None) -> list[MismatchReport]:
        return []

    def peek_dds_samples(self, topic: str, count: int) -> SampleResult:
        return SampleResult(topic=topic, count=0, samples=[], mode_effective="live")


# ---------------------------------------------------------------------------
# Branch 1 — mock mode
# ---------------------------------------------------------------------------


def test_mock_mode_returns_mock_adapter() -> None:
    settings = Settings(
        mode="mock", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False
    )
    assert isinstance(factory.build_adapter(settings), MockAdapter)


def test_mock_mode_ignores_dds_backend_selection() -> None:
    """Global mock mode overrides DDS backend — MockAdapter serves all 8."""
    settings = Settings(
        mode="mock",
        log_level="INFO",
        ros2_executable="ros2",
        telemetry_enabled=False,
        dds_backend="cyclone",
    )
    assert isinstance(factory.build_adapter(settings), MockAdapter)


# ---------------------------------------------------------------------------
# Branch 5 — final fallback when nothing live is reachable
# ---------------------------------------------------------------------------


def test_live_without_ros2_or_dds_falls_back_to_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Ros2CliAdapter, "is_available", lambda self: False)
    settings = _live_settings(dds_backend="mock")
    adapter = factory.build_adapter(settings)
    assert isinstance(adapter, MockAdapter)


# ---------------------------------------------------------------------------
# Branch 3 — live + DDS backend mock → Ros2CliAdapter alone
# ---------------------------------------------------------------------------


def test_live_with_dds_mock_returns_ros2_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Ros2CliAdapter, "is_available", lambda self: True)
    settings = _live_settings(dds_backend="mock")
    adapter = factory.build_adapter(settings)
    assert isinstance(adapter, Ros2CliAdapter)


# ---------------------------------------------------------------------------
# Branch 2a — composite (both halves up)
# ---------------------------------------------------------------------------


def test_composite_when_both_ros_and_dds_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Ros2CliAdapter, "is_available", lambda self: True)
    monkeypatch.setattr(factory, "_try_build_dds", lambda settings: _StubDdsAdapter())

    settings = _live_settings(dds_backend="cyclone")
    adapter = factory.build_adapter(settings)

    assert isinstance(adapter, CompositeAdapter)
    assert adapter.name == "ros2_cli+cyclone"


# ---------------------------------------------------------------------------
# Branch 2b — DDS binding missing, fall back to ROS2 CLI alone
# ---------------------------------------------------------------------------


def test_dds_missing_falls_back_to_ros2_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Ros2CliAdapter, "is_available", lambda self: True)
    monkeypatch.setattr(factory, "_try_build_dds", lambda settings: None)

    settings = _live_settings(dds_backend="cyclone")
    adapter = factory.build_adapter(settings)

    assert isinstance(adapter, Ros2CliAdapter)


# ---------------------------------------------------------------------------
# Branch 2c — ROS2 CLI missing, DDS up → DDS adapter alone
# ---------------------------------------------------------------------------


def test_dds_only_when_ros2_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Ros2CliAdapter, "is_available", lambda self: False)
    stub = _StubDdsAdapter()
    monkeypatch.setattr(factory, "_try_build_dds", lambda settings: stub)

    settings = _live_settings(dds_backend="cyclone")
    adapter = factory.build_adapter(settings)

    assert adapter is stub  # DDS-only path, no composite wrapper.


# ---------------------------------------------------------------------------
# Branch 4 — rti requested → ROS2 CLI alone (logged warning)
# ---------------------------------------------------------------------------


def test_rti_backend_falls_back_to_ros2_cli_alone(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(Ros2CliAdapter, "is_available", lambda self: True)
    settings = _live_settings(dds_backend="rti")

    with caplog.at_level("WARNING"):
        adapter = factory.build_adapter(settings)

    assert isinstance(adapter, Ros2CliAdapter)
    assert any("rti" in record.message.lower() for record in caplog.records)


# ---------------------------------------------------------------------------
# DDS auto resolution flowing into the factory
# ---------------------------------------------------------------------------


def test_auto_mode_with_no_ros2_picks_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    # `auto` resolves to live only if `ros2_executable` is on PATH (per
    # `Settings.effective_mode`). With a clearly missing executable name
    # the resolver picks "mock".
    settings = Settings(
        mode="auto",
        log_level="INFO",
        ros2_executable="definitely-not-a-real-binary-xyz",
        telemetry_enabled=False,
    )
    adapter = factory.build_adapter(settings)
    assert isinstance(adapter, MockAdapter)
