"""Tests for `topicforge.services.factory.build_adapter`.

The factory's decision tree is the single source of truth for which
adapter actually runs at startup. These tests pin every branch:

  * mock mode always returns MockAdapter (no composite).
  * live + no DDS + no `ros2` on PATH -> fallback to MockAdapter.
  * live + ros2 on PATH + DDS backend mock -> Ros2CliAdapter alone.
  * live + ros2 on PATH + DDS backend cyclone (binding missing) ->
    Ros2CliAdapter alone (graceful degradation).
  * live + ros2 on PATH + DDS backend cyclone (binding installed) ->
    CompositeAdapter wrapping both.
  * live + no ros2 on PATH + DDS backend installed -> DDS adapter alone.
  * auto + explicit DDS backend + no ros2 -> that DDS adapter alone, or
    mock with an explicit warning when the binding is missing.
  * a DDS constructor that raises never escapes `build_adapter`.

The DDS adapters are heavyweight to instantiate (they create real DDS
participants when imported), so we stub them via monkeypatching.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
import types
from collections.abc import Callable

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

_CYCLONE_MODULE = "topicforge.adapters.dds_cyclone"
_FAST_MODULE = "topicforge.adapters.dds_fast"
_NO_ROS2 = "definitely-not-a-real-binary-xyz"


def _live_settings(*, dds_backend: str = "mock") -> Settings:
    return Settings(
        mode="live",
        log_level="INFO",
        ros2_executable="ros2",
        telemetry_enabled=False,
        dds_backend=dds_backend,  # type: ignore[arg-type]
        dds_domain_id=0,
    )


def _auto_settings(*, dds_backend: str) -> Settings:
    """`auto` mode on a host where `ros2` is not installed."""
    return Settings(
        mode="auto",
        log_level="INFO",
        ros2_executable=_NO_ROS2,
        telemetry_enabled=False,
        dds_backend=dds_backend,  # type: ignore[arg-type]
        dds_domain_id=7,
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


class _FakeCyclone(_StubDdsAdapter):
    """Stands in for `CycloneDdsAdapter`: records the domain it was built for."""

    def __init__(self, domain_id: int = 0) -> None:
        super().__init__()
        self.domain_id = domain_id


def _install_fake_adapter_module(
    monkeypatch: pytest.MonkeyPatch, module: str, cls_name: str, cls: Callable[..., object]
) -> None:
    """Make `from <module> import <cls_name>` resolve to `cls` without the real binding."""
    fake = types.ModuleType(module)
    setattr(fake, cls_name, cls)
    monkeypatch.setitem(sys.modules, module, fake)


# ---------------------------------------------------------------------------
# Branch 1: mock mode
# ---------------------------------------------------------------------------


def test_mock_mode_returns_mock_adapter() -> None:
    settings = Settings(
        mode="mock", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False
    )
    assert isinstance(factory.build_adapter(settings), MockAdapter)


def test_mock_mode_ignores_dds_backend_selection() -> None:
    """Global mock mode overrides DDS backend: MockAdapter serves everything."""
    settings = Settings(
        mode="mock",
        log_level="INFO",
        ros2_executable="ros2",
        telemetry_enabled=False,
        dds_backend="cyclone",
    )
    assert isinstance(factory.build_adapter(settings), MockAdapter)


def test_explicit_mock_mode_still_forces_mock_over_explicit_dds_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_adapter_module(monkeypatch, _CYCLONE_MODULE, "CycloneDdsAdapter", _FakeCyclone)
    settings = Settings(
        mode="mock",
        log_level="INFO",
        ros2_executable=_NO_ROS2,
        telemetry_enabled=False,
        dds_backend="cyclone",
    )
    assert isinstance(factory.build_adapter(settings), MockAdapter)


# ---------------------------------------------------------------------------
# Branch 5: final fallback when nothing live is reachable
# ---------------------------------------------------------------------------


def test_live_without_ros2_or_dds_falls_back_to_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Ros2CliAdapter, "is_available", lambda self: False)
    settings = _live_settings(dds_backend="mock")
    adapter = factory.build_adapter(settings)
    assert isinstance(adapter, MockAdapter)


# ---------------------------------------------------------------------------
# Branch 3: live + DDS backend mock -> Ros2CliAdapter alone
# ---------------------------------------------------------------------------


def test_live_with_dds_mock_returns_ros2_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Ros2CliAdapter, "is_available", lambda self: True)
    settings = _live_settings(dds_backend="mock")
    adapter = factory.build_adapter(settings)
    assert isinstance(adapter, Ros2CliAdapter)


# ---------------------------------------------------------------------------
# Branch 2a: composite (both halves up)
# ---------------------------------------------------------------------------


def test_composite_when_both_ros_and_dds_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Ros2CliAdapter, "is_available", lambda self: True)
    monkeypatch.setattr(factory, "_try_build_dds", lambda settings: _StubDdsAdapter())

    settings = _live_settings(dds_backend="cyclone")
    adapter = factory.build_adapter(settings)

    assert isinstance(adapter, CompositeAdapter)
    assert adapter.name == "ros2_cli+cyclone"


# ---------------------------------------------------------------------------
# Branch 2b: DDS binding missing, fall back to ROS2 CLI alone
# ---------------------------------------------------------------------------


def test_dds_missing_falls_back_to_ros2_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Ros2CliAdapter, "is_available", lambda self: True)
    monkeypatch.setattr(factory, "_try_build_dds", lambda settings: None)

    settings = _live_settings(dds_backend="cyclone")
    adapter = factory.build_adapter(settings)

    assert isinstance(adapter, Ros2CliAdapter)


# ---------------------------------------------------------------------------
# Branch 2c: ROS2 CLI missing, DDS up -> DDS adapter alone
# ---------------------------------------------------------------------------


def test_dds_only_when_ros2_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Ros2CliAdapter, "is_available", lambda self: False)
    stub = _StubDdsAdapter()
    monkeypatch.setattr(factory, "_try_build_dds", lambda settings: stub)

    settings = _live_settings(dds_backend="cyclone")
    adapter = factory.build_adapter(settings)

    assert adapter is stub  # DDS-only path, no composite wrapper.


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
        ros2_executable=_NO_ROS2,
        telemetry_enabled=False,
    )
    adapter = factory.build_adapter(settings)
    assert isinstance(adapter, MockAdapter)


def test_dds_auto_with_pyopendds_importable_still_builds_cyclone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`pyopendds` exists on PyPI; the stub adapter must not shadow Cyclone."""
    real_find_spec = importlib.util.find_spec
    present = {"pyopendds", "cyclonedds"}

    def fake_find_spec(name: str, *args: object, **kwargs: object) -> object | None:
        if name in ("pyopendds", "cyclonedds", "fastdds", "dust_dds_python"):
            return object() if name in present else None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    _install_fake_adapter_module(monkeypatch, _CYCLONE_MODULE, "CycloneDdsAdapter", _FakeCyclone)
    monkeypatch.setattr(Ros2CliAdapter, "is_available", lambda self: False)

    adapter = factory.build_adapter(_live_settings(dds_backend="auto"))

    assert isinstance(adapter, _FakeCyclone)


