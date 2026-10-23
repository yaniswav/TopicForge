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


def _rate(hz: float, cv: float = 0.04, count: int = 12, clock: dict | None = None) -> dict:
    return {
        **(clock or {}),
        "basis": "received_ns",
        "message_count": count,
        "startup_burst_count": 0,
        "mean_interval_s": 1 / hz,
        "interval_cv": cv,
        "verdict": "stable",
        "observed_frequency_hz": hz,
        "sim_frequency_hz": hz,
    }


def _samples(
    topic: str,
    source: str,
    payload: dict,
    hz: float,
    cv: float = 0.04,
    count: int = 12,
    steps_ns: list[int] | None = None,
    clock: dict | None = None,
) -> dict:
    """Sample result; stamps step by 100 ms unless `steps_ns` (cycled) says otherwise."""
    stamps = [0] * count
    if source != "none":
        steps = steps_ns or [10**8]
        stamps = [10**8]
        for i in range(count - 1):
            stamps.append(stamps[-1] + steps[i % len(steps)])
    samples = [
        {"topic": topic, "timestamp_ns": t, "stamp_source": source, "payload": payload}
        for t in stamps
    ]
    return _call(
        "sample_messages",
        {"topic": topic, "count": count},
        {"topic": topic, "count": count, "samples": samples, "rate": _rate(hz, cv, count, clock)},
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
        {
            "topic": "/scan",
            "count": 1,
            "samples": [sample],
            "rate": _rate(10.0, clock={"clock_basis": "sim"}),
        },
    )


def _tf_report(truth: dict, declared: dict) -> dict[str, Any]:
    """A perfect `inspect_tf` answer for the bench's `tf` and `declared.urdf` truth."""
    parents = {}
    for parent, child in [*truth["static_edges"], *truth["dynamic_edges"]]:
        parents.setdefault(child, []).append(parent)
    static = {child for _, child in truth["static_edges"]}
    frames = []
    for name in truth["frames"]:
        parent = sorted(parents[name])[0] if name in parents else None
        steady = name not in static and parent is not None
        frames.append(
            {
                "name": name,
                "parent": parent,
                "kind": None if parent is None else ("static" if name in static else "dynamic"),
                "stale": name in truth["stale"],
                "observed_frequency_hz": truth["steady_hz"] if steady else None,
                "sim_frequency_hz": truth["steady_hz"] if steady else None,
            }
        )
    findings = [{"kind": "stale", "frames": ["map", "odom"], "text": "stale"}]
    findings += [
        {
            "kind": "declared_not_published",
            "frames": declared["declared_frames_missing"],
            "text": "d",
        }
    ]
    findings += [
        {"kind": "two_parents", "frames": [child, *ps], "text": "two parents"}
        for child, ps in truth["two_parents"].items()
    ]
    return {
        "frames": frames,
        "roots": truth["roots"],
        "findings": findings,
        "path": truth["path"],
        "real_time_factor": truth["real_time_factor"],
        "urdf_source": declared["source"],
        "urdf_link_count": declared["link_count"],
        "declared_frames_missing": declared["declared_frames_missing"],
        "frames_not_in_urdf": declared["frames_not_in_urdf"],
        "urdf_note": None,
    }


ACTION_TYPE = "example_interfaces/action/Fibonacci"


def _bench_results(gt: dict) -> dict[str, Any]:
    out: dict[str, Any] = {}
    before = gt["graph"]["before_capture"]
    out["_initialize"] = {"topicforge_version": "0.7.0-test"}
    out["_tools"] = [{"name": "health_check", "description": "d", "output_schema": None}]
    out["01_health_check"] = _call(
        "health_check",
        {},
        {
            "ros2_distro": gt["ros"]["distro"],
            "rmw_implementation": gt["ros"]["rmw_implementation"],
            "rmw_source": "env",
            "contract_version": 2,
            "ros_security_enabled": False,
            "ros_security_strategy": None,
            "ros_security_keystore_set": False,
            "ros_security_note": None,
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
            "publisher_count": topic["publisher_count"],
            "subscriber_count": 0,
            "publisher_qos": qos,
            "subscription_qos": None,
            "publisher_nodes": [compare.node_full_name(ep)],
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
                {
                    "name": n.rpartition("/")[2],
                    "namespace": n.rpartition("/")[0] or "/",
                    "full_name": n,
                    "duplicate_count": 1,
                    "lifecycle": n in before["lifecycle_nodes"],
                }
                for n in before["nodes"]
            ],
            "duplicates": [],
            "lifecycle_count": len(before["lifecycle_nodes"]),
            "lifecycle_note": None,
            "by_namespace": dict(before["nodes_by_namespace"]),
        },
    )
    infra = [
        {"name": "/parameter_events", "type": "rcl_interfaces/msg/ParameterEvent"},
        {"name": "/rosout", "type": "rcl_interfaces/msg/Log"},
    ]
    user = [
        {"name": t["name"], "type": t["types"][0]}
        for t in before["topics"]
        if t.get("counts") != "ignore" and not t["name"].startswith("/robot1/")
    ]
    lifecycle_topic = next(t for t in before["topics"] if t["name"].startswith("/robot1/"))
    out["03n_node_info_robot1_bench_lifecycle"] = _call(
        "get_node_info",
        {"node": "/robot1/bench_lifecycle"},
        {
            "full_name": "/robot1/bench_lifecycle",
            "publishers": [
                {"name": lifecycle_topic["name"], "type": lifecycle_topic["types"][0]},
                *infra,
            ],
            "subscribers": [],
            "use_sim_time": False,
            "use_sim_time_note": None,
        },
    )
    out["03n_node_info_bench_robot"] = _call(
        "get_node_info",
        {"node": "/bench_robot"},
        {
            "full_name": "/bench_robot",
            "publishers": user + infra,
            "subscribers": [],
            "action_servers": [{"name": "/bench_fib", "type": ACTION_TYPE}],
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
                {
                    "vendor": "fast",
                    "name": "bench_robot",
                    "is_observer": False,
                    "node_names": ["/bench_robot"],
                    "endpoint_count": 31,
                },
                {"vendor": "fast", "name": "bench_blocked", "is_observer": False},
                {"vendor": "fast", "name": "bench_lifecycle", "is_observer": False},
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
                for _ in range(t["publisher_count"])
            ]
        },
    )
    out["05s_list_endpoints_scan"] = _call(
        "list_endpoints",
        {"topic": "/scan"},
        {
            "endpoints": [
                {"role": "writer", "ros_topic": "/scan", "node_name": "/bench_robot"},
                {"role": "reader", "ros_topic": "/scan", "node_name": None},
            ],
            "by_topic": [
                {"ros_topic": "/scan", "writer_nodes": ["/bench_robot"], "reader_nodes": []}
            ],
        },
    )
    out["06_detect_qos_mismatches"] = _call(
        "detect_qos_mismatches", {}, {"reports": [], "reports_total": 0, "matched_total": 6}
    )
    declared = gt["declared"]
    out["17_inspect_tf"] = _call(
        "inspect_tf", gt["tf"]["call"], _tf_report(gt["tf"], declared["urdf"])
    )
    out[declared["params"]["call"]] = _call(
        "get_node_info",
        {"node": declared["params"]["node"]},
        {
            "full_name": declared["params"]["node"],
            "param_diff": [dict(d) for d in declared["params"]["differences"]],
            "param_diff_summary": f"{declared['params']['same']} same (frame_id), 3 live_only",
            "param_diff_note": None,
        },
    )
    _graph_diff_results(gt, out)
    _nav_results(gt, out)
    out["07_sample_scan_full"] = _bench_scan_sample(gt, {"topic": "/scan", "count": 1})
    out["08_sample_scan_defaults"] = _call(
        "sample_messages",
        {"topic": "/scan", "count": 1},
        {"samples": [{"payload": {"ranges": [1.0] * 128, "_truncated_fields": ["ranges"]}}]},
    )
    out["08c_sample_scan_rate"] = _bench_scan_sample(gt, {"topic": "/scan", "count": 12})
    out["20_sample_clock"] = _samples("/clock", "payload", {"clock": {}}, 50.0)
    out["20_sample_cmd_vel_out"] = _samples(
        "/cmd_vel_out", "none", {}, 5.0, clock={"clock_basis": "none"}
    )
    out["20_sample_imu_data"] = _samples(
        "/imu/data",
        "header",
        {},
        50.0,
        clock={"clock_basis": "wall", "stamp_age_median_s": 0.04, "stamp_age_p95_s": 0.12},
    )
    out["20_sample_pose_mixed"] = _samples(
        "/pose_mixed", "header", {}, 20.0, clock={"clock_basis": "mixed"}
    )
    out["20_sample_stamps_regress"] = _samples(
        "/stamps_regress",
        "header",
        {},
        5.0,
        clock={"clock_basis": "sim", "stamp_regression_count": 3},
    )
    out["20_sample_camera_image_raw"] = _samples("/camera/image_raw", "header", {}, 2.0)
    out["20_sample_scan_edge"] = _samples(
        "/scan_edge", "header", {"ranges": ["inf", "nan", "-inf", 1.5]}, 2.0
    )
    return out


