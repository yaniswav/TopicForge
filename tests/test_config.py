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
