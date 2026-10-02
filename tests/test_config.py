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
# DDS backend resolution
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
    """'fast' is an accepted value."""
    s = load_settings(env={"TOPICFORGE_DDS_BACKEND": "fast", "TOPICFORGE_MODE": "live"})
    assert s.effective_dds_backend == "fast"


def test_invalid_dds_backend_rejected() -> None:
    with pytest.raises(ValueError, match="TOPICFORGE_DDS_BACKEND"):
        load_settings(env={"TOPICFORGE_DDS_BACKEND": "nonexistent_vendor"})


@pytest.mark.parametrize("vendor", ["rti", "opensplice", "coredx", "intercom", "RTI"])
def test_removed_pro_vendors_rejected_with_explicit_message(vendor: str) -> None:
    """Removed vendor names are rejected with a message that points at the cyclone backend."""
    with pytest.raises(ValueError) as excinfo:
        load_settings(env={"TOPICFORGE_DDS_BACKEND": vendor, "TOPICFORGE_MODE": "live"})
    message = str(excinfo.value)
    assert "TOPICFORGE_DDS_BACKEND" in message
    assert "removed in 0.5.3" in message
    assert "cyclone" in message


def test_explicit_dds_backend_opendds_accepted() -> None:
    """Stub adapter: still selectable explicitly."""
    s = load_settings(env={"TOPICFORGE_DDS_BACKEND": "opendds", "TOPICFORGE_MODE": "live"})
    assert s.effective_dds_backend == "opendds"


def test_explicit_dds_backend_dust_accepted() -> None:
    s = load_settings(env={"TOPICFORGE_DDS_BACKEND": "dust", "TOPICFORGE_MODE": "live"})
    assert s.effective_dds_backend == "dust"


def test_dds_backend_mock_global_forces_dds_mock() -> None:
    """Global mock mode collapses every DDS backend to mock: no live access."""
    s = load_settings(env={"TOPICFORGE_MODE": "mock", "TOPICFORGE_DDS_BACKEND": "cyclone"})
    assert s.effective_dds_backend == "mock"


def test_dds_auto_prefers_fast_when_both_importable(monkeypatch: pytest.MonkeyPatch) -> None:
    """auto prefers Fast over Cyclone when both are installed."""
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
    """auto picks Cyclone when it is the only binding installed."""
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
    }

    def fake(name: str, *args: object, **kwargs: object) -> object | None:
        if name in _AUTO_DETECT_MODULES:
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake)
    s = load_settings(env={"TOPICFORGE_MODE": "live", "TOPICFORGE_DDS_BACKEND": "auto"})
    assert s.effective_dds_backend == "mock"


# ---------------------------------------------------------------------------
# Auto-detect priority chain: fast > cyclone > mock. The opendds and dust
# stubs are never auto-selected (their is_available() is always False).
# ---------------------------------------------------------------------------


def _patch_find_spec(monkeypatch: pytest.MonkeyPatch, present: set[str]) -> None:
    """Patch importlib.util.find_spec so only `present` modules look importable."""
    import importlib.util

    real_find_spec = importlib.util.find_spec
    _AUTO_DETECT_MODULES = {"fastdds", "cyclonedds", "pyopendds", "dust_dds_python"}

    def fake(name: str, *args: object, **kwargs: object) -> object | None:
        if name in _AUTO_DETECT_MODULES:
            return object() if name in present else None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake)


def test_dds_auto_picks_fast_over_cyclone(monkeypatch: pytest.MonkeyPatch) -> None:
    """auto order is Fast, then Cyclone."""
    _patch_find_spec(monkeypatch, present={"fastdds", "cyclonedds"})
    s = load_settings(env={"TOPICFORGE_MODE": "live", "TOPICFORGE_DDS_BACKEND": "auto"})
    assert s.effective_dds_backend == "fast"


def test_dds_auto_skips_opendds_stub_when_cyclone_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`pyopendds` exists on PyPI but the adapter is a stub: auto must reach Cyclone."""
    _patch_find_spec(monkeypatch, present={"pyopendds", "cyclonedds"})
    s = load_settings(env={"TOPICFORGE_MODE": "live", "TOPICFORGE_DDS_BACKEND": "auto"})
    assert s.effective_dds_backend == "cyclone"


def test_dds_auto_never_picks_stubs_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_find_spec(monkeypatch, present={"pyopendds", "dust_dds_python"})
    s = load_settings(env={"TOPICFORGE_MODE": "live", "TOPICFORGE_DDS_BACKEND": "auto"})
    assert s.effective_dds_backend == "mock"


# ---------------------------------------------------------------------------
# Explicit DDS backend without ROS2 in `auto` mode
# ---------------------------------------------------------------------------

_NO_ROS2 = "definitely-not-a-real-binary-xyz"


@pytest.mark.parametrize("backend", ["cyclone", "fast"])
def test_auto_mode_without_ros2_keeps_explicit_dds_backend(backend: str) -> None:
    """DDS-only users: naming a backend must not be overridden by the missing ros2."""
    s = load_settings(
        env={
            "TOPICFORGE_MODE": "auto",
            "TOPICFORGE_ROS2_BIN": _NO_ROS2,
            "TOPICFORGE_DDS_BACKEND": backend,
        }
    )
    assert s.effective_mode == "mock"
    assert s.effective_dds_backend == backend


@pytest.mark.parametrize("backend", ["mock", "auto"])
def test_auto_mode_without_ros2_and_no_explicit_backend_stays_mock(backend: str) -> None:
    s = load_settings(
        env={
            "TOPICFORGE_MODE": "auto",
            "TOPICFORGE_ROS2_BIN": _NO_ROS2,
            "TOPICFORGE_DDS_BACKEND": backend,
        }
    )
    assert s.effective_dds_backend == "mock"


def test_dds_domain_id_parsing() -> None:
    s = load_settings(env={"TOPICFORGE_DDS_DOMAIN_ID": "42"})
    assert s.dds_domain_id == 42


def test_dds_domain_id_out_of_range_rejected() -> None:
    with pytest.raises(ValueError, match="TOPICFORGE_DDS_DOMAIN_ID"):
        load_settings(env={"TOPICFORGE_DDS_DOMAIN_ID": "300"})


def test_dds_domain_id_non_integer_rejected() -> None:
    with pytest.raises(ValueError, match="TOPICFORGE_DDS_DOMAIN_ID"):
        load_settings(env={"TOPICFORGE_DDS_DOMAIN_ID": "not-a-number"})


def test_dds_auto_without_ros2_still_walks_the_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    """`TOPICFORGE_DDS_BACKEND=auto` is never the default (that is `mock`).

    Setting it is an explicit request for DDS, so a DDS-only host without the
    `ros2` CLI must get the detected binding, not fixtures.
    """
    _patch_find_spec(monkeypatch, present={"cyclonedds"})
    s = load_settings(
        env={
            "TOPICFORGE_MODE": "auto",
            "TOPICFORGE_DDS_BACKEND": "auto",
            "TOPICFORGE_ROS2_BIN": _NO_ROS2,
        }
    )
    assert s.effective_mode == "mock"  # no ROS2 CLI on this host
    assert s.effective_dds_backend == "cyclone"


def test_explicit_mock_mode_still_wins_over_dds_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_find_spec(monkeypatch, present={"cyclonedds"})
    s = load_settings(env={"TOPICFORGE_MODE": "mock", "TOPICFORGE_DDS_BACKEND": "auto"})
    assert s.effective_dds_backend == "mock"