def _nav_results(gt: dict, out: dict[str, Any]) -> None:
    """The two `calls_nav_primitives.json` answers of a healthy bench."""
    truth = gt["nav_primitives"]
    status, bond = truth["action_status"], truth["bond"]
    out[status["call"]] = _call(
        "sample_messages",
        {"topic": status["topic"], "count": 1},
        {"topic": status["topic"], "count": 1, "samples": [{"payload": {"status_list": []}}]},
    )
    beats = [{"payload": {"id": bond["id"], "active": True}} for _ in range(5)]
    out[bond["call"]] = _call(
        "sample_messages",
        {"topic": bond["topic"], "count": 5},
        {"topic": bond["topic"], "count": 5, "samples": beats},
    )


def _bag_truth(gt: dict) -> None:
    gt["bag"] = {
        "storage_identifier": "sqlite3",
        "duration_ns": 10_000_000_000,
        "message_count": 150,
        "starting_time_ns": 10**9,
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
                "stamps_ns": [10**9, *(3 * 10**9 + i * 10**7 for i in range(50)), 10**10],
            },
        },
    }


def _graph_diff_results(gt: dict, out: dict[str, Any], *, bag: bool = False) -> None:
    """The four `graph_diff` answers of a healthy bench (`calls_graph_diff.json`)."""
    truth = gt["graph_diff"]

    def report(changes: list[dict], **extra: Any) -> dict[str, Any]:
        return {
            "baseline_id": "snap-1",
            "changes": changes,
            "removed_count": sum(c["change"] == "removed" for c in changes),
            "builtin_changes_count": 2,
            "not_compared": {"rates": "bag against bag only"},
            **extra,
        }

    note = "No baseline yet: snapshot snap-1 stored; call again to compare with it."
    out[truth["first"]["call"]] = _call(
        "graph_diff", {}, report([], baseline_id=None, baseline=None, note=note)
    )
    out[truth["steady"]["call"]] = _call("graph_diff", {}, report([]))
    planned = [{**c, "text": "t"} for c in truth["v2"]["changes"]]
    out[truth["v2"]["call"]] = _call("graph_diff", {}, report(planned))
    if bag:
        out[truth["bag"]["call"]] = _call(
            "graph_diff",
            {"baseline": "/bag"},
            report(
                [{"kind": "topic", "change": "added", "name": "/tf", "text": "t"}],
                baseline_id="/bag",
                baseline={"source": "bag"},
                not_compared={"nodes": "no graph", "rates": "bag only", "counts": "none"},
            ),
        )


