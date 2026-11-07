"""The `sampler` section of scripts/ground_truth/compare.py, and how the bench uses it."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


compare = _load("gt_compare_sampler", ROOT / "scripts" / "ground_truth" / "compare.py")

_METRIC = "health_check sampling_backend"
_WRITERS = "sampler helper writers"


def _call(payload: Any) -> dict[str, Any]:
    return {
        "isError": False,
        "content": [json.dumps(payload)],
        "parsed": [payload],
        "_call": {"tool": "t", "args": {}, "wall_s": 0.1, "t_wall": 0.0},
    }


def _endpoint(guid: str, role: str, topic: str, node: str | None = None) -> dict[str, Any]:
    return {"participant_guid": guid, "role": role, "dds_topic": topic, "node_name": node}


def _endpoints(*rows: dict[str, Any], truncated: bool = False) -> dict[str, Any]:
    return _call({"endpoints": list(rows), "truncated": truncated})


def _rows(tmp_path: Path, results: dict[str, Any], *args: str) -> dict[str, dict[str, Any]]:
    folder = tmp_path / "results"
    folder.mkdir()
    for name, data in results.items():
        (folder / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")
    truth = tmp_path / "truth.json"
    truth.write_text("{}", encoding="utf-8")
    compare.main(["--truth", str(truth), "--results", str(folder), *args])
    report = json.loads((folder / "comparison.json").read_text(encoding="utf-8"))
    return {r["metric"]: r for r in report["rows"] if r["section"] == "sampler"}


@pytest.mark.parametrize(
    ("reported", "expected", "verdict"),
    [("rclpy", "rclpy", "PASS"), ("cli", "cli", "PASS"), ("cli", "rclpy", "FAIL")],
)
def test_the_row_on_the_backend(tmp_path: Path, reported: str, expected: str, verdict: str) -> None:
    health = _call({"sampling_backend": reported, "sampling_backend_note": "why"})
    rows = _rows(tmp_path, {"01_health_check": health}, "--expect-sampler", expected)
    assert rows[_METRIC]["verdict"] == verdict


def test_a_missing_health_call_fails_when_a_backend_is_expected(tmp_path: Path) -> None:
    assert _rows(tmp_path, {}, "--expect-sampler", "rclpy")[_METRIC]["verdict"] == "FAIL"


def test_no_expectation_no_row(tmp_path: Path) -> None:
    assert _rows(tmp_path, {"01_health_check": _call({"sampling_backend": "cli"})}) == {}


def _good() -> list[dict[str, Any]]:
    node = "/_topicforge_sampler_42"
    return [
        _endpoint("h", "writer", "ros_discovery_info"),
        _endpoint("h", "writer", "rt/parameter_events", node),
        _endpoint("h", "reader", "rt/sampler_hold", node),
        _endpoint("h", "reader", "ros_discovery_info"),
        _endpoint("robot", "writer", "rt/scan", "/bench_robot"),
    ]


def test_the_exact_writers_pass(tmp_path: Path) -> None:
    rows = _rows(tmp_path, {"60_sampler_endpoints": _endpoints(*_good())})
    assert rows[_WRITERS]["verdict"] == "PASS"


@pytest.mark.parametrize(
    "extra",
    ["rt/rosout", "rt/sampler_hold", "rq/_topicforge_sampler_42/get_parametersRequest"],
)
def test_one_more_writer_fails(tmp_path: Path, extra: str) -> None:
    rows = [*_good(), _endpoint("h", "writer", extra)]
    report = _rows(tmp_path, {"60_sampler_endpoints": _endpoints(*rows)})
    assert report[_WRITERS]["verdict"] == "FAIL"


def test_a_missing_writer_fails(tmp_path: Path) -> None:
    rows = [r for r in _good() if r["dds_topic"] != "ros_discovery_info"]
    report = _rows(tmp_path, {"60_sampler_endpoints": _endpoints(*rows)})
    assert report[_WRITERS]["verdict"] == "FAIL"


def test_no_helper_seen_fails_and_says_when_the_list_was_cut(tmp_path: Path) -> None:
    rows = [_endpoint("robot", "writer", "rt/scan", "/bench_robot")]
    report = _rows(tmp_path, {"60_sampler_endpoints": _endpoints(*rows, truncated=True)})
    assert report[_WRITERS]["verdict"] == "FAIL"
    assert "truncated" in report[_WRITERS]["note"]


def test_a_failed_call_fails(tmp_path: Path) -> None:
    failed = {"isError": True, "content": ["boom"], "parsed": [None], "_call": {"wall_s": 0.1}}
    report = _rows(tmp_path, {"60_sampler_endpoints": failed})
    assert report[_WRITERS]["verdict"] == "FAIL"


def test_a_helper_name_is_a_cli_participant_for_the_dds_counts() -> None:
    assert compare._is_cli_participant({"name": "/", "node_names": ["/_topicforge_sampler_7"]})
    assert not compare._is_cli_participant({"name": "/", "node_names": ["/bench_robot"]})


def test_skip_section_leaves_it_out(tmp_path: Path) -> None:
    results = {"01_health_check": _call({"sampling_backend": "cli"})}
    rows = _rows(tmp_path, results, "--expect-sampler", "rclpy", "--skip-section", "sampler")
    assert rows == {}


def test_skipping_graph_diff_drops_its_rows_only(tmp_path: Path) -> None:
    calls = {"first": {"call": "a"}, "steady": {"call": "b", "changes": 0}}
    truth = {"graph_diff": {**calls, "v2": {"call": "c"}, "bag": {"call": "d"}}}

    def sections(*skip: str) -> set[str]:
        report, _ = compare.compare(truth, tmp_path, skip)
        return {row["section"] for row in report.rows}

    assert "graph_diff" in sections()
    assert "graph_diff" not in sections("graph_diff")


def test_the_bench_runs_a_pass_per_backend_before_the_graph_changes() -> None:
    script = (ROOT / "tests" / "integration" / "ros2" / "entrypoint.sh").read_text(encoding="utf-8")
    cli = script.index("sampler_pass cli")
    rclpy = script.index(
        'sampler_pass rclpy "$GT/calls_sampler_window.json" "$GT/calls_sampler_writers.json"'
    )
    graph_diff = script.index('"$GT/calls_graph_diff.json"')
    assert cli < rclpy < graph_diff
    assert "--skip-section graph_diff" in script
    assert "--expect-sampler rclpy" in script
    assert '[ "$CLI_RC" -eq 0 ] && [ "$RCLPY_RC" -eq 0 ]' in script


def test_the_workflow_triggers_on_the_helper_paths() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ros2-live.yml").read_text(encoding="utf-8")
    for path in ("adapters/ros2_live/**", "common/sampler_frames.py", "common/ros_python.py"):
        assert workflow.count(path) == 2, path


_WINDOW_TRUTH = {"rates": {"per_topic": {"/clock": {"rate_wall_hz": 50.0}}}}


def _window(received: int, span: float, hz: float, returned: int = 5) -> dict[str, Any]:
    data = _call(
        {
            "count": returned,
            "rate": {"message_count": received, "window_s": span, "observed_frequency_hz": hz},
        }
    )
    data["_call"]["args"] = {"topic": "/clock", "count": 5, "window_s": 5}
    return data


def _window_rows(
    tmp_path: Path, data: dict[str, Any], expect: str | None = "rclpy"
) -> dict[str, dict[str, Any]]:
    folder = tmp_path / "results"
    folder.mkdir()
    (folder / "61_window_clock.json").write_text(json.dumps(data), encoding="utf-8")
    truth = tmp_path / "truth.json"
    truth.write_text(json.dumps(_WINDOW_TRUTH), encoding="utf-8")
    args = ["--expect-sampler", expect] if expect else []
    compare.main(["--truth", str(truth), "--results", str(folder), *args])
    report = json.loads((folder / "comparison.json").read_text(encoding="utf-8"))
    return {r["metric"]: r for r in report["rows"] if r["section"] == "sampler"}


def test_a_window_with_the_publisher_rate_passes(tmp_path: Path) -> None:
    rows = _window_rows(tmp_path, _window(247, 5.0, 50.0))
    assert rows["/clock window_s"]["verdict"] == "PASS"


@pytest.mark.parametrize(
    "bad",
    [
        _window(100, 5.0, 50.0),
        _window(247, 8.0, 50.0),
        _window(247, 5.0, 30.0),
        _window(247, 5.0, 50.0, 2),
    ],
)
def test_a_window_that_disagrees_with_the_publisher_fails(
    tmp_path: Path, bad: dict[str, Any]
) -> None:
    assert _window_rows(tmp_path, bad)["/clock window_s"]["verdict"] == "FAIL"


def test_the_cli_pass_wants_the_refusal(tmp_path: Path) -> None:
    refused = {
        "isError": True,
        "content": ["`window_s` needs the sampling helper: TOPICFORGE_ROS_SAMPLER=cli"],
        "_call": {"tool": "t", "args": {"topic": "/clock", "count": 5, "window_s": 5}},
    }
    rows = _window_rows(tmp_path, refused, "cli")
    assert rows["/clock window_s refused on the CLI"]["verdict"] == "PASS"


def test_the_cli_pass_fails_when_a_window_answers(tmp_path: Path) -> None:
    rows = _window_rows(tmp_path, _window(247, 5.0, 50.0), "cli")
    assert rows["/clock window_s refused on the CLI"]["verdict"] == "FAIL"
