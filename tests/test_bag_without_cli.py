"""Bag tools without the `ros2` CLI: routing, container fields, errors and agreement with the CLI.

The bags are generated with `rosbags`, so nothing here needs ROS 2 and the module is safe in
the sdist; the omnisim comparison skips where the recorded fixture is left out.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.bag_builder import BASE, Series, periodic, write_bag
from topicforge.adapters.base import AdapterError
from topicforge.adapters.common import bag_read
from topicforge.adapters.common.bag_container import describe_container
from topicforge.adapters.ros2_live import Ros2CliAdapter
from topicforge.adapters.ros2_live.parsers import parse_bag_info
from topicforge.adapters.ros2_mock import MockAdapter
from topicforge.config import Settings
from topicforge.services import factory

pytest.importorskip("rosbags")
pytestmark = pytest.mark.requires_rosbags

_NO_ROS2 = "topicforge-no-such-ros2-binary"
OMNISIM = Path(__file__).parent / "fixtures" / "bags" / "omnisim_humble"


def _series() -> list[Series]:
    return [
        Series("/scan", "stamped", periodic(BASE, 0.1, 50)),
        Series("/note", "string", periodic(BASE + 1, 0.5, 8)),
    ]


@pytest.fixture(params=["mcap", "sqlite3"])
def bag(request: pytest.FixtureRequest, tmp_path: Path) -> Path:
    return write_bag(tmp_path / "bag", _series(), storage=request.param)


def _settings(mode: str) -> Settings:
    return Settings(
        mode=mode,  # type: ignore[arg-type]
        log_level="INFO",
        ros2_executable=_NO_ROS2,
        telemetry_enabled=False,
    )


# ---- container fields ---------------------------------------------------------


def test_directory_and_file_report_their_container(tmp_path: Path) -> None:
    for storage, fmt, name in (("mcap", "mcap", "mcap"), ("sqlite3", "db3", "sqlite3")):
        directory = write_bag(tmp_path / storage, _series(), storage=storage)
        analysis = bag_read.analyze_bag_file(str(directory))
        assert (analysis.bag_format, analysis.storage_format) == (fmt, name)
        assert analysis.reader == "rosbags"
        assert analysis.message_count == 58
        inner = next(directory.glob(f"*.{'mcap' if storage == 'mcap' else 'db3'}"))
        assert describe_container(inner) == (fmt, name)


def test_counts_and_times_agree_between_storages(tmp_path: Path) -> None:
    results = [
        bag_read.analyze_bag_file(str(write_bag(tmp_path / s, _series(), storage=s)))
        for s in ("mcap", "sqlite3")
    ]
    a, b = results
    assert a.duration_s == pytest.approx(b.duration_s, abs=1e-6)
    assert {t.name: t.message_count for t in a.topics} == {
        t.name: t.message_count for t in b.topics
    }
    assert a.topics[0].frequency_basis == "topic_span"


# ---- errors ---------------------------------------------------------------------


def test_a_directory_without_metadata_is_a_clear_error(tmp_path: Path) -> None:
    (tmp_path / "x_0.db3").write_bytes(b"")
    with pytest.raises(AdapterError, match=r"metadata.yaml"):
        bag_read.analyze_bag_file(str(tmp_path))
    with pytest.raises(AdapterError, match=r"metadata.yaml"):
        bag_read.peek_bag_samples(str(tmp_path), "/scan", 1)


def test_a_bare_zstd_file_is_a_clear_error(tmp_path: Path) -> None:
    packed = tmp_path / "bag_0.mcap.zstd"
    packed.write_bytes(b"\x28\xb5\x2f\xfd")
    with pytest.raises(AdapterError, match="zstd"):
        bag_read.analyze_bag_file(str(packed))
    with pytest.raises(AdapterError, match="zstd"):
        bag_read.peek_bag_samples(str(packed), "/scan", 1)


# ---- routing ----------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["auto", "live"])
def test_a_fallback_mock_reads_a_real_bag(bag: Path, mode: str) -> None:
    adapter = factory.build_adapter(_settings(mode))
    assert isinstance(adapter, MockAdapter)
    analysis = adapter.analyze_bag(str(bag))
    assert (analysis.reader, analysis.message_count, analysis.mode_effective) == (
        "rosbags",
        58,
        "live",
    )
    samples = adapter.peek_bag_samples(str(bag), "/scan", 3)
    assert samples.count == 3 and samples.mode_effective == "live"


def test_explicit_mock_keeps_fixtures_even_for_a_real_bag(bag: Path) -> None:
    adapter = factory.build_adapter(_settings("mock"))
    analysis = adapter.analyze_bag(str(bag))
    assert analysis.reader == "fixture" and analysis.message_count == 1298
    assert analysis.note is None
    assert adapter.peek_bag_samples(str(bag), "/cmd_vel", 2).mode_effective == "mock"


def test_a_fallback_mock_answers_a_missing_path_with_a_labelled_fixture(tmp_path: Path) -> None:
    adapter = factory.build_adapter(_settings("auto"))
    analysis = adapter.analyze_bag(str(tmp_path / "gone.mcap"))
    assert analysis.reader == "fixture"
    assert analysis.note is not None and "not your bag" in analysis.note
    peek = adapter.peek_bag_samples(str(tmp_path / "gone.mcap"), "/cmd_vel", 1)
    assert peek.note is not None and "not your bag" in peek.note


def test_the_cli_failing_falls_back_to_rosbags(bag: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = Ros2CliAdapter()

    def boom(*_a: object, **_k: object) -> str:
        raise AdapterError("ros2 bag info exited 1")

    monkeypatch.setattr(adapter, "_run", boom)
    analysis = adapter.analyze_bag(str(bag))
    assert analysis.reader == "rosbags" and analysis.message_count == 58
    assert (
        analysis.note is not None
        and "ros2 bag info" in analysis.note
        and "rosbags" in analysis.note
    )


def test_both_readers_failing_names_both_reasons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = tmp_path / "bad.mcap"
    bad.write_bytes(b"not a bag")
    adapter = Ros2CliAdapter()

    def boom(*_a: object, **_k: object) -> str:
        raise AdapterError("cli says no")

    monkeypatch.setattr(adapter, "_run", boom)
    with pytest.raises(AdapterError, match=r"cli says no.*rosbags"):
        adapter.analyze_bag(str(bad))


def test_the_cli_path_keeps_its_numbers_and_adds_the_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not OMNISIM.exists():
        pytest.skip("recorded bag left out of the sdist")
    info = (OMNISIM / "ros2_bag_info.txt").read_text()
    adapter = Ros2CliAdapter()
    monkeypatch.setattr(adapter, "_run", lambda *a, **k: info)
    analysis = adapter.analyze_bag(str(OMNISIM))
    assert analysis.reader == "ros2_cli"
    assert (analysis.bag_format, analysis.storage_format) == ("db3", "sqlite3")
    assert analysis.message_count == 1434 and analysis.anomalies == []


# ---- agreement with the CLI on the recorded bag -----------------------------------


def test_omnisim_rosbags_matches_the_cli_figures() -> None:
    if not OMNISIM.exists():
        pytest.skip("recorded bag left out of the sdist")
    cli = parse_bag_info(
        (OMNISIM / "ros2_bag_info.txt").read_text(), fallback_path="x", mode_effective="live"
    )
    mine = bag_read.analyze_bag_file(str(OMNISIM))
    assert mine.message_count == cli.message_count == 1434
    assert abs(mine.duration_s - cli.duration_s) < 1e-6
    assert {t.name: t.message_count for t in mine.topics} == {
        t.name: t.message_count for t in cli.topics
    }
    assert mine.storage_format == cli.storage_format == "sqlite3"
    assert mine.anomalies == [] and mine.note is None