def _bag_results(gt: dict, out: dict[str, Any]) -> None:
    _graph_diff_results(gt, out, bag=True)
    out["13_analyze_bag"] = _call(
        "analyze_bag",
        {"path": "/bag"},
        {
            "storage_format": "sqlite3",
            "duration_s": 10.0,
            "message_count": 150,
            "start_ns": 10**9,
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
    _log_results(out)


def _log(level: int, name: str, sec: int = 1) -> dict[str, Any]:
    return {"payload": {"level": level, "name": name}, "timestamp_ns": sec * 10**9}


def _log_results(out: dict[str, Any], clock_count: int = 50, error_count: int = 4) -> None:
    """Results of the 0.7.3 log and bag-range calls, as a healthy bench gives them."""
    errors = [_log(40, "bench_robot", 2 * i) for i in range(error_count)]
    mixed = [_log(30, "bench_robot", 1), _log(40, "bench_robot", 2), _log(30, "bench_robot", 3)]
    note = {"note": "matched"}
    out["21_log_error_node"] = _call("sample_messages", {}, {"samples": errors, **note})
    out["22_log_warn"] = _call("sample_messages", {}, {"samples": mixed, **note})
    out["23_log_blocked_node"] = _call("sample_messages", {}, {"samples": [], **note})
    out["24_log_lifecycle_node"] = _call(
        "sample_messages", {}, {"samples": [_log(20, "robot1.bench_lifecycle")], **note}
    )
    out["25_peek_bag_rosout_error"] = _call("peek_bag_samples", {}, {"samples": errors[:3], **note})
    clock = [{"recorded_ns": 3 * 10**9 + i * 10**7} for i in range(clock_count)]
    out["26_peek_bag_clock_range"] = _call("peek_bag_samples", {}, {"samples": clock})


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
        "use_sim_time": {
            "/bench_robot": False,
            "/bench_blocked": None,
            "/robot1/bench_lifecycle": False,
        },
    }
    before = truth["graph"]["before_capture"]
    assert before["lifecycle_nodes"] == ["/robot1/bench_lifecycle"]
    assert before["nodes_by_namespace"] == {"/": 2, "/robot1": 1}
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
    assert sections == {
        "graph",
        "scan",
        "stamps",
        "rates",
        "bag",
        "dds",
        "tf",
        "clock",
        "logs",
        "graph_diff",
        "nav_primitives",
        "visibility",
        "declared",
        "listing",
    }


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
            lambda r: r["40_action_status"]["parsed"][0]["samples"][0]["payload"].update(
                status_list=[{"status": 2}]
            ),
            "sample (sample_messages)",
        ),
        (
            lambda r: r["40_action_status"]["parsed"][0]["samples"].clear(),
            "sample (sample_messages)",
        ),
        (
            lambda r: r["03n_node_info_bench_robot"]["parsed"][0]["action_servers"].clear(),
            "in action_servers of /bench_robot",
        ),
        (
            lambda r: r["02_list_topics"]["parsed"][0]["topics"].append(
                {
                    "name": "/bench_fib/_action/status",
                    "message_type": "action_msgs/msg/GoalStatusArray",
                    "publisher_count": 1,
                    "subscriber_count": 0,
                }
            ),
            "absent from list_topics",
        ),
        (
            lambda r: r["41_bond"]["parsed"][0]["samples"][0]["payload"].update(id="other"),
            "samples carry the bond id",
        ),
        (lambda r: r["41_bond"]["parsed"][0]["samples"].clear(), "samples carry the bond id"),
        (
            lambda r: r["02n_list_nodes"]["parsed"][0]["nodes"][2].update(lifecycle=False),
            "lifecycle nodes (list_nodes)",
        ),
        (
            lambda r: r["02n_list_nodes"]["parsed"][0].update(by_namespace={"/": 3}),
            "nodes per namespace (list_nodes)",
        ),
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
        (
            lambda r: r["20_sample_imu_data"]["parsed"][0]["rate"].update(clock_basis="sim"),
            "/imu/data clock_basis",
        ),
        (
            lambda r: r["20_sample_imu_data"]["parsed"][0]["rate"].update(
                stamp_age_median_s=None, stamp_age_p95_s=None
            ),
            "/imu/data stamp_age_median_s",
        ),
        (
            lambda r: r["20_sample_imu_data"]["parsed"][0]["rate"].update(stamp_age_p95_s=0.9),
            "/imu/data stamp_age_p95_s",
        ),
        (
            lambda r: r["20_sample_pose_mixed"]["parsed"][0]["rate"].update(clock_basis="sim"),
            "/pose_mixed clock_basis",
        ),
        (
            lambda r: r["20_sample_stamps_regress"]["parsed"][0]["rate"].update(
                stamp_regression_count=0
            ),
            "/stamps_regress stamp_regression_count",
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


def test_the_tf_section_catches_each_kind_of_wrong_tree(tmp_path: Path, bench_truth: dict) -> None:
    def run(mutate: Any) -> tuple[int, list[str]]:
        results = _bench_results(bench_truth)
        _bag_results(bench_truth, results)
        mutate(results["17_inspect_tf"]["parsed"][0])
        code, report = _run(tmp_path / str(id(mutate)), bench_truth, results, "--strict-shape")
        return code, [m for m in _metrics(report, "FAIL") if "inspect_tf" in m]

    def no_stale(r: dict) -> None:
        next(f for f in r["frames"] if f["name"] == "odom")["stale"] = False
        r["findings"] = [f for f in r["findings"] if f["kind"] != "stale"]

    cases = {
        "frame set": lambda r: r["frames"].pop(0),
        "finding kinds": lambda r: r["findings"].pop(),
        "stale frames": no_stale,
        "roots": lambda r: r.update(roots=["odom"]),
        "path": lambda r: r.update(path=None),
        "two_parents tool": lambda r: r["findings"][-1].update(frames=["tool", "odom"]),
    }
    for needle, mutate in cases.items():
        code, failed = run(mutate)
        assert code == 1 and any(needle in m for m in failed), (needle, failed)


def test_a_missing_tf_answer_is_a_warning_not_a_failure(tmp_path: Path, bench_truth: dict) -> None:
    results = _bench_results(bench_truth)
    _bag_results(bench_truth, results)
    del results["17_inspect_tf"]
    code, report = _run(tmp_path, bench_truth, results)
    assert code == 0 and "inspect_tf" in _metrics(report, "WARN")


def test_the_bench_tf_truth_is_a_sound_description_of_the_published_tree() -> None:
    tf = bench.build_truth("humble", "rmw_fastrtps_cpp")["tf"]
    assert tf["roots"] == ["map"] and "tool" in tf["two_parents"]
    assert tf["path"] == [
        "camera_optical_frame",
        "camera_link",
        "base_link",
        "base_laser",
    ]
    assert tf["call"]["window_s"] > bench.TF_FLAKY_ACTIVE_S + 2 * tf["call"]["stale_after_s"]


def _clock_run(tmp_path: Path, truth: dict, **kw: Any) -> tuple[int, dict]:
    results = _bench_results(truth)
    _bag_results(truth, results)
    results["20_sample_clock"] = _samples("/clock", "payload", {"clock": {}}, 50.0, **kw)
    return _run(tmp_path, truth, results)


def test_a_noisy_short_sample_is_only_an_info(tmp_path: Path, bench_truth: dict) -> None:
    code, report = _clock_run(tmp_path, bench_truth, cv=0.31)
    assert code == 0
    assert "/clock STABLE_CV (n=12)" in _metrics(report, "INFO")
    assert "/clock STABLE_CV (n=12)" not in _metrics(report, "WARN")
    assert report["rate_cv"]["/clock"]["interval_cv"] == 0.31


def test_a_noisy_gated_sample_with_regular_stamps_warns_stable_cv(
    tmp_path: Path, bench_truth: dict
) -> None:
    code, report = _clock_run(tmp_path, bench_truth, cv=0.31, count=50)
    assert code == 0
    assert "/clock STABLE_CV (n=50)" in _metrics(report, "WARN")
    assert report["rate_cv"]["/clock"]["stamp_cv"] == 0.0


def test_a_noisy_gated_sample_with_irregular_stamps_is_only_an_info(
    tmp_path: Path, bench_truth: dict
) -> None:
    steps = [15 * 10**6, 25 * 10**6]
    code, report = _clock_run(tmp_path, bench_truth, cv=0.31, count=50, steps_ns=steps)
    assert code == 0
    assert "/clock STABLE_CV (n=50)" in _metrics(report, "INFO")
    assert "/clock STABLE_CV (n=50)" not in _metrics(report, "WARN")
    assert report["rate_cv"]["/clock"]["stamp_cv"] >= 0.2


def test_the_clock_rate_call_fails_only_when_gated(tmp_path: Path, bench_truth: dict) -> None:
    for count, verdict in ((50, "FAIL"), (12, "WARN")):
        results = _bench_results(bench_truth)
        _bag_results(bench_truth, results)
        results["08d_sample_clock_rate"] = _samples(
            "/clock", "payload", {"clock": {}}, 91.0, count=count
        )
        # 50 messages at 91 Hz span 0.54 s: ungated too; widen the span to gate the row.
        if count == 50:
            rate = results["08d_sample_clock_rate"]["parsed"][0]["rate"]
            rate["mean_interval_s"] = 0.02
        code, report = _run(tmp_path / str(count), bench_truth, results)
        label = f"/clock wall frequency (Hz, n={count})"
        assert label in _metrics(report, verdict)
        assert (code == 1) == (verdict == "FAIL")


def test_a_start_up_burst_is_listed_as_an_info(tmp_path: Path, bench_truth: dict) -> None:
    results = _bench_results(bench_truth)
    _bag_results(bench_truth, results)
    sample = _samples("/clock", "payload", {"clock": {}}, 50.0, count=12)
    result = sample["parsed"][0]
    result["rate"]["startup_burst_count"] = 5
    for i, item in enumerate(result["samples"]):
        item["received_ns"] = i * 10**6 if i < 6 else 5 * 10**6 + (i - 5) * 20 * 10**6
    results.pop("20_sample_clock", None)
    results["08d_sample_clock_rate"] = sample
    _, report = _run(tmp_path, bench_truth, results)
    assert "/clock start-up burst (n=12)" in _metrics(report, "INFO")
    row = next(r for r in report["rows"] if r["metric"] == "/clock start-up burst (n=12)")
    assert "1.0, 1.0, 1.0, 1.0, 1.0, 20.0" in row["note"]
    assert report["rate_cv"]["/clock"]["startup_burst_count"] == 5


def test_a_rate_far_from_the_configured_one_fails(tmp_path: Path, bench_truth: dict) -> None:
    results = _bench_results(bench_truth)
    _bag_results(bench_truth, results)
    results["20_sample_cmd_vel_out"] = _samples("/cmd_vel_out", "none", {}, 1.0)
    code, report = _run(tmp_path, bench_truth, results)
    assert code == 1
    assert "/cmd_vel_out wall frequency (Hz, n=12)" in _metrics(report, "FAIL")


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


def test_default_calls_file_runs_dds_calls_before_node_info_and_settles() -> None:
    calls = json.loads(
        (ROOT / "scripts" / "ground_truth" / "calls.json").read_text(encoding="utf-8")
    )
    names = [c["name"] for c in calls if "name" in c]
    for dds in ("04_list_participants", "05_list_endpoints", "06_detect_qos_mismatches"):
        assert names.index(dds) < names.index("03n_node_info_{slug}")
    # 3 s settle before the DDS calls (ghost CLI participants leave) and at the very end.
    assert calls[-1] == {"settle_s": 3}
    first_dds = next(i for i, c in enumerate(calls) if c.get("name") == "04_list_participants")
    assert calls[first_dds - 1] == {"settle_s": 3}


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
        *FIXTURES.glob("*.json"),
        ROOT / "tests/integration/ros2/ground_truth.py",
    ]:
        if path.is_file():
            path.read_text(encoding="ascii")


