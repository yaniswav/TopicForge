"""Tests for `topicforge.config.settings`."""

from __future__ import annotations

import pytest

from topicforge.config import load_settings


def test_defaults_when_env_empty() -> None:
    s = load_settings(env={})
    assert s.mode == "auto"
    assert s.log_level == "INFO"
    assert s.ros2_executable == "ros2"


def test_explicit_mock_mode() -> None:
    s = load_settings(env={"TOPICFORGE_MODE": "mock"})
    assert s.mode == "mock"
    assert s.effective_mode == "mock"


def test_explicit_live_mode_does_not_check_path() -> None:
    # `effective_mode` returns the requested mode as-is for "live"; the
    # factory is responsible for any fallback to mock.
    s = load_settings(env={"TOPICFORGE_MODE": "live"})
    assert s.effective_mode == "live"


def test_auto_mode_resolves_to_mock_when_executable_missing() -> None:
    s = load_settings(
        env={
            "TOPICFORGE_MODE": "auto",
            "TOPICFORGE_ROS2_BIN": "definitely-not-a-real-binary-xyz",
        }
    )
    assert s.effective_mode == "mock"


def test_invalid_mode_rejected() -> None:
    with pytest.raises(ValueError, match="TOPICFORGE_MODE"):
        load_settings(env={"TOPICFORGE_MODE": "bogus"})


def test_invalid_log_level_rejected() -> None:
    with pytest.raises(ValueError, match="TOPICFORGE_LOG_LEVEL"):
        load_settings(env={"TOPICFORGE_LOG_LEVEL": "shouty"})


def test_case_insensitive_inputs() -> None:
    s = load_settings(env={"TOPICFORGE_MODE": "MOCK", "TOPICFORGE_LOG_LEVEL": "debug"})
    assert s.mode == "mock"
    assert s.log_level == "DEBUG"


def test_empty_ros2_executable_defaults_to_ros2() -> None:
    s = load_settings(env={"TOPICFORGE_ROS2_BIN": "   "})
    assert s.ros2_executable == "ros2"


# ---------------------------------------------------------------------------
# DDS backend resolution (v0.2.0 added, v0.3.0 widened to include "fast")
# ---------------------------------------------------------------------------


def test_default_dds_backend_is_mock() -> None:
    s = load_settings(env={})
    assert s.dds_backend == "mock"
    assert s.dds_domain_id == 0


def test_explicit_dds_backend_mock() -> None:
    s = load_settings(env={"TOPICFORGE_DDS_BACKEND": "mock", "TOPICFORGE_MODE": "live"})
    assert s.effective_dds_backend == "mock"


def test_explicit_dds_backend_cyclone() -> None:
    s = load_settings(env={"TOPICFORGE_DDS_BACKEND": "cyclone", "TOPICFORGE_MODE": "live"})
    assert s.effective_dds_backend == "cyclone"


def test_explicit_dds_backend_fast() -> None:
    """v0.3.0: 'fast' is now an accepted value."""
    s = load_settings(env={"TOPICFORGE_DDS_BACKEND": "fast", "TOPICFORGE_MODE": "live"})
    assert s.effective_dds_backend == "fast"


def test_explicit_dds_backend_rti_returned_as_is() -> None:
    """rti is parsed even if no Pro adapter ships ; factory handles the fallback."""
    s = load_settings(env={"TOPICFORGE_DDS_BACKEND": "rti", "TOPICFORGE_MODE": "live"})
    assert s.effective_dds_backend == "rti"


def test_invalid_dds_backend_rejected() -> None:
    # v0.4.0 Phase 1.5: "opensplice" is now a valid backend value
    # (Pro-tier vendor). Pick a string that is unambiguously not a
    # known vendor to exercise the rejection path.
    with pytest.raises(ValueError, match="TOPICFORGE_DDS_BACKEND"):
        load_settings(env={"TOPICFORGE_DDS_BACKEND": "nonexistent_vendor"})