# ---------------------------------------------------------------------------
# Stub vendors: selectable explicitly, never serve
# ---------------------------------------------------------------------------


def test_opendds_backend_falls_back_to_ros2_cli_when_binding_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The OpenDdsAdapter stub reports unavailable and the factory falls back
    to ROS2 CLI alone."""
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


# ---------------------------------------------------------------------------
# Explicit DDS backend without ROS2 (the DDS-only user)
# ---------------------------------------------------------------------------


def test_auto_mode_with_explicit_cyclone_and_no_ros2_builds_cyclone_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_adapter_module(monkeypatch, _CYCLONE_MODULE, "CycloneDdsAdapter", _FakeCyclone)

    adapter = factory.build_adapter(_auto_settings(dds_backend="cyclone"))

    assert isinstance(adapter, _FakeCyclone)  # DDS-only: no composite, no mock
    assert adapter.domain_id == 7


def test_auto_mode_with_explicit_cyclone_and_missing_binding_warns_and_falls_back(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setitem(sys.modules, _CYCLONE_MODULE, None)  # forces ImportError

    with caplog.at_level(logging.WARNING, logger=factory.__name__):
        adapter = factory.build_adapter(_auto_settings(dds_backend="cyclone"))

    assert isinstance(adapter, MockAdapter)
    messages = [r.getMessage() for r in caplog.records]
    assert any("cyclonedds" in m and "topicforge[dds-cyclone]" in m for m in messages)


def test_auto_mode_with_explicit_fast_and_missing_binding_explains_the_build(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setitem(sys.modules, _FAST_MODULE, None)

    with caplog.at_level(logging.WARNING, logger=factory.__name__):
        adapter = factory.build_adapter(_auto_settings(dds_backend="fast"))

    assert isinstance(adapter, MockAdapter)
    messages = [r.getMessage() for r in caplog.records]
    assert any("not published on PyPI" in m and "eProsima" in m for m in messages)


@pytest.mark.parametrize("dds_backend", ["mock", "auto"])
def test_auto_mode_without_ros2_and_no_explicit_dds_backend_is_silent_mock(
    caplog: pytest.LogCaptureFixture, dds_backend: str
) -> None:
    with caplog.at_level(logging.WARNING, logger=factory.__name__):
        adapter = factory.build_adapter(_auto_settings(dds_backend=dds_backend))

    assert isinstance(adapter, MockAdapter)
    assert not caplog.records


# ---------------------------------------------------------------------------
# Constructors never escape `build_adapter`
# ---------------------------------------------------------------------------


def test_dds_constructor_raising_adapter_error_falls_back_to_mock(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """E.g. an invalid CYCLONEDDS_URI makes the DomainParticipant fail."""

    def boom(domain_id: int = 0) -> object:
        raise AdapterError("invalid CYCLONEDDS_URI")

    _install_fake_adapter_module(monkeypatch, _CYCLONE_MODULE, "CycloneDdsAdapter", boom)
    monkeypatch.setattr(Ros2CliAdapter, "is_available", lambda self: False)

    with caplog.at_level(logging.WARNING, logger=factory.__name__):
        adapter = factory.build_adapter(_live_settings(dds_backend="cyclone"))

    assert isinstance(adapter, MockAdapter)
    assert any("invalid CYCLONEDDS_URI" in r.getMessage() for r in caplog.records)


def test_dds_constructor_raising_adapter_error_keeps_ros2_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(domain_id: int = 0) -> object:
        raise AdapterError("cannot join domain")

    _install_fake_adapter_module(monkeypatch, _FAST_MODULE, "FastDdsAdapter", boom)
    monkeypatch.setattr(Ros2CliAdapter, "is_available", lambda self: True)

    adapter = factory.build_adapter(_live_settings(dds_backend="fast"))

    assert isinstance(adapter, Ros2CliAdapter)


def test_dds_constructor_raising_unexpected_error_is_logged_with_cause(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(domain_id: int = 0) -> object:
        raise OSError("shared library not found")

    _install_fake_adapter_module(monkeypatch, _CYCLONE_MODULE, "CycloneDdsAdapter", boom)
    monkeypatch.setattr(Ros2CliAdapter, "is_available", lambda self: False)

    with caplog.at_level(logging.WARNING, logger=factory.__name__):
        adapter = factory.build_adapter(_live_settings(dds_backend="cyclone"))

    assert isinstance(adapter, MockAdapter)
    assert any(
        "OSError" in r.getMessage() and "shared library not found" in r.getMessage()
        for r in caplog.records
    )


def test_dds_adapter_reporting_unavailable_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Unavailable(_FakeCyclone):
        def is_available(self) -> bool:
            return False

    _install_fake_adapter_module(monkeypatch, _CYCLONE_MODULE, "CycloneDdsAdapter", _Unavailable)
    monkeypatch.setattr(Ros2CliAdapter, "is_available", lambda self: True)

    adapter = factory.build_adapter(_live_settings(dds_backend="cyclone"))

    assert isinstance(adapter, Ros2CliAdapter)


def test_dds_binding_failing_to_load_falls_back_instead_of_crashing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A native library that fails to load raises OSError, not ImportError.

    Startup must degrade with a logged cause rather than abort.
    """
    import builtins

    real_import = builtins.__import__

    def failing_import(name, *args, **kwargs):  # type: ignore[no-untyped-def]
        if name == _CYCLONE_MODULE:
            raise OSError("cannot load library 'ddsc'")
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, _CYCLONE_MODULE, raising=False)
    monkeypatch.setattr(builtins, "__import__", failing_import)

    with caplog.at_level(logging.WARNING, logger=factory.__name__):
        adapter = factory.build_adapter(_auto_settings(dds_backend="cyclone"))

    assert isinstance(adapter, MockAdapter)
    assert any("OSError" in r.getMessage() for r in caplog.records)