def test_bag_truth_reads_a_humble_bag_without_message_definitions() -> None:
    # Humble's rosbag2 records no type definitions; the OmniSim fixture is such a bag.
    pytest.importorskip("rosbags")
    truth = compare.build_bag_truth(ROOT / "tests" / "fixtures" / "bags" / "omnisim_humble")
    assert truth["message_count"] == 1434
    assert truth["per_topic"]["/scan"]["count"] == 177


# ---------------------------------------------------------------- 0.7.1 runs, 2026-10-14
def _fixture(tag: str) -> tuple[dict, dict]:
    truth = json.loads((FIXTURES / f"omnisim_071_{tag}_truth.json").read_text(encoding="utf-8"))
    results = json.loads((FIXTURES / f"omnisim_071_{tag}_results.json").read_text(encoding="utf-8"))
    return truth, results


def test_071_morning_run_low_rtf_ends_pass_with_wall_rows_as_warnings(tmp_path: Path) -> None:
    # Host busy rendering, real_time_factor 0.162: every wall-clock frequency was a FAIL.
    # The truth is reconstructed (the kit's file was overwritten by the rerun); no bag section.
    code, report = _run(tmp_path, *_fixture("morning"))
    assert code == 0
    assert report["result"] == "PASS"
    assert report["counts"].get("FAIL", 0) == 0
    rows = [r for r in report["rows"] if "wall frequency" in r["metric"]]
    assert len(rows) == 8
    assert {r["verdict"] for r in rows} == {"WARN"}
    assert all("low RTF 0.162" in r["note"] for r in rows)
    sim = [r for r in report["rows"] if "sim frequency" in r["metric"] and r["verdict"] != "INFO"]
    assert sim and {r["verdict"] for r in sim} == {"PASS"}


def test_071_morning_run_still_fails_when_a_sim_frequency_is_wrong(tmp_path: Path) -> None:
    truth, results = _fixture("morning")
    truth["rates"]["per_topic"]["/odom"]["rate_sim_hz"] = 50.0
    code, report = _run(tmp_path, truth, results)
    assert code == 1
    assert "/odom sim frequency (Hz, n=12)" in _metrics(report, "FAIL")


def test_071_rerun_ends_pass_with_zero_fail(tmp_path: Path) -> None:
    # RTF 0.908. Wall rows gate again and pass; ghost CLI participants were the old FAIL.
    code, report = _run(tmp_path, *_fixture("rerun"))
    assert code == 0
    assert report["result"] == "PASS"
    assert report["counts"].get("FAIL", 0) == 0
    assert report["counts"]["PASS"] >= 75
    wall = [r for r in report["rows"] if "wall frequency" in r["metric"] and r["verdict"] != "INFO"]
    assert wall and {r["verdict"] for r in wall} == {"PASS"}


def test_071_rerun_ghost_cli_writers_on_infra_topics_are_not_counted(tmp_path: Path) -> None:
    truth, results = _fixture("rerun")
    bad = copy.deepcopy(results)
    endpoints = bad["05_list_endpoints"]["parsed"][0]["endpoints"]
    ghost = next(e for e in endpoints if e["role"] == "writer" and e["ros_topic"] == "/rosout")
    endpoints.append(copy.deepcopy(ghost))
    code, report = _run(tmp_path, truth, bad)
    assert code == 0
    assert "writers/readers per topic (list_endpoints)" in _metrics(report, "PASS")
    # ...but a real topic still counts.
    scan = next(e for e in endpoints if e["role"] == "writer" and e["ros_topic"] == "/scan")
    endpoints.append(copy.deepcopy(scan))
    code, report = _run(tmp_path / "again", truth, bad)
    assert code == 1
    assert "writers/readers per topic (list_endpoints)" in _metrics(report, "FAIL")


def _participants(results: dict) -> list[dict]:
    return results["04_list_participants"]["parsed"][0]["participants"]


def _active_fast(participants: list[dict]) -> list[dict]:
    return [
        p
        for p in participants
        if p["vendor"] == "fast" and not p["is_observer"] and p["status"] == "active"
    ]


