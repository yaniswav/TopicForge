"""Tests for scripts/ground_truth (the ground-truth comparator and its driver).

No ROS and no MCP server: the comparator reads saved result files, so the tests feed it

- a trimmed copy of a real run (OmniSim kit, TopicForge 0.6.4, `tests/fixtures/ground_truth`),
- a synthetic, perfect contract-2 answer set for the Docker bench's own truth,
- corrupted copies of both, which must fail with exit code 1.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "ground_truth"


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


compare = _load("gt_compare", ROOT / "scripts" / "ground_truth" / "compare.py")
drive = _load("gt_drive", ROOT / "scripts" / "ground_truth" / "drive.py")
bench = _load("gt_bench", ROOT / "tests" / "integration" / "ros2" / "ground_truth.py")


def _write_results(folder: Path, results: dict[str, Any]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in results.items():
        (folder / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")
    return folder


def _run(tmp_path: Path, truth: dict, results: dict, *extra: str) -> tuple[int, dict]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    truth_file = tmp_path / "ground_truth.json"
    truth_file.write_text(json.dumps(truth), encoding="utf-8")
    folder = _write_results(tmp_path / "results", results)
    code = compare.main(["--truth", str(truth_file), "--results", str(folder), *extra])
    return code, json.loads((folder / "comparison.json").read_text(encoding="utf-8"))


def _metrics(report: dict, verdict: str) -> list[str]:
    return [r["metric"] for r in report["rows"] if r["verdict"] == verdict]


# ---------------------------------------------------------------------- recorded OmniSim run
@pytest.fixture
def omnisim() -> tuple[dict, dict]:
    truth = json.loads((FIXTURES / "omnisim_064_truth.json").read_text(encoding="utf-8"))
    results = json.loads((FIXTURES / "omnisim_064_results.json").read_text(encoding="utf-8"))
    return truth, results


def test_omnisim_064_run_passes_with_the_known_warnings(
    tmp_path: Path, omnisim: tuple[dict, dict], capsys: pytest.CaptureFixture[str]
) -> None:
    code, report = _run(tmp_path, *omnisim)
    assert code == 0
    assert report["result"] == "PASS"
    assert report["counts"].get("FAIL", 0) == 0
    assert report["counts"].get("SHAPE", 0) == 0
    assert report["counts"]["PASS"] >= 35
    warned = _metrics(report, "WARN")
    # 0.6.x shapes: no list_nodes, no summary, no rate block, no RMW field, no subscription QoS.
    assert "list_nodes" in warned
    assert "scan summary" in warned
    assert "rate block" in warned
    assert "RMW reported by TopicForge" in warned
    assert "QoS of subscriber-only topics" in warned
    assert len(warned) <= 7
    assert "RESULT: PASS" in capsys.readouterr().out
    assert (
        (tmp_path / "results" / "COMPARISON.md")
        .read_text(encoding="utf-8")
        .endswith("INFO " + str(report["counts"].get("INFO", 0)) + ")\n")
    )


def test_omnisim_run_with_a_wrong_range_fails_and_exits_1(
    tmp_path: Path, omnisim: tuple[dict, dict]
) -> None:
    truth, results = omnisim
    bad = copy.deepcopy(results)
    sample = bad["07_sample_scan_full"]["parsed"][0]["samples"][0]["payload"]
    sample["ranges"][270] = 2.9
    code, report = _run(tmp_path, truth, bad)
    assert code == 1
    assert report["result"] == "FAIL"
    assert any("ranges vs lidar.published_ranges" in m for m in _metrics(report, "FAIL"))


def test_omnisim_run_with_a_wrong_bag_count_and_topic_set_fails(
    tmp_path: Path, omnisim: tuple[dict, dict]
) -> None:
    truth, results = omnisim
    bad = copy.deepcopy(results)
    bad["13_analyze_bag"]["parsed"][0]["message_count"] += 1
    topics = bad["02_list_topics"]["parsed"]
    del topics[0]
    code, report = _run(tmp_path, truth, bad)
    assert code == 1
    failed = _metrics(report, "FAIL")
    assert "message_count" in failed
    assert "topic set" in failed


def test_missing_results_are_failures_not_crashes(
    tmp_path: Path, omnisim: tuple[dict, dict]
) -> None:
    truth, _ = omnisim
    code, report = _run(tmp_path, truth, {})
    assert code == 1
    assert "list_topics" in _metrics(report, "FAIL")


def test_strict_shape_turns_shape_rows_into_failures(
    tmp_path: Path, omnisim: tuple[dict, dict]
) -> None:
    truth, results = omnisim
    bad = copy.deepcopy(results)
    bad["07_sample_scan_full"]["parsed"] = [{"unexpected": 1}]
    code, report = _run(tmp_path, truth, bad)
    assert report["counts"].get("SHAPE", 0) >= 1
    assert code == 0
    code, _ = _run(tmp_path / "strict", truth, bad, "--strict-shape")
    assert code == 1


# ---------------------------------------------------------------------- synthetic contract 2
def _call(tool: str, args: dict, payload: Any, wall: float = 0.1) -> dict:
    return {
        "isError": False,
        "parsed": [payload],
        "_call": {"tool": tool, "args": args, "wall_s": wall},
    }


def _rate(hz: float, cv: float = 0.04) -> dict:
    return {
        "basis": "received_ns",
        "message_count": 12,
        "interval_cv": cv,
        "verdict": "stable",
        "observed_frequency_hz": hz,
        "sim_frequency_hz": hz,
    }


def _samples(topic: str, source: str, payload: dict, hz: float, cv: float = 0.04) -> dict:
    stamps = [0] * 12 if source == "none" else [(1 + i) * 10**8 for i in range(12)]
    samples = [
        {"topic": topic, "timestamp_ns": t, "stamp_source": source, "payload": payload}
        for t in stamps
    ]
    return _call(
        "sample_messages",
        {"topic": topic, "count": 12},
        {"topic": topic, "count": 12, "samples": samples, "rate": _rate(hz, cv)},
    )


def _bench_scan_sample(gt: dict, args: dict) -> dict:
    lidar = gt["lidar"]
    payload = {
        k: lidar[k]
        for k in (
            "angle_min",
            "angle_max",
            "angle_increment",
            "range_min",
            "range_max",
            "time_increment",
            "scan_time",
        )
    }
    payload.update(
        header={"frame_id": lidar["frame_id"]},
        ranges=list(lidar["published_ranges"]),
        intensities=[0.0] * lidar["n_intensities"],
    )
    walls = gt["walls"]
    visible = {k: w for k, w in walls.items() if w["in_field_of_view"]}
    closest = min(visible.values(), key=lambda w: w["expected_range_m"])
    sectors = {}
    for name, w in walls.items():
        sectors[name] = (
            {
                "closest": {"range": w["expected_range_m"], "beam_index": w["beam_index"]},
                "beam_count": 10,
            }
            if w["in_field_of_view"]
            else {"closest": None, "beam_count": 0, "note": "outside"}
        )
    summary = {
        "beam_count": lidar["n_beams"],
        "frame_id": lidar["frame_id"],
        "closest_obstacle": {
            "range": closest["expected_range_m"],
            "beam_index": closest["beam_index"],
        },
        "sectors": sectors,
    }
    sample = {
        "topic": "/scan",
        "timestamp_ns": 10**8,
        "stamp_source": "header",
        "payload": payload,
        "summary": summary,
    }
    return _call(
        "sample_messages",
        args,
        {"topic": "/scan", "count": 1, "samples": [sample], "rate": _rate(10.0)},
    )


def _bench_results(gt: dict) -> dict[str, Any]:
    out: dict[str, Any] = {}
    before = gt["graph"]["before_capture"]
    out["_initialize"] = {"topicforge_version": "0.7.0-test"}
    out["01_health_check"] = _call(
        "health_check",
        {},
        {
            "ros2_distro": gt["ros"]["distro"],
            "rmw_implementation": gt["ros"]["rmw_implementation"],
            "rmw_source": "env",
            "contract_version": 2,
        },
    )
    out["02_list_topics"] = _call(
        "list_topics",
        {},
        {
            "topics": [
                {
                    "name": t["name"],
                    "message_type": t["types"][0],
                    "publisher_count": t["publisher_count"] or 1,
                    "subscriber_count": t["subscription_count"],
                }
                for t in before["topics"]
            ]
        },
    )
    for topic in before["topics"]:
        if topic.get("counts") == "ignore":
            continue
        ep = topic["endpoints"][0]
        qos = {
            "reliability": ep["reliability"].lower(),
            "durability": ep["durability"].lower(),
            "endpoint_count": 1,
        }
        info = {
            "name": topic["name"],
            "message_type": topic["types"][0],
            "publisher_count": 1,
            "subscriber_count": 0,
            "publisher_qos": qos,
            "subscription_qos": None,
            "publisher_nodes": ["/bench_robot"],
            "subscriber_nodes": [],
        }
        out["03_topic_info_" + compare.slug(topic["name"])] = _call(
            "get_topic_info", {"topic": topic["name"]}, info
        )
    out["02n_list_nodes"] = _call(
        "list_nodes",
        {},
        {
            "nodes": [
                {"name": n.strip("/"), "namespace": "/", "full_name": n, "duplicate_count": 1}
                for n in before["nodes"]
            ],
            "duplicates": [],
        },
    )
    infra = [
        {"name": "/parameter_events", "type": "rcl_interfaces/msg/ParameterEvent"},
        {"name": "/rosout", "type": "rcl_interfaces/msg/Log"},
    ]
    user = [
        {"name": t["name"], "type": t["types"][0]}
        for t in before["topics"]
        if t.get("counts") != "ignore"
    ]
    out["03n_node_info_bench_robot"] = _call(
        "get_node_info",
        {"node": "/bench_robot"},
        {
            "full_name": "/bench_robot",
            "publishers": user + infra,
            "subscribers": [],
            "use_sim_time": False,
            "use_sim_time_note": None,
        },
    )
    out["03n_node_info_bench_blocked"] = _call(
        "get_node_info",
        {"node": "/bench_blocked"},
        {
            "full_name": "/bench_blocked",
            "publishers": infra,
            "subscribers": [],
            "use_sim_time": None,
            "use_sim_time_note": "the node did not answer",
        },
    )
    out["04_list_participants"] = _call(
        "list_participants",
        {},
        {
            "participants": [
                {"vendor": "fast", "name": "bench_robot", "is_observer": False},
                {"vendor": "fast", "name": "bench_blocked", "is_observer": False},
                {"vendor": "cyclone", "name": "topicforge", "is_observer": True},
            ]
        },
    )
    out["05_list_endpoints"] = _call(
        "list_endpoints",
        {},
        {
            "endpoints": [
                {"role": "writer", "ros_topic": t["name"], "qos": {"reliability": "RELIABLE"}}
                for t in before["topics"]
                if t.get("counts") != "ignore"
            ]
        },
    )
    out["06_detect_qos_mismatches"] = _call(
        "detect_qos_mismatches", {}, {"reports": [], "reports_total": 0, "matched_total": 6}
    )
    out["07_sample_scan_full"] = _bench_scan_sample(gt, {"topic": "/scan", "count": 1})
    out["08_sample_scan_defaults"] = _call(
        "sample_messages",
        {"topic": "/scan", "count": 1},
        {"samples": [{"payload": {"ranges": [1.0] * 128, "_truncated_fields": ["ranges"]}}]},
    )
    out["08c_sample_scan_rate"] = _bench_scan_sample(gt, {"topic": "/scan", "count": 12})
    out["20_sample_clock"] = _samples("/clock", "payload", {"clock": {}}, 50.0)
    out["20_sample_cmd_vel_out"] = _samples("/cmd_vel_out", "none", {}, 5.0)
    out["20_sample_camera_image_raw"] = _samples("/camera/image_raw", "header", {}, 2.0)
    out["20_sample_scan_edge"] = _samples(
        "/scan_edge", "header", {"ranges": ["inf", "nan", "-inf", 1.5]}, 2.0
    )
    return out


def _bag_truth(gt: dict) -> None:
    gt["bag"] = {
        "storage_identifier": "sqlite3",
        "duration_ns": 10_000_000_000,
        "message_count": 150,
        "scans_total": 100,
        "scans_equal_to_published_scan": 100,
        "per_topic": {
            "/scan": {
                "count": 100,
                "first_ns": 10**9,
                "last_ns": 10_900_000_000,
                "rate_recorded_wall_hz": 99 / 9.9,
                "count_over_bag_duration_hz": 10.0,
            },
            "/clock": {
                "count": 50,
                "first_ns": 10**9,
                "last_ns": 10**10,
                "rate_recorded_wall_hz": 49 / 9.0,
                "count_over_bag_duration_hz": 5.0,
            },
        },
    }


def _bag_results(gt: dict, out: dict[str, Any]) -> None:
    out["13_analyze_bag"] = _call(
        "analyze_bag",
        {"path": "/bag"},
        {
            "storage_format": "sqlite3",
            "duration_s": 10.0,
            "message_count": 150,
            "topics": [
                {
                    "name": "/scan",
                    "message_count": 100,
                    "kind": "user",
                    "frequency_hz": 99 / 9.9,
                    "first_timestamp_ns": 10**9,
                    "last_timestamp_ns": 10_900_000_000,
                    "frequency_basis": "topic_span",
                },
                {
                    "name": "/clock",
                    "message_count": 50,
                    "kind": "user",
                    "frequency_hz": 49 / 9.0,
                    "first_timestamp_ns": 10**9,
                    "last_timestamp_ns": 10**10,
                    "frequency_basis": "topic_span",
                },
            ],
        },
    )
    scan = [
        {
            "payload": {"ranges": list(gt["lidar"]["published_ranges"])},
            "stamp_source": "header",
            "recorded_ns": 10**9,
        }
    ]
    out["14_peek_bag_scan"] = _call("peek_bag_samples", {"topic": "/scan"}, {"samples": scan * 2})
    out["16_peek_bag_clock"] = _call(
        "peek_bag_samples",
        {"topic": "/clock"},
        {
            "samples": [
                {"stamp_source": "payload", "timestamp_ns": (i + 1) * 10**9} for i in range(3)
            ]
        },
    )


@pytest.fixture
def bench_truth() -> dict:
    truth = bench.build_truth("humble", "rmw_fastrtps_cpp")
    _bag_truth(truth)
    return truth


def test_bench_truth_describes_the_published_scan() -> None:
    truth = bench.build_truth("jazzy", "rmw_cyclonedds_cpp")
    lidar = truth["lidar"]
    assert lidar["n_beams"] == 541 and len(lidar["published_ranges"]) == 541
    assert lidar["published_ranges"][0] == pytest.approx(1.0)
    assert lidar["published_ranges"][540] == pytest.approx(1.54)
    walls = truth["walls"]
    # The scan spans exactly +-135 degrees: nothing beyond, the +-45 edge beams are in front.
    assert walls["rear"]["in_field_of_view"] is False
    assert (walls["right"]["beam_index"], walls["front"]["beam_index"]) == (0, 180)
    assert walls["left"]["beam_index"] == 361
    assert truth["ros"] == {
        "distro": "jazzy",
        "rmw_implementation": "rmw_cyclonedds_cpp",
        "use_sim_time": {"/bench_robot": False, "/bench_blocked": None},
    }
    json.dumps(truth)


def test_synthetic_contract2_run_matches_the_bench_truth(tmp_path: Path, bench_truth: dict) -> None:
    results = _bench_results(bench_truth)
    _bag_results(bench_truth, results)
    code, report = _run(tmp_path, bench_truth, results, "--strict-shape")
    assert report["counts"].get("FAIL", 0) == 0, [
        r for r in report["rows"] if r["verdict"] == "FAIL"
    ]
    assert report["counts"].get("SHAPE", 0) == 0
    assert report["counts"].get("WARN", 0) == 0, _metrics(report, "WARN")
    assert code == 0
    assert report["rate_cv"]["/scan"]["interval_cv"] == 0.04
    sections = {r["section"] for r in report["rows"]}
    assert sections == {"graph", "scan", "stamps", "rates", "bag", "dds"}


@pytest.mark.parametrize(
    "mutate, failing",
    [
        (
            lambda r: r["03n_node_info_bench_robot"]["parsed"][0].update(use_sim_time=True),
            "use_sim_time per node (get_node_info)",
        ),
        (
            lambda r: r["03n_node_info_bench_blocked"]["parsed"][0].update(use_sim_time=False),
            "use_sim_time per node (get_node_info)",
        ),
        (lambda r: r["02n_list_nodes"]["parsed"][0]["nodes"].pop(), "node set (list_nodes)"),
        (
            lambda r: r["20_sample_clock"]["parsed"][0]["samples"][0].update(stamp_source="none"),
            "/clock stamp_source",
        ),
        (
            lambda r: r["20_sample_scan_edge"]["parsed"][0]["samples"][0]["payload"][
                "ranges"
            ].reverse(),
            "/scan_edge inf/nan/-inf/finite ranges",
        ),
        (
            lambda r: r["07_sample_scan_full"]["parsed"][0]["samples"][0]["summary"]["sectors"][
                "front"
            ]["closest"].update(range=3.0),
            "summary front sector minimum",
        ),
        (
            lambda r: r["03_topic_info_scan"]["parsed"][0]["publisher_qos"].update(
                reliability="best_effort"
            ),
            "QoS per side and node names (get_topic_info)",
        ),
        (
            lambda r: r["13_analyze_bag"]["parsed"][0]["topics"][0].update(message_count=99),
            "per-topic message counts",
        ),
    ],
)
def test_synthetic_run_with_one_wrong_answer_fails(
    tmp_path: Path, bench_truth: dict, mutate: Any, failing: str
) -> None:
    results = _bench_results(bench_truth)
    _bag_results(bench_truth, results)
    mutate(results)
    code, report = _run(tmp_path, bench_truth, results, "--strict-shape")
    assert code == 1
    assert any(failing in m for m in _metrics(report, "FAIL")), _metrics(report, "FAIL")


def test_a_noisy_fixed_rate_publisher_warns_stable_cv(tmp_path: Path, bench_truth: dict) -> None:
    results = _bench_results(bench_truth)
    _bag_results(bench_truth, results)
    results["20_sample_clock"] = _samples("/clock", "payload", {"clock": {}}, 50.0, cv=0.31)
    code, report = _run(tmp_path, bench_truth, results)
    assert code == 0
    assert "/clock STABLE_CV" in _metrics(report, "WARN")
    assert report["rate_cv"]["/clock"]["interval_cv"] == 0.31


def test_a_rate_far_from_the_configured_one_fails(tmp_path: Path, bench_truth: dict) -> None:
    results = _bench_results(bench_truth)
    _bag_results(bench_truth, results)
    results["20_sample_cmd_vel_out"] = _samples("/cmd_vel_out", "none", {}, 1.0)
    code, report = _run(tmp_path, bench_truth, results)
    assert code == 1
    assert "/cmd_vel_out wall frequency (Hz)" in _metrics(report, "FAIL")


# ---------------------------------------------------------------------- bag truth with rosbags
def test_bag_truth_is_read_from_the_messages(tmp_path: Path) -> None:
    pytest.importorskip("rosbags")
    from rosbags.rosbag2 import Writer
    from rosbags.typesys import Stores, get_typestore

    typestore = get_typestore(Stores.LATEST)
    msgtype = "std_msgs/msg/String"
    with Writer(tmp_path / "bag", version=Writer.VERSION_LATEST) as writer:
        fast = writer.add_connection("/fast", msgtype, typestore=typestore)
        slow = writer.add_connection("/slow", msgtype, typestore=typestore)
        for i in range(21):
            raw = typestore.serialize_cdr(typestore.types[msgtype](data="x"), msgtype)
            writer.write(fast, 5_000_000_000 + i * 100_000_000, raw)
        writer.write(slow, 5_500_000_000, raw)
    truth = compare.build_bag_truth(tmp_path / "bag")
    assert truth["message_count"] == 22
    assert truth["duration_ns"] == 2_000_000_000
    assert truth["storage_identifier"] == "sqlite3"
    fast_truth = truth["per_topic"]["/fast"]
    assert (fast_truth["count"], fast_truth["first_ns"], fast_truth["last_ns"]) == (
        21,
        5_000_000_000,
        7_000_000_000,
    )
    assert fast_truth["rate_recorded_wall_hz"] == pytest.approx(10.0)
    assert fast_truth["count_over_bag_duration_hz"] == pytest.approx(10.5)
    assert truth["per_topic"]["/slow"]["rate_recorded_wall_hz"] is None


# ---------------------------------------------------------------------- drive.py (no server)
def test_drive_expands_for_each_calls_from_saved_results() -> None:
    saved = {
        "02_list_topics": _call(
            "list_topics",
            {},
            {
                "topics": [
                    {"name": "/scan", "publisher_count": 1},
                    {"name": "/camera/image_raw", "publisher_count": 1},
                    {"name": "/cmd_vel", "publisher_count": 0},
                    {"name": "/rosout", "publisher_count": 3},
                ]
            },
        ),
        "02n_list_nodes": _call("list_nodes", {}, {"nodes": [{"full_name": "/ns/a"}]}),
    }
    sample = {
        "name": "20_sample_{slug}",
        "tool": "sample_messages",
        "for_each": "topics",
        "min_publishers": 1,
        "exclude": ["/scan", "/rosout"],
        "args": {"topic": "{item}"},
    }
    calls = drive.expand(sample, saved, None)
    assert [c["name"] for c in calls] == ["20_sample_camera_image_raw"]
    assert calls[0]["args"] == {"topic": "/camera/image_raw"}
    node = {
        "name": "03n_{slug}",
        "tool": "get_node_info",
        "for_each": "nodes",
        "args": {"node": "{item}"},
    }
    assert drive.expand(node, saved, None)[0]["args"] == {"node": "/ns/a"}
    assert drive.expand(node, saved, None)[0]["name"] == "03n_ns_a"


def test_drive_skips_bag_calls_without_a_bag_or_a_topic() -> None:
    saved = {"13_analyze_bag": _call("analyze_bag", {}, {"topics": [{"name": "/clock"}]})}
    call = {"name": "x", "tool": "peek_bag_samples", "needs_bag": True, "args": {"path": "{BAG}"}}
    assert drive.expand(call, saved, None) == []
    assert drive.expand(call, saved, "/b")[0]["args"] == {"path": "/b"}
    rosout = {**call, "requires_bag_topic": "/rosout"}
    assert drive.expand(rosout, saved, "/b") == []
    assert drive.expand({**call, "requires_bag_topic": "/clock"}, saved, "/b")


def test_default_calls_file_puts_graph_calls_before_sampling() -> None:
    calls = json.loads(
        (ROOT / "scripts" / "ground_truth" / "calls.json").read_text(encoding="utf-8")
    )
    tools = [c.get("tool") for c in calls if "tool" in c]
    first_sample = tools.index("sample_messages")
    for graph in (
        "list_topics",
        "get_topic_info",
        "list_nodes",
        "get_node_info",
        "list_participants",
        "list_endpoints",
    ):
        assert tools.index(graph) < first_sample


def test_scripts_are_ascii() -> None:
    for path in [
        *(ROOT / "scripts" / "ground_truth").iterdir(),
        FIXTURES / "omnisim_064_truth.json",
        FIXTURES / "omnisim_064_results.json",
        ROOT / "tests/integration/ros2/ground_truth.py",
    ]:
        if path.is_file():
            path.read_text(encoding="ascii")
