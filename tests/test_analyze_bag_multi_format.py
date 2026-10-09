"""The mock bag analysis carries `bag_format`, `samples_decoded_count`,
`recording_duration_ns` and `participants_recorded`; the fields are optional."""

from __future__ import annotations

from topicforge.adapters.ros2_mock import MockAdapter


def test_mock_analyze_bag_returns_enriched_fields() -> None:
    adapter = MockAdapter()
    result = adapter.analyze_bag("/tmp/demo.mcap")
    # Original fields.
    assert result.path == "/tmp/demo.mcap"
    assert result.storage_format == "mcap"
    assert result.message_count == 1287
    # Added fields.
    assert result.bag_format == "mcap"
    assert result.recording_duration_ns == 42_500_000_000
    assert result.samples_decoded_count == 0
    assert result.participants_recorded == []


def test_mock_analyze_bag_path_echo() -> None:
    """model_copy(update=path): the mock fixture's path field reflects the request."""
    adapter = MockAdapter()
    paths = [
        "/tmp/a.mcap",
        "/var/log/run.db3",
        "/home/me/legacy.bag",
    ]
    for path in paths:
        result = adapter.analyze_bag(path)
        assert result.path == path


def test_bag_analysis_schema_defaults_when_constructed_minimally() -> None:
    """Construction without the four added fields succeeds."""
    from topicforge.models import BagAnalysis, BagTopicStats

    legacy = BagAnalysis(
        path="/tmp/legacy.bag",
        storage_format=None,
        duration_s=10.0,
        message_count=100,
        topics=[
            BagTopicStats(
                name="/x",
                message_type="t/Y",
                message_count=100,
                frequency_hz=10.0,
            )
        ],
        mode_effective="live",
    )
    assert legacy.bag_format is None  # safe default
    assert legacy.samples_decoded_count == 0
    assert legacy.recording_duration_ns is None
    assert legacy.participants_recorded == []


def test_bag_analysis_schema_accepts_db3_format() -> None:
    from topicforge.models import BagAnalysis

    analysis = BagAnalysis(
        path="/tmp/x.db3",
        storage_format="sqlite3",
        duration_s=5.0,
        message_count=50,
        topics=[],
        mode_effective="live",
        bag_format="db3",
        recording_duration_ns=5_000_000_000,
    )
    assert analysis.bag_format == "db3"


def test_bag_analysis_schema_accepts_ros1_bag_format() -> None:
    from topicforge.models import BagAnalysis

    analysis = BagAnalysis(
        path="/tmp/legacy.bag",
        storage_format="bag",
        duration_s=2.0,
        message_count=20,
        topics=[],
        mode_effective="live",
        bag_format="bag",
    )
    assert analysis.bag_format == "bag"


def test_bag_analysis_schema_accepts_unknown_format() -> None:
    """`unknown` is the explicit "detected but couldn't classify" tag."""
    from topicforge.models import BagAnalysis

    analysis = BagAnalysis(
        path="/tmp/x.weird",
        storage_format=None,
        duration_s=0.0,
        message_count=0,
        topics=[],
        mode_effective="live",
        bag_format="unknown",
    )
    assert analysis.bag_format == "unknown"
