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
    ParticipantEvent,
    ParticipantInfo,
    SampleResult,
    TopicInfo,
    TopicMetrics,
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

    def participant_events(
        self, domain_id: int = 0, lookback_seconds: int = 300
    ) -> list[ParticipantEvent]:
        return []

    def topic_metrics(
        self, topic: str, window_seconds: int = 60, domain_id: int = 0
    ) -> TopicMetrics:
        return TopicMetrics(
            topic=topic,
            window_seconds=window_seconds,
            window_seconds_actual=0.0,
            samples_observed=0,
            sequence_gaps_count=0,
            sequence_numbers_available=False,
            latency_available=False,
            mode_effective="live",
        )

    def peek_bag_samples(self, path: str, topic: str, count: int) -> SampleResult:
        raise AdapterError("DDS adapter does not handle bags")


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


# ---------------------------------------------------------------------------
# v0.4.0 Phase 1.5 — new vendor branches in _try_build_dds
# ---------------------------------------------------------------------------


def test_opendds_backend_falls_back_to_ros2_cli_when_binding_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`pyopendds` is not on PyPI ; the OpenDdsAdapter reports unavailable
    and the factory falls back to ROS2 CLI alone."""
    monkeypatch.setattr(Ros2CliAdapter, "is_available", lambda self: True)
    settings = _live_settings(dds_backend="opendds")
    adapter = factory.build_adapter(settings)
    assert isinstance(adapter, Ros2CliAdapter)


def test_dust_backend_falls_back_to_ros2_cli_when_binding_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dust DDS stub always reports unavailable ; ROS2 CLI takes over."""
    monkeypatch.setattr(Ros2CliAdapter, "is_available", lambda self: True)
    settings = _live_settings(dds_backend="dust")
    adapter = factory.build_adapter(settings)
    assert isinstance(adapter, Ros2CliAdapter)


def test_opensplice_backend_falls_back_when_pro_package_absent(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Pro tier vendor without `topicforge_pro` package installed → fallback."""
    monkeypatch.setattr(Ros2CliAdapter, "is_available", lambda self: True)
    settings = _live_settings(dds_backend="opensplice")
    with caplog.at_level("WARNING"):
        adapter = factory.build_adapter(settings)
    assert isinstance(adapter, Ros2CliAdapter)
    assert any("opensplice" in record.message.lower() for record in caplog.records)


def test_coredx_backend_falls_back_when_pro_package_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Ros2CliAdapter, "is_available", lambda self: True)
    settings = _live_settings(dds_backend="coredx")
    adapter = factory.build_adapter(settings)
    assert isinstance(adapter, Ros2CliAdapter)


def test_intercom_backend_falls_back_when_pro_package_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Ros2CliAdapter, "is_available", lambda self: True)
    settings = _live_settings(dds_backend="intercom")
    adapter = factory.build_adapter(settings)
    assert isinstance(adapter, Ros2CliAdapter)


def test_pro_vendor_module_path_uses_topicforge_pro_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful Pro vendor load should target `topicforge_pro.adapters.<vendor>`
    (and `rti_connext` specifically for the RTI alias). We can't run a
    real Pro adapter here ; we assert the import path by patching
    `importlib.import_module` and capturing the requested name.
    """
    import importlib

    monkeypatch.setattr(Ros2CliAdapter, "is_available", lambda self: True)
    requested: list[str] = []

    def fake_import(name: str) -> object:
        requested.append(name)
        raise ImportError(f"simulated missing {name}")

    monkeypatch.setattr(importlib, "import_module", fake_import)

    settings = _live_settings(dds_backend="rti")
    factory.build_adapter(settings)
    assert "topicforge_pro.adapters.rti_connext" in requested
