"""Tool-level tests for `peek_bag_samples` (11th MCP tool, v0.4.0 Phase 3).

Exercises the Inspector + MockAdapter path against the deterministic
mock bag fixtures (`MOCK_BAG_SAMPLES` in
`adapters/ros2_mock/fixtures.py`).
"""

from __future__ import annotations

import pytest

from topicforge.adapters.base import AdapterError
from topicforge.models import SampleResult
from topicforge.services import Inspector

# ---------------------------------------------------------------------------
# Shape + happy path
# ---------------------------------------------------------------------------


def test_peek_bag_samples_returns_sample_result_shape(inspector: Inspector) -> None:
    result = inspector.peek_bag_samples("/tmp/demo.mcap", "/cmd_vel", count=3)
    assert isinstance(result, SampleResult)
    assert result.topic == "/cmd_vel"
    assert result.mode_effective == "mock"
    assert result.count == 3
    assert len(result.samples) == 3


def test_peek_bag_samples_carries_decode_status_full(inspector: Inspector) -> None:
    result = inspector.peek_bag_samples("/tmp/demo.mcap", "/cmd_vel", count=2)
    for sample in result.samples:
        assert sample.payload.get("_decode_status") == "full"
        # Decoded fields land at top level alongside the annotation.
        assert "linear" in sample.payload


def test_peek_bag_samples_unknown_topic_returns_empty(inspector: Inspector) -> None:
    """Mirror `peek_dds_samples` semantics: unknown topic → empty result."""
    result = inspector.peek_bag_samples("/tmp/demo.mcap", "/never/recorded", count=5)
    assert result.count == 0
    assert result.samples == []


def test_peek_bag_samples_db3_extension_accepted(inspector: Inspector) -> None:
    result = inspector.peek_bag_samples("/var/log/run.db3", "/odom", count=2)
    assert result.count == 2


def test_peek_bag_samples_ros1_extension_accepted(inspector: Inspector) -> None:
    result = inspector.peek_bag_samples("/home/me/legacy.bag", "/cmd_vel", count=1)
    assert result.count == 1


def test_peek_bag_samples_clamps_count_at_50(inspector: Inspector) -> None:
    """The fixture has < 50 samples ; this exercises the clamp path
    without hitting the underlying cap directly."""
    result = inspector.peek_bag_samples("/tmp/demo.mcap", "/cmd_vel", count=100)
    # MOCK_BAG_SAMPLES["/cmd_vel"] has 5 entries
    assert result.count == 5


# ---------------------------------------------------------------------------
# Validation paths
# ---------------------------------------------------------------------------


def test_peek_bag_samples_rejects_negative_count(inspector: Inspector) -> None:
    with pytest.raises(AdapterError, match="count must be >= 0"):
        inspector.peek_bag_samples("/tmp/demo.mcap", "/cmd_vel", count=-1)


def test_peek_bag_samples_rejects_blank_path(inspector: Inspector) -> None:
    with pytest.raises(AdapterError, match="non-empty"):
        inspector.peek_bag_samples("   ", "/cmd_vel", count=5)


def test_peek_bag_samples_rejects_null_byte_path(inspector: Inspector) -> None:
    with pytest.raises(AdapterError, match="null bytes"):
        inspector.peek_bag_samples("/tmp/foo\x00.mcap", "/cmd_vel", count=5)


def test_peek_bag_samples_rejects_non_bag_extension(inspector: Inspector) -> None:
    """The mock adapter rejects non-bag extensions (`/tmp/note.txt`)."""
    with pytest.raises(AdapterError, match="does not look like a ROS2 bag"):
        inspector.peek_bag_samples("/tmp/note.txt", "/cmd_vel", count=5)


def test_peek_bag_samples_rejects_malformed_topic(inspector: Inspector) -> None:
    with pytest.raises(AdapterError, match="DDS topic name"):
        inspector.peek_bag_samples("/tmp/demo.mcap", "bad topic name", count=5)
