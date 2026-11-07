"""Anomaly detection in `analyze_bag`: injected faults are found, healthy bags give nothing.

Bags are generated with `rosbags` (`tests/bag_builder.py`), so the module is sdist-safe. A fault
test checks the topic or edge, the offset and the size named in the string; the false-alarm
tests check that jitter, slow, bursty, latched and built-in topics stay silent.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from tests.bag_builder import BASE, NS, Series, periodic, without, write_bag
from topicforge.adapters.common import bag_read
from topicforge.adapters.ros2_live import Ros2CliAdapter

pytest.importorskip("rosbags")
pytestmark = pytest.mark.requires_rosbags

OMNISIM = Path(__file__).parent / "fixtures" / "bags" / "omnisim_humble"
SECONDS = 30
EDGES = [("odom", "base_link"), ("base_link", "lidar")]


def _healthy(*, seed: int = 7) -> dict[str, Series]:
    """A 30 s robot bag: jittery steady topics, a TF with two edges, a clock."""
    rng = random.Random(seed)

    def jitter(i: int) -> int:
        return rng.randint(-3_000_000, 3_000_000)

    return {
        "scan": Series("/scan", "stamped", periodic(BASE, 0.1, 10 * SECONDS, jitter)),
        "odom": Series("/odom", "stamped", periodic(BASE, 0.05, 20 * SECONDS, jitter)),
        "imu": Series("/imu", "stamped", periodic(BASE, 0.05, 20 * SECONDS, jitter)),
        "tf": Series("/tf", "tf", periodic(BASE, 0.02, 50 * SECONDS, jitter), edges=EDGES),
        "clock": Series("/clock", "clock", periodic(BASE, 0.02, 50 * SECONDS)),
    }


def _analyze(
    tmp_path: Path, series: dict[str, Series], storage: str = "mcap"
) -> bag_read.BagAnalysis:
    bag = write_bag(tmp_path / f"bag_{storage}", list(series.values()), storage=storage)
    return bag_read.analyze_bag_file(str(bag))


def _only(analysis: bag_read.BagAnalysis, fragment: str) -> str:
    hits = [a for a in analysis.anomalies if fragment in a]
    assert len(hits) == 1, analysis.anomalies
    return hits[0]


# ---- no false alarm ---------------------------------------------------------------


@pytest.mark.parametrize("storage", ["mcap", "sqlite3"])
def test_a_healthy_bag_with_jitter_has_no_anomaly(tmp_path: Path, storage: str) -> None:
    analysis = _analyze(tmp_path, _healthy(), storage)
    assert analysis.anomalies == [] and analysis.note is None


def test_slow_bursty_latched_and_builtin_topics_stay_silent(tmp_path: Path) -> None:
    rng = random.Random(3)
    burst: list[int] = []
    t = BASE
    for _ in range(60):  # event-driven: exponential gaps, some of them long
        t += int(rng.expovariate(1 / 0.4) * NS)
        burst.append(t)
    series = _healthy()
    bursts = [BASE + i * 6 * NS + k * 100_000_000 for i in range(8) for k in range(5)]
    series["bursts"] = Series("/trigger", "string", bursts)  # 5 messages, then a long silence
    series["slow"] = Series("/battery", "stamped", periodic(BASE, 2.0, 40))  # steady, 0.5 Hz
    series["burst"] = Series("/events", "string", burst)
    latched = without(periodic(BASE, 0.5, 50), BASE + 8 * NS, BASE + 18 * NS)
    series["latched"] = Series("/map", "string", latched, latched=True)
    series["rosout"] = Series(
        "/rosout", "string", without(periodic(BASE, 0.1, 200), BASE + 5 * NS, BASE + 9 * NS)
    )
    series["static"] = Series("/tf_static", "tf", periodic(BASE, 1.0, 20), edges=[("map", "odom")])
    analysis = _analyze(tmp_path, series)
    assert analysis.anomalies == []


def test_a_topic_with_too_few_messages_is_not_judged(tmp_path: Path) -> None:
    series = _healthy()
    series["few"] = Series(
        "/rare", "stamped", without(periodic(BASE, 0.1, 25), BASE + 1 * NS, BASE + 2 * NS)
    )
    assert _analyze(tmp_path, series).anomalies == []


# ---- injected faults ------------------------------------------------------------------


def test_a_clock_that_jumps_backward(tmp_path: Path) -> None:
    series = _healthy()
    clock = series["clock"]
    stamps = [t - (20 * NS if t >= BASE + 15 * NS else 0) for t in clock.times]
    series["clock"] = Series("/clock", "clock", clock.times, stamps=stamps)
    line = _only(_analyze(tmp_path, series), "/clock jumped backward")
    assert "19.98 s" in line and "t=15.0" in line


def test_a_clock_that_jumps_forward(tmp_path: Path) -> None:
    series = _healthy()
    clock = series["clock"]
    stamps = [t + (60 * NS if t >= BASE + 15 * NS else 0) for t in clock.times]
    series["clock"] = Series("/clock", "clock", clock.times, stamps=stamps)
    line = _only(_analyze(tmp_path, series), "/clock jumped forward")
    assert "60.0" in line and "t=15.0" in line and "usual step 20 ms" in line


def test_a_tf_edge_that_goes_silent(tmp_path: Path) -> None:
    series = _healthy()
    silent = (BASE + 10 * NS, BASE + 13 * NS)

    def edges(t: int) -> list[tuple[str, str]]:
        return [EDGES[0]] if silent[0] <= t < silent[1] else EDGES

    series["tf"] = Series("/tf", "tf", series["tf"].times, edges=edges)
    analysis = _analyze(tmp_path, series)
    line = _only(analysis, "base_link->lidar")
    assert line.startswith("/tf base_link->lidar: no TF update for 3.0")
    assert "t=10.0" in line and "t=13.0" in line and "usual period 20 ms" in line
    assert not any("odom->base_link" in a for a in analysis.anomalies)


def test_a_steady_topic_that_loses_messages(tmp_path: Path) -> None:
    series = _healthy()
    scan = series["scan"]
    series["scan"] = Series(
        "/scan", "stamped", without(scan.times, BASE + 10 * NS, BASE + 12_500_000_000)
    )
    line = _only(_analyze(tmp_path, series), "/scan: no messages")
    assert "for 2.5" in line or "for 2.6" in line
    assert "t=9.9" in line or "t=10.0" in line
    assert "usual period 100 ms" in line and "missing" in line


def test_a_silence_shared_by_many_topics_is_one_stall(tmp_path: Path) -> None:
    gap = (BASE + 10 * NS, BASE + 13 * NS)
    series = {
        k: Series(s.topic, s.kind, without(s.times, *gap), edges=s.edges, stamps=None)
        for k, s in _healthy().items()
    }
    analysis = _analyze(tmp_path, series)
    assert len(analysis.anomalies) == 1, analysis.anomalies
    assert "steady topics silent together" in analysis.anomalies[0]
    assert "t=10.0" in analysis.anomalies[0] and "paused simulation" in analysis.anomalies[0]


def test_a_header_stamp_that_goes_backward(tmp_path: Path) -> None:
    series = _healthy()
    odom = series["odom"]
    stamps = [t - (8 * NS if t >= BASE + 20 * NS else 0) for t in odom.times]
    series["odom"] = Series("/odom", "stamped", odom.times, stamps=stamps)
    line = _only(_analyze(tmp_path, series), "/odom: header stamp went backward")
    assert "7.95 s" in line and "t=20.0" in line and "sampled messages" in line


# ---- what the note says -------------------------------------------------------------


def test_a_cut_scan_says_so_and_keeps_exact_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bag = write_bag(tmp_path / "bag", list(_healthy().values()), storage="mcap")
    monkeypatch.setattr(bag_read, "BAG_SCAN_MAX_SECONDS", -1.0)
    analysis = bag_read.analyze_bag_file(str(bag))
    assert analysis.note is not None
    assert "time budget" in analysis.note and "ruled out" in analysis.note
    assert "count / bag duration" in analysis.note
    assert analysis.message_count == 300 + 600 + 600 + 1500 + 1500
    assert {t.frequency_basis for t in analysis.topics} == {"bag_duration"}


def test_a_cut_scan_keeps_exact_db3_spans(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bag = write_bag(tmp_path / "bag", list(_healthy().values()), storage="sqlite3")
    monkeypatch.setattr(bag_read, "BAG_SCAN_MAX_SECONDS", -1.0)
    analysis = bag_read.analyze_bag_file(str(bag))
    assert analysis.note is not None and "time budget" in analysis.note
    assert {t.frequency_basis for t in analysis.topics} == {"topic_span"}


def test_a_bag_rosbags_cannot_open_says_anomalies_were_not_checked(tmp_path: Path) -> None:
    broken = tmp_path / "broken.mcap"
    broken.write_bytes(b"not a bag at all")
    scan = bag_read.scan_bag(broken)
    assert scan.anomalies == [] and scan.spans is None
    assert scan.note is not None and "not checked" in scan.note


def test_without_rosbags_the_cli_path_says_anomalies_were_not_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bag = write_bag(tmp_path / "bag", list(_healthy().values()), storage="mcap")
    monkeypatch.setattr(bag_read, "is_rosbags_available", lambda: False)
    scan = bag_read.scan_bag(bag)
    assert scan.note is not None and "not checked" in scan.note and "not installed" in scan.note


# ---- the CLI path -----------------------------------------------------------------------


def test_the_cli_path_reports_the_same_anomalies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not OMNISIM.exists():
        pytest.skip("recorded bag left out of the sdist")
    series = _healthy()
    scan = series["scan"]
    series["scan"] = Series("/scan", "stamped", without(scan.times, BASE + 10 * NS, BASE + 13 * NS))
    bag = write_bag(tmp_path / "bag", list(series.values()), storage="mcap")
    adapter = Ros2CliAdapter()
    monkeypatch.setattr(
        adapter, "_run", lambda *a, **k: (OMNISIM / "ros2_bag_info.txt").read_text()
    )
    analysis = adapter.analyze_bag(str(bag))
    assert analysis.reader == "ros2_cli"
    assert any(a.startswith("/scan: no messages") for a in analysis.anomalies)