def test_cli_participants_are_ignored_by_flag_or_node_name(tmp_path: Path) -> None:
    truth, results = _fixture("rerun")
    bad = copy.deepcopy(results)
    active = _active_fast(_participants(bad))
    assert len(active) == 8  # 6 nodes + daemon + one ghost `ros2 node info`
    active[0]["is_topicforge_cli"] = True
    active[1]["node_names"] = ["/_ros2cli_4242"]
    _, report = _run(tmp_path, truth, bad)
    count = next(r for r in report["rows"] if r["metric"].startswith("fast active participants"))
    assert count["topicforge"] == 6
    assert count["verdict"] == "PASS"
    note = next(r for r in report["rows"] if r["metric"].startswith("TopicForge ros2 CLI"))
    assert note["topicforge"] == "2 ignored"
    assert note["verdict"] == "INFO"


def test_cli_participant_flag_false_or_absent_changes_nothing(tmp_path: Path) -> None:
    truth, results = _fixture("rerun")
    flagged = copy.deepcopy(results)
    for p in _participants(flagged):
        p["is_topicforge_cli"] = False
    _, report = _run(tmp_path, truth, flagged)
    assert not [r for r in report["rows"] if r["metric"].startswith("TopicForge ros2 CLI")]
    count = next(r for r in report["rows"] if r["metric"].startswith("fast active participants"))
    assert count["topicforge"] == 8


@pytest.mark.parametrize(
    ("participant", "expected"),
    [
        ({"name": "/"}, False),
        ({"name": "/", "is_topicforge_cli": False}, False),
        ({"name": "/", "is_topicforge_cli": True}, True),
        ({"name": "/_ros2cli_1234"}, True),
        ({"name": "_ros2cli_1234"}, True),
        ({"name": "/", "node_names": ["/_ros2cli_77"]}, True),
        ({"name": "/", "node_names": ["/omnisim_clock"]}, False),
        ({"name": None}, False),
    ],
)
def test_is_cli_participant(participant: dict, expected: bool) -> None:
    assert compare._is_cli_participant(participant) is expected


def _rows_for(rate: dict, truth: dict, rtf: float | None) -> dict[str, dict[str, Any]]:
    rep = compare.Report()
    compare._compare_frequencies(truth, "/t", rate, rep, None, True, 3.0, rtf=rtf)
    return {r["metric"].split()[1]: r for r in rep.rows}


_RATE = {"message_count": 12, "observed_frequency_hz": 4.0, "sim_frequency_hz": 5.0}
_KIT = {"rate_wall_hz": 4.5, "rate_sim_hz": 5.0}


def test_rtf_rule_makes_wall_fail_a_warn_naming_the_rtf() -> None:
    kit = {"rate_wall_hz": 1.0, "rate_sim_hz": 5.0}
    rows = _rows_for({**_RATE, "observed_frequency_hz": 5.0}, kit, 0.162)
    assert rows["wall"]["verdict"] == "WARN"
    assert "low RTF 0.162 < 0.5" in rows["wall"]["note"]
    assert rows["sim"]["verdict"] == "PASS"


def test_rtf_rule_does_not_apply_at_or_above_the_threshold_or_without_rtf() -> None:
    kit = {"rate_wall_hz": 1.0, "rate_sim_hz": 1.0}
    rate = {**_RATE, "observed_frequency_hz": 5.0, "sim_frequency_hz": 5.0}
    for rtf in (0.5, 0.9, None):
        assert _rows_for(rate, kit, rtf)["wall"]["verdict"] == "FAIL"


def test_ratio_rule_warns_when_the_wall_sim_ratios_differ_by_more_than_25_percent() -> None:
    # kit ratio 0.5, TopicForge ratio 0.8 (60 % apart); wall 4.0 vs 2.5 is a FAIL otherwise.
    kit = {"rate_wall_hz": 2.5, "rate_sim_hz": 5.0}
    rows = _rows_for(_RATE, kit, 0.9)
    assert rows["wall"]["verdict"] == "WARN"
    assert "wall/sim ratio mismatch" in rows["wall"]["note"]
    assert "0.800" in rows["wall"]["note"]
    assert "0.500" in rows["wall"]["note"]


def test_ratio_rule_keeps_the_gate_when_the_ratios_agree() -> None:
    # ratios 0.9 and 0.9, but the wall rate itself is 60 % off: still a FAIL.
    kit = {"rate_wall_hz": 4.5, "rate_sim_hz": 5.0}
    rate = {**_RATE, "observed_frequency_hz": 1.8, "sim_frequency_hz": 2.0}
    assert _rows_for(rate, kit, 0.9)["wall"]["verdict"] == "FAIL"


def test_ratio_rule_needs_both_rates_and_leaves_sim_rows_gating() -> None:
    clock = {"rate_wall_hz": 6.0, "rate_sim_hz": None}
    rows = _rows_for({**_RATE, "observed_frequency_hz": 10.0}, clock, 0.9)
    assert rows["wall"]["verdict"] == "FAIL"
    assert rows["sim"]["verdict"] == "INFO"
    rows = _rows_for(_RATE, {"rate_wall_hz": 2.5, "rate_sim_hz": 1.0}, 0.9)
    assert rows["sim"]["verdict"] == "FAIL"


def test_relaxation_does_not_touch_a_passing_wall_row() -> None:
    rows = _rows_for(_RATE, _KIT, 0.1)
    assert rows["wall"]["verdict"] == "PASS"
    assert "low RTF" not in rows["wall"]["note"]


def test_listing_size_row_passes_fails_and_warns_when_absent(
    tmp_path: Path, bench_truth: dict
) -> None:
    def row(results: dict, sub: str) -> dict:
        _, report = _run(tmp_path / sub, bench_truth, results)
        return next(r for r in report["rows"] if r["metric"] == "tools/list bytes")

    results = _bench_results(bench_truth)
    assert row(results, "ok")["verdict"] == "PASS"
    results["_tools"] = [{"name": "t", "description": "x" * 100_000}]
    assert row(results, "big")["verdict"] == "FAIL"
    del results["_tools"]
    assert row(results, "absent")["verdict"] == "WARN"


def _logs_report(tmp_path: Path, truth: dict, mutate: Any = None) -> dict[str, str]:
    results = _bench_results(truth)
    _bag_results(truth, results)
    if mutate:
        mutate(results)
    _, report = _run(tmp_path, truth, results)
    return {r["metric"]: r["verdict"] for r in report["rows"] if r["section"] == "logs"}


def test_the_logs_section_passes_on_a_healthy_run(tmp_path: Path, bench_truth: dict) -> None:
    verdicts = _logs_report(tmp_path, bench_truth)
    assert len(verdicts) == 7
    assert set(verdicts.values()) == {"PASS"}


def test_too_few_buffered_errors_fail_and_the_row_reports_the_count(
    tmp_path: Path, bench_truth: dict
) -> None:
    def few(results: dict) -> None:
        _log_results(results, error_count=2)

    verdicts = _logs_report(tmp_path, bench_truth, few)
    assert verdicts["live /rosout error + node /bench_robot"] == "FAIL"
    rows = json.loads((tmp_path / "results" / "comparison.json").read_text())["rows"]
    row = next(r for r in rows if r["metric"].startswith("live /rosout error"))
    assert row["topicforge"].startswith("2 msgs")