def test_explicit_dds_backend_opensplice_accepted() -> None:
    """v0.4.0 Phase 1.5: Pro-tier vendor names parse cleanly."""
    s = load_settings(env={"TOPICFORGE_DDS_BACKEND": "opensplice", "TOPICFORGE_MODE": "live"})
    assert s.effective_dds_backend == "opensplice"


def test_explicit_dds_backend_coredx_accepted() -> None:
    s = load_settings(env={"TOPICFORGE_DDS_BACKEND": "coredx", "TOPICFORGE_MODE": "live"})
    assert s.effective_dds_backend == "coredx"


def test_explicit_dds_backend_intercom_accepted() -> None:
    s = load_settings(env={"TOPICFORGE_DDS_BACKEND": "intercom", "TOPICFORGE_MODE": "live"})
    assert s.effective_dds_backend == "intercom"


def test_explicit_dds_backend_opendds_accepted() -> None:
    """OSS stub adapter — value still parses."""
    s = load_settings(env={"TOPICFORGE_DDS_BACKEND": "opendds", "TOPICFORGE_MODE": "live"})
    assert s.effective_dds_backend == "opendds"


def test_explicit_dds_backend_dust_accepted() -> None:
    s = load_settings(env={"TOPICFORGE_DDS_BACKEND": "dust", "TOPICFORGE_MODE": "live"})
    assert s.effective_dds_backend == "dust"


def test_dds_backend_mock_global_forces_dds_mock() -> None:
    """Global mock mode collapses every DDS backend to mock — no live access."""
    s = load_settings(env={"TOPICFORGE_MODE": "mock", "TOPICFORGE_DDS_BACKEND": "cyclone"})
    assert s.effective_dds_backend == "mock"


def test_dds_auto_prefers_fast_when_both_importable(monkeypatch: pytest.MonkeyPatch) -> None:
    """v0.3.0: auto prefers Fast over Cyclone when both are installed."""
    import importlib.util

    real_find_spec = importlib.util.find_spec

    def fake(name: str, *args: object, **kwargs: object) -> object | None:
        if name in ("fastdds", "cyclonedds"):
            return object()  # both importable
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake)
    s = load_settings(env={"TOPICFORGE_MODE": "live", "TOPICFORGE_DDS_BACKEND": "auto"})
    assert s.effective_dds_backend == "fast"


def test_dds_auto_falls_back_to_cyclone_when_only_cyclone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backward compat: v0.2.0 users with only cyclonedds installed still get cyclone."""
    import importlib.util

    real_find_spec = importlib.util.find_spec

    def fake(name: str, *args: object, **kwargs: object) -> object | None:
        if name == "fastdds":
            return None
        if name == "cyclonedds":
            return object()
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake)
    s = load_settings(env={"TOPICFORGE_MODE": "live", "TOPICFORGE_DDS_BACKEND": "auto"})
    assert s.effective_dds_backend == "cyclone"


def test_dds_auto_falls_back_to_mock_when_neither_importable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib.util

    real_find_spec = importlib.util.find_spec
    _AUTO_DETECT_MODULES = {
        "fastdds",
        "cyclonedds",
        "pyopendds",
        "dust_dds_python",
        "topicforge_pro.adapters.rti_connext",
        "topicforge_pro.adapters.opensplice",
        "topicforge_pro.adapters.coredx",
        "topicforge_pro.adapters.intercom",
    }

    def fake(name: str, *args: object, **kwargs: object) -> object | None:
        if name in _AUTO_DETECT_MODULES:
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake)
    s = load_settings(env={"TOPICFORGE_MODE": "live", "TOPICFORGE_DDS_BACKEND": "auto"})
    assert s.effective_dds_backend == "mock"


# ---------------------------------------------------------------------------
# Auto-detect priority chain (v0.4.0 Phase 1.5)
# Order: rti > opensplice > coredx > intercom (Pro) > opendds > fast >
# cyclone > dust > mock
# ---------------------------------------------------------------------------


def _patch_find_spec(monkeypatch: pytest.MonkeyPatch, present: set[str]) -> None:
    """Patch importlib.util.find_spec so only `present` modules look importable."""
    import importlib.util

    real_find_spec = importlib.util.find_spec
    _AUTO_DETECT_MODULES = {
        "fastdds",
        "cyclonedds",
        "pyopendds",
        "dust_dds_python",
        "topicforge_pro.adapters.rti_connext",
        "topicforge_pro.adapters.opensplice",
        "topicforge_pro.adapters.coredx",
        "topicforge_pro.adapters.intercom",
    }

    def fake(name: str, *args: object, **kwargs: object) -> object | None:
        if name in _AUTO_DETECT_MODULES:
            return object() if name in present else None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake)


def test_dds_auto_picks_rti_when_pro_rti_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pro tier RTI dominates everything else when its plugin is installed."""
    _patch_find_spec(
        monkeypatch,
        present={
            "topicforge_pro.adapters.rti_connext",
            "fastdds",
            "cyclonedds",
        },
    )
    s = load_settings(env={"TOPICFORGE_MODE": "live", "TOPICFORGE_DDS_BACKEND": "auto"})
    assert s.effective_dds_backend == "rti"


