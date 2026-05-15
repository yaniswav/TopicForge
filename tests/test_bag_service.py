"""Tests for `topicforge.services.bag_service.BagService`.

Most tests are gated by `@pytest.mark.requires_rosbags` since they
need the rosbags library installed. A handful of pure-Python tests
(format detection, availability probe, error paths) run in the
default `make check`.
"""

from __future__ import annotations

import importlib.util

import pytest

from topicforge.adapters.base import AdapterError
from topicforge.services.bag_service import (
    BagService,
    detect_bag_format,
    is_rosbags_available,
)

# --------------------------------------------------------------------------
# Pure-Python — run in default make check
# --------------------------------------------------------------------------


def test_detect_bag_format_mcap() -> None:
    assert detect_bag_format("/tmp/demo.mcap") == "mcap"
    assert detect_bag_format("/tmp/RECORDING.MCAP") == "mcap"


def test_detect_bag_format_db3() -> None:
    assert detect_bag_format("/var/log/run.db3") == "db3"


def test_detect_bag_format_bag() -> None:
    """ROS1 legacy bag — extension-only detection."""
    assert detect_bag_format("/home/me/old.bag") == "bag"


def test_detect_bag_format_unknown() -> None:
    assert detect_bag_format("/tmp/notes.txt") == "unknown"
    assert detect_bag_format("/tmp/no_extension") == "unknown"


def test_is_rosbags_available_matches_find_spec() -> None:
    """The convenience wrapper should agree with `importlib.util.find_spec`."""
    expected = importlib.util.find_spec("rosbags") is not None
    assert is_rosbags_available() is expected


def test_bag_service_constructs_without_rosbags() -> None:
    """Constructor never raises — only methods raise when rosbags is absent."""
    BagService()


def test_bag_service_analyze_raises_clearly_without_rosbags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When rosbags is absent the AdapterError points at the install command."""
    from topicforge.services import bag_service

    monkeypatch.setattr(bag_service, "is_rosbags_available", lambda: False)
    svc = BagService()
    with pytest.raises(AdapterError, match="rosbags"):
        svc.analyze("/tmp/anything.mcap")


def test_bag_service_peek_raises_clearly_without_rosbags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from topicforge.services import bag_service

    monkeypatch.setattr(bag_service, "is_rosbags_available", lambda: False)
    svc = BagService()
    with pytest.raises(AdapterError, match="rosbags"):
        svc.peek_samples("/tmp/anything.mcap", "/topic", 5)


def test_bag_service_analyze_raises_on_nonexistent_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even with rosbags available, missing paths raise AdapterError."""
    from topicforge.services import bag_service

    monkeypatch.setattr(bag_service, "is_rosbags_available", lambda: True)
    svc = BagService()
    with pytest.raises(AdapterError, match="does not exist"):
        svc.analyze("/tmp/definitely-not-a-real-file-xyz.mcap")


def test_bag_service_peek_rejects_negative_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
) -> None:
    """`count < 0` short-circuits before touching the filesystem."""
    from topicforge.services import bag_service

    monkeypatch.setattr(bag_service, "is_rosbags_available", lambda: True)
    svc = BagService()
    # tmp_path-like dummy ; the count check happens before the path check
    with pytest.raises(AdapterError, match="count must be >= 0"):
        svc.peek_samples("/tmp/whatever.mcap", "/topic", -1)


# --------------------------------------------------------------------------
# requires_rosbags — auto-skipped without the library
# --------------------------------------------------------------------------


@pytest.mark.requires_rosbags
def test_bag_service_analyze_returns_enriched_bag_analysis_db3(
    tmp_path: pytest.TempPathFactory,
) -> None:  # pragma: no cover — exercised on rosbags-installed hosts
    """Generate a tiny ROS2 .db3 bag and assert analyze() returns enriched fields."""
    pytest.importorskip("rosbags")
    from rosbags.rosbag2 import Writer  # type: ignore[import-not-found]
    from rosbags.typesys import Stores, get_typestore  # type: ignore[import-not-found]

    bag_path = tmp_path / "test.db3"
    typestore = get_typestore(Stores.LATEST)
    string_msgtype = "std_msgs/msg/String"

    with Writer(bag_path) as writer:
        conn = writer.add_connection("/test_topic", string_msgtype, typestore=typestore)
        for i in range(5):
            msg = typestore.types[string_msgtype.replace("/", "__")](data=f"hello-{i}")
            writer.write(conn, i * 100_000_000, typestore.serialize_cdr(msg, string_msgtype))

    svc = BagService()
    analysis = svc.analyze(str(bag_path))
    assert analysis.bag_format == "db3"
    assert analysis.recording_duration_ns is not None
    assert analysis.message_count >= 5
    assert any(t.name == "/test_topic" for t in analysis.topics)