def _late_clock(truth: dict, in_window: int) -> None:
    """The recorder started /clock late: only `in_window` stamps fall in [start+2 s, start+4 s)."""
    first = 4 * 10**9 + 1 - in_window * 10**7 if in_window else 4 * 10**9
    stamps = [first + i * 10**7 for i in range(in_window)] + [6 * 10**9]
    truth["bag"]["per_topic"]["/clock"]["stamps_ns"] = stamps


def test_a_late_clock_expects_the_stamps_inside_the_window(
    tmp_path: Path, bench_truth: dict
) -> None:
    _late_clock(bench_truth, 20)

    def got(results: dict) -> None:
        _log_results(results, clock_count=20)
        results["26_peek_bag_clock_range"]["parsed"][0]["samples"] = [
            {"recorded_ns": 4 * 10**9 - (20 - i) * 10**7 + 1} for i in range(20)
        ]

    verdicts = _logs_report(tmp_path / "a", bench_truth, got)
    assert verdicts["bag /clock start_s 2 end_s 4"] == "PASS"
    full = _logs_report(tmp_path / "b", bench_truth)
    assert full["bag /clock start_s 2 end_s 4"] == "FAIL"


def test_an_empty_clock_window_expects_no_sample_and_a_note(
    tmp_path: Path, bench_truth: dict
) -> None:
    _late_clock(bench_truth, 0)

    def none(results: dict) -> None:
        _log_results(results, clock_count=0)
        results["26_peek_bag_clock_range"]["parsed"][0]["note"] = "no message in range"

    assert _logs_report(tmp_path / "a", bench_truth, none)["bag /clock start_s 2 end_s 4"] == "PASS"
    bare = _logs_report(tmp_path / "b", bench_truth, lambda r: _log_results(r, clock_count=0))
    assert bare["bag /clock start_s 2 end_s 4"] == "FAIL"


def test_a_wrong_range_count_or_start_ns_fails(tmp_path: Path, bench_truth: dict) -> None:
    def short(results: dict) -> None:
        _log_results(results, clock_count=40)
        results["13_analyze_bag"]["parsed"][0]["start_ns"] = 5

    verdicts = _logs_report(tmp_path, bench_truth, short)
    assert verdicts["analyze_bag.start_ns"] == "FAIL"
    assert verdicts["bag /clock start_s 2 end_s 4"] == "FAIL"


# ------------------------------------------------------------------ graph_diff (0.8.0)
def _gd_run(tmp_path: Path, truth: dict, mutate: Any) -> tuple[int, list[str]]:
    results = _bench_results(truth)
    _bag_results(truth, results)
    mutate(results)
    code, report = _run(tmp_path / str(id(mutate)), truth, results, "--strict-shape")
    return code, _metrics(report, "FAIL")


def test_the_graph_diff_section_catches_each_kind_of_wrong_answer(
    tmp_path: Path, bench_truth: dict
) -> None:
    def parsed(r: dict, name: str) -> dict:
        return r[name]["parsed"][0]

    false_alarm = {"kind": "count", "change": "changed", "name": "/scan", "text": "t"}
    cases = {
        "first call": lambda r: parsed(r, "30_graph_diff_first").update(baseline_id="snap-0"),
        "steady graph": lambda r: parsed(r, "31_graph_diff_steady")["changes"].append(false_alarm),
        "v2: exactly": lambda r: parsed(r, "32_graph_diff_v2")["changes"].append(false_alarm),
        "v2: exactly the": lambda r: parsed(r, "32_graph_diff_v2")["changes"].pop(0),
        "/cmd_vel_out count": lambda r: parsed(r, "32_graph_diff_v2")["changes"][2].update(
            after="3"
        ),
        "removed_count": lambda r: parsed(r, "33_graph_diff_bag").update(removed_count=1),
        "no type or qos": lambda r: parsed(r, "33_graph_diff_bag")["changes"].append(
            {"kind": "qos", "change": "changed", "name": "/scan", "text": "t"}
        ),
        "not_compared": lambda r: parsed(r, "33_graph_diff_bag").update(not_compared={}),
    }
    for needle, mutate in cases.items():
        code, failed = _gd_run(tmp_path, bench_truth, mutate)
        assert code == 1 and any(needle in m for m in failed), (needle, failed)


# ------------------------------------------------------------------ visibility (0.8.0)
def test_the_visibility_section_passes_on_a_healthy_bench(
    tmp_path: Path, bench_truth: dict
) -> None:
    _, report = _run(tmp_path, bench_truth, _bench_results(bench_truth), "--strict-shape")
    rows = [r for r in report["rows"] if r["section"] == "visibility"]
    assert len(rows) == 5 and {r["verdict"] for r in rows} == {"PASS"}


def test_the_visibility_section_catches_each_kind_of_wrong_answer(
    tmp_path: Path, bench_truth: dict
) -> None:
    def parsed(r: dict, name: str) -> dict:
        return r[name]["parsed"][0]

    cases = {
        "writer node_name": lambda r: parsed(r, "05s_list_endpoints_scan")["endpoints"][0].update(
            node_name=None
        ),
        "writer_nodes": lambda r: parsed(r, "05s_list_endpoints_scan")["by_topic"][0].update(
            writer_nodes=["/"]
        ),
        "endpoint_count": lambda r: parsed(r, "04_list_participants")["participants"][0].update(
            endpoint_count=3
        ),
        "ros_security_enabled": lambda r: parsed(r, "01_health_check").update(
            ros_security_enabled=True
        ),
        "ros_security_note": lambda r: parsed(r, "01_health_check").update(
            ros_security_note="TopicForge joins the domain without an identity"
        ),
    }
    for needle, mutate in cases.items():
        results = _bench_results(bench_truth)
        mutate(results)
        code, report = _run(tmp_path / needle, bench_truth, results, "--strict-shape")
        failed = _metrics(report, "FAIL")
        assert code == 1 and any(needle in m for m in failed), (needle, failed)


def test_an_older_server_without_the_visibility_calls_only_warns(
    tmp_path: Path, bench_truth: dict
) -> None:
    results = _bench_results(bench_truth)
    _bag_results(bench_truth, results)
    del results["05s_list_endpoints_scan"]
    code, report = _run(tmp_path, bench_truth, results)
    assert code == 0 and "list_endpoints node_name" in _metrics(report, "WARN")


def test_the_kit_truth_has_no_visibility_section(
    tmp_path: Path, omnisim: tuple[dict, dict]
) -> None:
    _, report = _run(tmp_path, *omnisim)
    assert all(r["section"] != "visibility" for r in report["rows"])


def test_the_visibility_call_sits_in_the_kit_calls_and_names_the_scan_topic() -> None:
    kit = json.loads((ROOT / "scripts" / "ground_truth" / "calls.json").read_text("utf-8"))
    truth = bench.build_truth("humble", "rmw_fastrtps_cpp")["visibility"]
    call = next(c for c in kit if c.get("name") == truth["endpoints_call"])
    assert call["tool"] == "list_endpoints" and call["args"] == {"topic": truth["topic"]}
    assert truth["node"] in bench.BENCH_NODES and truth["writer_nodes"] == [truth["node"]]