def test_dds_auto_picks_opensplice_over_oss(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_find_spec(
        monkeypatch,
        present={"topicforge_pro.adapters.opensplice", "fastdds", "cyclonedds"},
    )
    s = load_settings(env={"TOPICFORGE_MODE": "live", "TOPICFORGE_DDS_BACKEND": "auto"})
    assert s.effective_dds_backend == "opensplice"


def test_dds_auto_picks_coredx_over_remaining_oss(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_find_spec(
        monkeypatch,
        present={"topicforge_pro.adapters.coredx", "pyopendds", "fastdds"},
    )
    s = load_settings(env={"TOPICFORGE_MODE": "live", "TOPICFORGE_DDS_BACKEND": "auto"})
    assert s.effective_dds_backend == "coredx"


def test_dds_auto_picks_intercom_over_remaining_oss(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_find_spec(
        monkeypatch,
        present={"topicforge_pro.adapters.intercom", "fastdds"},
    )
    s = load_settings(env={"TOPICFORGE_MODE": "live", "TOPICFORGE_DDS_BACKEND": "auto"})
    assert s.effective_dds_backend == "intercom"


def test_dds_auto_picks_opendds_over_fast_and_cyclone(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_find_spec(monkeypatch, present={"pyopendds", "fastdds", "cyclonedds"})
    s = load_settings(env={"TOPICFORGE_MODE": "live", "TOPICFORGE_DDS_BACKEND": "auto"})
    assert s.effective_dds_backend == "opendds"


def test_dds_auto_picks_fast_over_cyclone_when_no_pro_no_opendds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserves v0.3.0 OSS internal order: Fast > Cyclone."""
    _patch_find_spec(monkeypatch, present={"fastdds", "cyclonedds"})
    s = load_settings(env={"TOPICFORGE_MODE": "live", "TOPICFORGE_DDS_BACKEND": "auto"})
    assert s.effective_dds_backend == "fast"


def test_dds_auto_picks_dust_only_when_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dust is lowest priority — only picked when no other OSS binding present."""
    _patch_find_spec(monkeypatch, present={"dust_dds_python"})
    s = load_settings(env={"TOPICFORGE_MODE": "live", "TOPICFORGE_DDS_BACKEND": "auto"})
    assert s.effective_dds_backend == "dust"


def test_dds_domain_id_parsing() -> None:
    s = load_settings(env={"TOPICFORGE_DDS_DOMAIN_ID": "42"})
    assert s.dds_domain_id == 42


def test_dds_domain_id_out_of_range_rejected() -> None:
    with pytest.raises(ValueError, match="TOPICFORGE_DDS_DOMAIN_ID"):
        load_settings(env={"TOPICFORGE_DDS_DOMAIN_ID": "300"})


def test_dds_domain_id_non_integer_rejected() -> None:
    with pytest.raises(ValueError, match="TOPICFORGE_DDS_DOMAIN_ID"):
        load_settings(env={"TOPICFORGE_DDS_DOMAIN_ID": "not-a-number"})