def test_a_missing_graph_diff_answer_is_a_warning_not_a_failure(
    tmp_path: Path, bench_truth: dict
) -> None:
    results = _bench_results(bench_truth)
    _bag_results(bench_truth, results)
    for name in ("30_graph_diff_first", "32_graph_diff_v2"):
        del results[name]
    code, report = _run(tmp_path, bench_truth, results)
    assert code == 0
    assert {"first call (graph_diff)", "graph v2 (graph_diff)"} <= set(_metrics(report, "WARN"))


def test_the_kit_truth_has_no_graph_diff_section(
    tmp_path: Path, omnisim: tuple[dict, dict]
) -> None:
    _, report = _run(tmp_path, *omnisim)
    assert all(r["section"] != "graph_diff" for r in report["rows"])


def test_bench_graph_diff_truth_names_what_graph_change_publishes() -> None:
    truth = bench.build_truth("humble", "rmw_fastrtps_cpp")["graph_diff"]
    names = {c["name"] for c in truth["v2"]["changes"]}
    assert names == {bench.GRAPH_V2_NODE, bench.GRAPH_V2_TOPIC, bench.GRAPH_V2_SHARED_TOPIC}
    assert bench.GRAPH_V2_SHARED_TOPIC in bench.BENCH_TOPICS  # a topic the robot already writes
    assert bench.GRAPH_V2_TOPIC not in bench.BENCH_TOPICS
    assert bench.GRAPH_V2_NODE not in bench.BENCH_NODES
    source = (ROOT / "tests" / "integration" / "ros2" / "graph_change.py").read_text("ascii")
    for constant in ("GRAPH_V2_NODE", "GRAPH_V2_TOPIC", "GRAPH_V2_SHARED_TOPIC"):
        assert f"model.{constant}" in source


def test_graph_diff_calls_run_after_the_kit_calls_and_change_the_graph_in_between() -> None:
    kit = json.loads((ROOT / "scripts" / "ground_truth" / "calls.json").read_text("utf-8"))
    assert not any("spawn" in c or c.get("tool") == "graph_diff" for c in kit)
    calls = json.loads(
        (ROOT / "scripts" / "ground_truth" / "calls_graph_diff.json").read_text("utf-8")
    )
    names = [c.get("name") or ("spawn" if "spawn" in c else "settle") for c in calls]
    assert names == [
        "30_graph_diff_first",
        "settle",
        "31_graph_diff_steady",
        "spawn",
        "settle",
        "32_graph_diff_v2",
        "33_graph_diff_bag",
        "settle",
    ]
    assert calls[1]["settle_s"] >= 3 and calls[4]["settle_s"] >= 5
    assert calls[-1] == {"settle_s": 3}
    assert calls[3]["spawn"].endswith("graph_change.py")
    assert (ROOT / calls[3]["spawn"]).is_file()
    assert calls[6]["args"] == {"baseline": "{BAG}"} and calls[6]["needs_bag"]
    bench_script = (ROOT / "tests" / "integration" / "ros2" / "entrypoint.sh").read_text("utf-8")
    assert "calls_graph_diff.json" in bench_script


def test_nav_primitives_calls_match_the_truth_and_stay_out_of_the_kit_run() -> None:
    gt_dir = ROOT / "scripts" / "ground_truth"
    kit = json.loads((gt_dir / "calls.json").read_text("utf-8"))
    assert not any(str(c.get("name", "")).startswith(("40_", "41_")) for c in kit)
    sampled = next(c for c in kit if c.get("name") == "20_sample_{slug}")
    assert bench.BOND_TOPIC in sampled["exclude"]  # 41_bond reads it; no second reader
    calls = json.loads((gt_dir / "calls_nav_primitives.json").read_text("utf-8"))
    truth = bench.nav_primitives_truth()
    named = {c["name"]: c for c in calls if "name" in c}
    assert set(named) == {truth["action_status"]["call"], truth["bond"]["call"]}
    status = named["40_action_status"]
    assert status["args"] == {"topic": bench.ACTION_STATUS_TOPIC, "count": 1, "timeout_s": 8}
    assert named["41_bond"]["args"]["topic"] == bench.BOND_TOPIC
    assert calls[-1] == {"settle_s": 3}
    assert bench.BENCH_TOPICS[bench.BOND_TOPIC] == ("bond/msg/Status", False)
    assert bench.ACTION_STATUS_TOPIC not in bench.BENCH_TOPICS  # hidden: never in list_topics
    entry = (ROOT / "tests" / "integration" / "ros2" / "entrypoint.sh").read_text("utf-8")
    calls_line = next(x for x in entry.splitlines() if x.strip().startswith("--calls"))
    assert calls_line.index("calls_nav_primitives.json") < calls_line.index("calls_graph_diff.json")
    dockerfile = (ROOT / "tests" / "integration" / "ros2" / "Dockerfile").read_text("utf-8")
    assert "ros-${DISTRO}-example-interfaces" in dockerfile and "ros-${DISTRO}-bond" in dockerfile


# ---------------------------------------------------------------- drive.py: spawn
_SLEEPER = "import time\ntime.sleep(60)\n"


def test_drive_spawns_a_process_and_stops_it(tmp_path: Path) -> None:
    script = tmp_path / "sleeper.py"
    script.write_text(_SLEEPER, encoding="ascii")
    with open(tmp_path / "err.log", "w", encoding="utf-8") as log:
        proc = drive.spawn({"spawn": str(script), "python": sys.executable}, log)
        assert proc.poll() is None
        drive.stop_spawned([proc])
    assert proc.poll() is not None


def test_drive_stops_spawned_processes_when_the_session_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    import subprocess

    script = tmp_path / "sleeper.py"
    script.write_text(_SLEEPER, encoding="ascii")
    started: list[subprocess.Popen] = []

    async def failing_session(args: Any, calls: Any, out: Any, errlog: Any, spawned: list) -> None:
        proc = drive.spawn({"spawn": str(script), "python": sys.executable}, errlog)
        spawned.append(proc)
        started.append(proc)
        raise RuntimeError("the session broke")

    monkeypatch.setattr(drive, "run_session", failing_session)
    log_path = tmp_path / "err.log"
    with open(log_path, "w", encoding="utf-8") as log, pytest.raises(RuntimeError, match="broke"):
        asyncio.run(drive.run(None, [], tmp_path, log))  # type: ignore[arg-type]
    assert started and started[0].poll() is not None


# ------------------------------------------------------------------ declared vs real (0.8.0)
def _declared_run(tmp_path: Path, truth: dict, mutate: Any) -> tuple[int, list[str]]:
    results = _bench_results(truth)
    _bag_results(truth, results)
    mutate(results)
    code, report = _run(tmp_path / str(id(mutate)), truth, results, "--strict-shape")
    return code, _metrics(report, "FAIL")


def test_the_declared_section_catches_each_kind_of_wrong_answer(
    tmp_path: Path, bench_truth: dict
) -> None:
    def tf(r: dict) -> dict:
        return r["17_inspect_tf"]["parsed"][0]

    def params(r: dict) -> dict:
        return r["03p_node_info_params"]["parsed"][0]

    cases = {
        "urdf_source": lambda r: tf(r).update(urdf_source="none"),
        "urdf_link_count": lambda r: tf(r).update(urdf_link_count=3),
        "declared_frames_missing": lambda r: tf(r).update(declared_frames_missing=[]),
        "frames_not_in_urdf": lambda r: tf(r).update(frames_not_in_urdf=["map"]),
        "declared_not_published finding": lambda r: tf(r).update(
            findings=[f for f in tf(r)["findings"] if f["kind"] != "declared_not_published"]
        ),
        "param_diff entries": lambda r: params(r)["param_diff"].pop(),
        "param_diff_summary": lambda r: params(r).update(
            param_diff_summary="0 same, 3 live_only (a, b, c)"
        ),
    }
    for needle, mutate in cases.items():
        code, failed = _declared_run(tmp_path, bench_truth, mutate)
        assert code == 1 and any(needle in m for m in failed), (needle, failed)


def test_a_wrong_param_value_or_status_fails(tmp_path: Path, bench_truth: dict) -> None:
    def wrong_value(r: dict) -> None:
        r["03p_node_info_params"]["parsed"][0]["param_diff"][1]["live"] = 11.0

    def wrong_status(r: dict) -> None:
        r["03p_node_info_params"]["parsed"][0]["param_diff"][0]["status"] = "different"

    def no_diff(r: dict) -> None:
        r["03p_node_info_params"]["parsed"][0]["param_diff"] = None

    for mutate in (wrong_value, wrong_status, no_diff):
        code, failed = _declared_run(tmp_path, bench_truth, mutate)
        assert code == 1 and "param_diff entries (get_node_info)" in failed


def test_the_tf_kinds_row_ignores_the_urdf_finding(tmp_path: Path, bench_truth: dict) -> None:
    results = _bench_results(bench_truth)
    _bag_results(bench_truth, results)
    code, report = _run(tmp_path, bench_truth, results, "--strict-shape")
    assert code == 0 and "finding kinds (inspect_tf)" in _metrics(report, "PASS")


def test_an_older_server_without_the_declared_fields_is_a_warning(
    tmp_path: Path, bench_truth: dict
) -> None:
    results = _bench_results(bench_truth)
    _bag_results(bench_truth, results)
    for key in ("urdf_source", "urdf_link_count", "declared_frames_missing", "frames_not_in_urdf"):
        del results["17_inspect_tf"]["parsed"][0][key]
    del results["03p_node_info_params"]["parsed"][0]["param_diff"]
    code, report = _run(tmp_path, bench_truth, results)
    assert code == 0
    assert {"URDF comparison (inspect_tf)", "param_diff (get_node_info)"} <= set(
        _metrics(report, "WARN")
    )


def test_a_missing_declared_answer_is_a_warning_not_a_failure(
    tmp_path: Path, bench_truth: dict
) -> None:
    results = _bench_results(bench_truth)
    _bag_results(bench_truth, results)
    del results["03p_node_info_params"]
    code, report = _run(tmp_path, bench_truth, results)
    assert code == 0 and "param_diff (get_node_info)" in _metrics(report, "WARN")


def test_the_kit_truth_has_no_declared_section(tmp_path: Path, omnisim: tuple[dict, dict]) -> None:
    _, report = _run(tmp_path, *omnisim)
    assert all(r["section"] != "declared" for r in report["rows"])


def test_bench_declared_truth_matches_what_the_real_code_computes() -> None:
    """The truth is derived from the bench's own constants: run TopicForge's code on them."""
    from topicforge.adapters.common.nodes import build_parameters
    from topicforge.adapters.common.param_diff import build_param_diff
    from topicforge.adapters.common.tf_tree import build_tf_report
    from topicforge.adapters.common.urdf import parse_urdf
    from topicforge.models import TfCapture, TfSample

    truth = bench.build_truth("humble", "rmw_fastrtps_cpp")
    urdf_truth, params_truth = truth["declared"]["urdf"], truth["declared"]["params"]
    model = parse_urdf(bench.urdf_text())
    assert len(model.links) == urdf_truth["link_count"]
    arm = model.joint_into(bench.URDF_UNPUBLISHED_LINK)
    assert arm is not None and arm.type == "revolute"
    edges = [*bench.TF_STATIC_EDGES, *bench.TF_STEADY_EDGES, bench.TF_FLAKY_EDGE]
    samples = [TfSample(parent=p, child=c, stamp_ns=0, received_ns=0) for p, c in edges]
    report = build_tf_report(
        TfCapture(static=samples[:3], dynamic=samples[3:], window_actual_s=1.0),
        2.0,
        None,
        None,
        urdf=model,
    )
    assert report.declared_frames_missing == urdf_truth["declared_frames_missing"]
    assert report.frames_not_in_urdf == urdf_truth["frames_not_in_urdf"]
    assert [f.kind for f in report.findings if f.kind == "declared_not_published"]
    # The file the bench reads, against the parameters /bench_robot declares (+ use_sim_time).
    text = (ROOT / "tests" / "integration" / "ros2" / bench.PARAMS_FILE_NAME).read_text("ascii")
    live = build_parameters({**bench.BENCH_PARAMS, "use_sim_time": False})[0]
    diff, summary, note = build_param_diff(text, bench.PARAMS_NODE, live)
    assert diff is not None and note is None
    assert [d.model_dump() for d in diff] == params_truth["differences"]
    assert (summary or "").startswith(f"{params_truth['same']} same (")


def test_the_declared_call_runs_in_the_bench_only_and_reads_the_shipped_file() -> None:
    kit = json.loads((ROOT / "scripts" / "ground_truth" / "calls.json").read_text("utf-8"))
    assert not any(c.get("name") == "03p_node_info_params" for c in kit)
    calls = json.loads(
        (ROOT / "scripts" / "ground_truth" / "calls_declared.json").read_text("utf-8")
    )
    (call,) = [c for c in calls if "name" in c]
    assert call["name"] == bench.PARAMS_CALL_NAME and call["tool"] == "get_node_info"
    assert call["args"]["node"] == bench.PARAMS_NODE
    assert call["args"]["params_file"].endswith("/tests/integration/ros2/" + bench.PARAMS_FILE_NAME)
    assert calls[-1] == {"settle_s": 3}
    script = (ROOT / "tests" / "integration" / "ros2" / "entrypoint.sh").read_text("utf-8")
    assert script.index("calls_declared.json") < script.index("calls_graph_diff.json")
    sample = next(c for c in kit if c.get("name") == "20_sample_{slug}")
    assert "/robot_description" in sample["exclude"]


def test_the_bench_publisher_uses_the_truth_constants() -> None:
    source = (ROOT / "tests" / "integration" / "ros2" / "publisher.py").read_text("ascii")
    for needle in ("model.urdf_text()", "model.BENCH_PARAMS", '"/robot_description"'):
        assert needle in source
    assert bench.BENCH_TOPICS["/robot_description"] == ("std_msgs/msg/String", True)
