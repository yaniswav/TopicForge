#!/usr/bin/env python3
"""Compare TopicForge MCP results with a ground_truth.json.

    python compare.py --truth ground_truth.json --results DIR [--out COMPARISON.md]
                      [--bag-truth BAG_DIR] [--strict-shape] [--label VERSION]

Two producers write the truth, in the same schema subset: the OmniSim repro kit (external,
manual, kept as confirmation) and the Docker bench publisher
(tests/integration/ros2/ground_truth.py, a CI gate). `DIR` holds what drive.py saved. The
comparator writes COMPARISON.md and comparison.json next to the results (or `--out`), prints
the table and ends with `RESULT: PASS|FAIL`. Exit code 1 when any row is FAIL (and, with
`--strict-shape`, SHAPE), 0 otherwise. Standard library plus, only for `--bag-truth`, the
`rosbags` package that TopicForge already depends on.

Verdicts: PASS (matches), FAIL (a mismatch with the truth), WARN (a known finding, a field that
is absent in 0.6.x output, or a deviation that depends on the machine), SHAPE (the tool's output
no longer has the expected shape; reported, never a crash), INFO (informative).

Sections: graph (topics, types, counts, QoS per side, nodes, use_sim_time per node), scan
(geometry, every range, wall and sector minima), stamps (stamp_source per topic), rates
(verdict and frequency, simulated and wall time), bag, dds. Reads output contract 2 (0.7:
listing envelopes, publisher_qos, duration_s, dds_topic/ros_topic) and tolerates 0.6.x shapes.

Tolerances (the constants below; each row names the one it used):

    scan header floats      angle_min/max, range_min/max, time_increment, scan_time  1e-6;
                            angle_increment 1e-9 (the publisher rounds to float32 first)
    every range             1e-5 m against lidar.published_ranges; +inf, "inf" and null are equal
    wall / sector minima    0.005 m against the geometry; beam index exact unless the wall has
                            `beam_index_tolerance`
    stamps                  header/payload stamp in (0, 1e7) s (sim time, not epoch); "none" is 0
    rates                   relative error <= 15 % PASS, <= 50 % WARN, above FAIL; only with
                            message_count >= 10; the verdict is WARN-only
    STABLE_CV               a fixed-rate topic (rates.fixed_rate_topics) with interval_cv >= 0.2
                            is a WARN (CONTRACT.md section 4); every cv is listed at the end
    bag                     counts, duration (1e-6 s), first/last ns exact; rate 0.1 % (>= 1e-3 Hz)
    pose (stationary robot) 1e-4 m
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

ANGLE_TOL = {
    "angle_min": 1e-6,
    "angle_max": 1e-6,
    "angle_increment": 1e-9,
    "range_min": 1e-6,
    "range_max": 1e-6,
    "time_increment": 1e-6,
    "scan_time": 1e-6,
}
RANGE_TOL = 1e-5
WALL_TOL = 0.005
POSE_TOL = 1e-4
RATE_PASS, RATE_WARN = 0.15, 0.5
RATE_MIN_MESSAGES = 10
STABLE_CV = 0.2
BAG_DURATION_TOL_S = 1e-6
INFRA_TOPICS = ("/parameter_events", "/rosout")
SAMPLE_PREFIXES = ("09_", "10_", "11_", "12_", "20_sample_")
# Expected stamp_source per topic when the truth carries no `stamps` map (the OmniSim kit).
DEFAULT_STAMPS = {
    "/odom": "header",
    "/imu/data": "header",
    "/gps/local": "header",
    "/husky/joint_states": "header",
    "/clock": "payload",
    "/tf": "payload",
}
DEFAULT_SECTORS = {"front": "front", "left": "left", "right_box": "right", "rear": "rear"}


# ---------------------------------------------------------------------------- helpers
class Report:
    """Rows of the comparison, the measured rate CVs and the per-call wall times."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.rate_cv: dict[str, dict[str, Any]] = {}

    def add(
        self,
        section: str,
        metric: str,
        truth: Any,
        got: Any,
        verdict: str,
        note: str = "",
        wall_s: float | None = None,
    ) -> None:
        self.rows.append(
            {
                "section": section,
                "metric": metric,
                "truth": _plain(truth),
                "topicforge": _plain(got),
                "verdict": verdict,
                "note": note,
                "wall_s": wall_s,
            }
        )

    def counts(self) -> collections.Counter:
        return collections.Counter(r["verdict"] for r in self.rows)


def _plain(value: Any) -> Any:
    """A JSON-safe copy of a value for comparison.json."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


class Results:
    """The result files drive.py saved in one directory."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.times: dict[str, float | None] = {}

    def load(self, name: str) -> dict[str, Any] | None:
        file = self.path / f"{name}.json"
        if not file.is_file():
            return None
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except ValueError:
            return None
        self.times[name] = (data.get("_call") or {}).get("wall_s")
        return data

    def names(self, *prefixes: str) -> list[str]:
        return sorted(p.stem for p in self.path.glob("*.json") if p.name.startswith(prefixes))


def tool_err(data: dict[str, Any] | None) -> str | None:
    """Why a call failed (`missing`, an exception, an isError text), else None."""
    if data is None:
        return "missing"
    if "exception" in data:
        return str(data["exception"])
    if data.get("isError"):
        return " ".join(data.get("content") or [])[:200]
    return None


def objs(data: dict[str, Any] | None) -> list[Any]:
    """Parsed JSON objects of every content block; items of a top-level list are flattened."""
    out: list[Any] = []
    for part in (data or {}).get("parsed") or []:
        if isinstance(part, list):
            out += part
        elif part is not None:
            out.append(part)
    return out


def listing(data: dict[str, Any] | None, key: str) -> list[Any]:
    """Items of a listing: the `key` array of a 0.7 envelope, else the flattened 0.6.x list."""
    out = objs(data)
    if len(out) == 1 and isinstance(out[0], dict) and isinstance(out[0].get(key), list):
        return out[0][key]
    return out


def pick(data: dict[str, Any], *names: str, default: Any = None) -> Any:
    """First present key of a dict (0.7 name first, 0.6.x name after)."""
    for name in names:
        if name in data:
            return data[name]
    return default


def num(value: Any) -> float:
    """A range value as float; None, "inf" and +inf are +inf."""
    if value is None:
        return math.inf
    if isinstance(value, str):
        text = value.strip().lower()
        table = {
            "inf": math.inf,
            "+inf": math.inf,
            "infinity": math.inf,
            "-inf": -math.inf,
            "-infinity": -math.inf,
            "nan": math.nan,
        }
        if text in table:
            return table[text]
        try:
            return float(text)
        except ValueError:
            return math.nan
    return float(value)


def same_range(a: float, b: float, tol: float = RANGE_TOL) -> bool:
    if math.isnan(a) or math.isnan(b):
        return math.isnan(a) and math.isnan(b)
    if math.isinf(a) or math.isinf(b):
        return a == b
    return abs(a - b) <= tol


def fmt(value: Any) -> str:
    return f"{value:.9g}" if isinstance(value, float) else str(value)


def slug(name: str) -> str:
    return name.strip("/").replace("/", "_") or "root"


def node_full_name(endpoint: dict[str, Any]) -> str:
    ns = endpoint.get("node_namespace") or "/"
    return ns.rstrip("/") + "/" + endpoint["node_name"]


def is_pub(endpoint: dict[str, Any]) -> bool:
    return str(endpoint["endpoint_type"]).upper().startswith("PUB")


def guard(section: str) -> Callable:
    """Turn an exception inside a section into one SHAPE row instead of a crash."""

    def deco(fn: Callable) -> Callable:
        def wrapped(gt: dict, res: Results, rep: Report) -> None:
            try:
                fn(gt, res, rep)
            except Exception as exc:
                rep.add(
                    section,
                    fn.__name__,
                    "-",
                    "-",
                    "SHAPE",
                    f"output shape changed ({type(exc).__name__}: {exc})",
                )

        wrapped.__name__ = fn.__name__
        return wrapped

    return deco


def _qos_pair(q: dict[str, Any] | None) -> tuple[str, str]:
    q = q or {}
    return str(q.get("reliability")).lower(), str(q.get("durability")).lower()


def _qos_ok(got: tuple[str, str], want: set[tuple[str, str]]) -> bool:
    return got in want or (len(want) > 1 and "mixed" in got)


# ---------------------------------------------------------------------------- graph
def _truth_topics(gt: dict) -> dict[str, dict]:
    return {t["name"]: t for t in gt["graph"]["before_capture"]["topics"]}


def _ignored(topic: dict) -> bool:
    """Counts and QoS of the topic are not part of the truth (infrastructure topics)."""
    return topic.get("counts") == "ignore"


@guard("graph")
def check_graph(gt: dict, res: Results, rep: Report) -> None:
    truth = _truth_topics(gt)
    data = res.load("02_list_topics")
    err = tool_err(data)
    if err:
        rep.add("graph", "list_topics", f"{len(truth)} topics", err, "FAIL", "tool failed")
        return
    got = {o["name"]: o for o in listing(data, "topics")}
    wall = res.times.get("02_list_topics")
    same = set(got) == set(truth)
    rep.add(
        "graph",
        "topic set",
        f"{len(truth)} names",
        f"{len(got)} names",
        "PASS" if same else "FAIL",
        ""
        if same
        else f"missing {sorted(set(truth) - set(got))} extra {sorted(set(got) - set(truth))}",
        wall,
    )
    bad_t = [n for n in truth if n in got and [got[n]["message_type"]] != truth[n]["types"]]
    rep.add(
        "graph",
        "topic types",
        "per before_capture",
        bad_t or "identical",
        "PASS" if not bad_t else "FAIL",
        "",
        wall,
    )
    bad_c = [
        f"{n} {got[n]['publisher_count']}/{got[n]['subscriber_count']} vs "
        f"{truth[n]['publisher_count']}/{truth[n]['subscription_count']}"
        for n in truth
        if n in got
        and not _ignored(truth[n])
        and (got[n]["publisher_count"], got[n]["subscriber_count"])
        != (truth[n]["publisher_count"], truth[n]["subscription_count"])
    ]
    ignored = sorted(n for n in truth if _ignored(truth[n]))
    rep.add(
        "graph",
        "publisher/subscription counts",
        "per before_capture",
        bad_c or "identical",
        "PASS" if not bad_c else "FAIL",
        f"not compared: {ignored}" if ignored else "",
        wall,
    )
    _check_topic_qos(truth, res, rep)
    _check_health(gt, res, rep)
    _check_nodes(gt, res, rep)


def _check_topic_qos(truth: dict[str, dict], res: Results, rep: Report) -> None:
    bad: list[str] = []
    null_subs: list[str] = []
    for name in sorted(truth):
        if _ignored(truth[name]):
            continue
        data = res.load("03_topic_info_" + slug(name))
        if data is None or tool_err(data):
            bad.append(f"{name}: get_topic_info {tool_err(data)}")
            continue
        info = (objs(data) or [{}])[0]
        eps = truth[name].get("endpoints", [])
        pubs = [e for e in eps if is_pub(e)]
        subs = [e for e in eps if not is_pub(e)]
        v2 = "publisher_qos" in info
        for side, group, key, old in (
            ("publishers", pubs, "publisher_qos", "qos_reliability"),
            ("subscribers", subs, "subscription_qos", None),
        ):
            if not group:
                continue
            want = {(e["reliability"].lower(), e["durability"].lower()) for e in group}
            if v2:
                seen = _qos_pair(info.get(key))
            elif old:
                seen = (str(info.get(old)).lower(), str(info.get("qos_durability")).lower())
            else:
                null_subs.append(name)
                continue
            if not _qos_ok(seen, want):
                bad.append(f"{name} ({side}): {seen} vs {sorted(want)}")
        if v2:
            pn, sn = info.get("publisher_nodes") or [], info.get("subscriber_nodes") or []
            if len(pn) > info.get("publisher_count", 0) or len(sn) > info.get(
                "subscriber_count", 0
            ):
                bad.append(f"{name}: more nodes than endpoints ({len(pn)}/{len(sn)})")
            want_p = sorted(node_full_name(e) for e in pubs)
            if pn and want_p and sorted(pn) != want_p:
                bad.append(f"{name}: publisher_nodes {sorted(pn)} vs {want_p}")
    rep.add(
        "graph",
        "QoS per side and node names (get_topic_info)",
        "per endpoints in the truth",
        bad or "identical",
        "PASS" if not bad else "FAIL",
        "",
    )
    if null_subs:
        rep.add(
            "graph",
            "QoS of subscriber-only topics",
            "subscribers reported",
            "null",
            "WARN",
            f"D2 (0.6.x): {null_subs} report no subscription QoS",
        )


def _check_health(gt: dict, res: Results, rep: Report) -> None:
    data = res.load("01_health_check")
    if not data or tool_err(data):
        return
    health = (objs(data) or [{}])[0]
    wall = res.times.get("01_health_check")
    ros = gt.get("ros", {})
    if ros.get("distro"):
        rep.add(
            "graph",
            "health_check ros2_distro",
            ros["distro"],
            health.get("ros2_distro"),
            "PASS" if health.get("ros2_distro") == ros["distro"] else "FAIL",
            "",
            wall,
        )
    keys = [k for k in health if "rmw" in k.lower()]
    if ros.get("rmw_implementation"):
        got = health[keys[0]] if keys else "not reported"
        src = health.get("rmw_source")
        rep.add(
            "graph",
            "RMW reported by TopicForge",
            ros["rmw_implementation"],
            got,
            "PASS" if keys and got == ros["rmw_implementation"] else "WARN",
            f"rmw_source {src}" if keys else "D4: no RMW field (0.6.x)",
        )
    cv = health.get("contract_version")
    rep.add(
        "graph",
        "contract_version",
        "2 (0.7)",
        cv if cv is not None else "absent (0.6.x: 1)",
        "INFO",
    )


def _expected_interfaces(gt: dict) -> dict[str, dict[str, set]]:
    """Per node: the user topics it publishes and subscribes, from the truth endpoints."""
    out: dict[str, dict[str, set]] = {}
    for topic in gt["graph"]["before_capture"]["topics"]:
        if topic["name"] in INFRA_TOPICS:
            continue
        for ep in topic.get("endpoints", []):
            side = "publishers" if is_pub(ep) else "subscribers"
            kind = out.setdefault(node_full_name(ep), {"publishers": set(), "subscribers": set()})
            kind[side].add((topic["name"], topic["types"][0]))
    return out


def _check_nodes(gt: dict, res: Results, rep: Report) -> None:
    before = gt["graph"]["before_capture"]
    truth_nodes = sorted(before.get("nodes") or [])
    data = res.load("02n_list_nodes")
    if data is None:
        rep.add(
            "graph",
            "list_nodes",
            f"{len(truth_nodes)} nodes",
            "absent",
            "WARN",
            "no list_nodes result (0.6.x or an older calls file)",
        )
        return
    err = tool_err(data)
    if err:
        rep.add("graph", "list_nodes", f"{len(truth_nodes)} nodes", err, "FAIL", "tool failed")
        return
    listed = listing(data, "nodes")
    names = sorted(n["full_name"] for n in listed)
    wall = res.times.get("02n_list_nodes")
    rep.add(
        "graph",
        "node set (list_nodes)",
        truth_nodes,
        names,
        "PASS" if names == truth_nodes else "FAIL",
        "",
        wall,
    )
    dups = (objs(data) or [{}])[0].get("duplicates") if objs(data) else None
    rep.add(
        "graph",
        "duplicate node names",
        "none",
        dups or "none",
        "PASS" if not dups else "FAIL",
        "",
        wall,
    )
    want_iface = _expected_interfaces(gt)
    want_sim = before.get("use_sim_time") or {}
    bad_iface: list[str] = []
    bad_sim: list[str] = []
    seen = 0
    for node in truth_nodes:
        info_data = res.load("03n_node_info_" + slug(node))
        if info_data is None or tool_err(info_data):
            bad_iface.append(f"{node}: get_node_info {tool_err(info_data)}")
            continue
        seen += 1
        info = (objs(info_data) or [{}])[0]
        for side in ("publishers", "subscribers"):
            got = {
                (i["name"], i["type"]) for i in info.get(side, []) if i["name"] not in INFRA_TOPICS
            }
            want = want_iface.get(node, {}).get(side, set())
            if got != want:
                bad_iface.append(f"{node} {side}: {sorted(got ^ want)}")
        if node in want_sim:
            want_value = want_sim[node]
            got_value = info.get("use_sim_time")
            ok = got_value == want_value
            if want_value is None:
                ok = got_value is None and bool(info.get("use_sim_time_note"))
            if not ok:
                bad_sim.append(f"{node}: {got_value} vs {want_value}")
    rep.add(
        "graph",
        "node interfaces (get_node_info)",
        f"{len(truth_nodes)} nodes",
        bad_iface or "identical",
        "PASS" if not bad_iface else "FAIL",
        "publishers and subscribers without /parameter_events and /rosout",
    )
    rep.add(
        "graph",
        "use_sim_time per node (get_node_info)",
        {n: want_sim.get(n) for n in truth_nodes},
        bad_sim or "identical",
        "PASS" if not bad_sim else "FAIL",
        "null in the truth means the node never answers: null plus a use_sim_time_note",
    )


# ---------------------------------------------------------------------------- scan
@guard("scan")
def check_scan(gt: dict, res: Results, rep: Report) -> None:
    lidar = gt["lidar"]
    data = res.load("07_sample_scan_full")
    err = tool_err(data)
    if err:
        rep.add("scan", "sample_messages /scan full", "-", err, "FAIL")
        return
    sample = objs(data)[0]["samples"][0]
    payload, wall = sample["payload"], res.times.get("07_sample_scan_full")
    ranges = [num(v) for v in payload["ranges"]]
    rep.add(
        "scan",
        "n_beams",
        lidar["n_beams"],
        len(ranges),
        "PASS" if len(ranges) == lidar["n_beams"] else "FAIL",
        "",
        wall,
    )
    for key, tol in ANGLE_TOL.items():
        ok = abs(payload[key] - lidar[key]) <= tol
        rep.add(
            "scan",
            key,
            fmt(lidar[key]),
            fmt(payload[key]),
            "PASS" if ok else "FAIL",
            f"tol {tol}",
            wall,
        )
    frame = payload["header"]["frame_id"]
    rep.add(
        "scan",
        "frame_id",
        lidar["frame_id"],
        frame,
        "PASS" if frame == lidar["frame_id"] else "FAIL",
        "",
        wall,
    )
    n_int = len(payload.get("intensities") or [])
    rep.add(
        "scan",
        "n_intensities",
        lidar["n_intensities"],
        n_int,
        "PASS" if n_int == lidar["n_intensities"] else "FAIL",
        "",
        wall,
    )
    expected = [num(v) for v in lidar["published_ranges"]]
    bad = [
        (j, a, b)
        for j, (a, b) in enumerate(zip(ranges, expected, strict=False))
        if not same_range(a, b)
    ]
    finite = sum(not math.isinf(v) for v in expected)
    rep.add(
        "scan",
        f"{len(expected)} ranges vs lidar.published_ranges (null=inf)",
        f"{finite} finite",
        f"{len(bad)} mismatches" + (f" {bad[:3]}" if bad else ""),
        "PASS" if not bad and len(ranges) == len(expected) else "FAIL",
        f"tol {RANGE_TOL}",
        wall,
    )
    kinds = collections.Counter(
        "str" if isinstance(v, str) else ("null" if v is None else "float")
        for v in payload["ranges"]
    )
    rep.add(
        "scan",
        "encoding of no-return beams",
        "+inf (null in json)",
        dict(kinds),
        "INFO" if kinds.get("str") or kinds.get("null") else "PASS",
        "D7: inf as string mixed with floats" if kinds.get("str") else "",
    )
    _check_walls(gt, ranges, rep, wall)
    _check_default_truncation(lidar, res, rep)
    _check_summary(gt, res, rep)
    _check_edge_scan(gt, res, rep)


def _check_walls(gt: dict, ranges: list[float], rep: Report, wall: float | None) -> None:
    for key, w in gt.get("walls", {}).items():
        if not w.get("in_field_of_view"):
            continue
        j = w["beam_index"]
        want = w.get("published_range_m", w["expected_range_m"])
        ok = (
            j < len(ranges)
            and abs(ranges[j] - want) <= RANGE_TOL
            and abs(ranges[j] - w["expected_range_m"]) <= WALL_TOL
        )
        rep.add(
            "scan",
            f"{key} beam {j}",
            f"{want:.6f} (geometry {w['expected_range_m']:.4f})",
            f"{ranges[j]:.6f}" if j < len(ranges) else "n/a",
            "PASS" if ok else "FAIL",
            f"tol {WALL_TOL} vs geometry",
            wall,
        )


def _check_default_truncation(lidar: dict, res: Results, rep: Report) -> None:
    data = res.load("08_sample_scan_defaults")
    if not data or tool_err(data):
        return
    payload = objs(data)[0]["samples"][0]["payload"]
    ok = len(payload["ranges"]) < lidar["n_beams"] and "ranges" in (
        payload.get("_truncated_fields") or []
    )
    rep.add(
        "scan",
        "default truncation flagged",
        "ranges cut + _truncated_fields",
        f"{len(payload['ranges'])} beams, {payload.get('_truncated_fields')}",
        "PASS" if ok else "WARN",
        "",
        res.times.get("08_sample_scan_defaults"),
    )


def _check_summary(gt: dict, res: Results, rep: Report) -> None:
    data = res.load("07_sample_scan_full") or res.load("08_sample_scan_defaults")
    if data is None or tool_err(data):
        rep.add("scan", "scan summary", "-", tool_err(data) or "no scan sample", "WARN")
        return
    samples = objs(data)[0].get("samples") or []
    summary = (samples[0] if samples else {}).get("summary")
    wall = res.times.get("07_sample_scan_full")
    if not summary:
        rep.add(
            "scan",
            "scan summary",
            "present (0.7)",
            "absent",
            "WARN",
            "0.6.x output has no summary",
            wall,
        )
        return
    walls = gt.get("walls", {})
    rep.add(
        "scan",
        "summary beam_count",
        gt["lidar"]["n_beams"],
        summary.get("beam_count"),
        "PASS" if summary.get("beam_count") == gt["lidar"]["n_beams"] else "FAIL",
        "",
        wall,
    )
    rep.add(
        "scan",
        "summary frame_id",
        gt["lidar"]["frame_id"],
        summary.get("frame_id"),
        "PASS" if summary.get("frame_id") == gt["lidar"]["frame_id"] else "FAIL",
        "",
        wall,
    )
    visible = [w for w in walls.values() if w.get("in_field_of_view")]
    if visible:
        want = min(visible, key=lambda w: w["expected_range_m"])
        got = summary.get("closest_obstacle") or {}
        tol_idx = want.get("beam_index_tolerance", 0)
        ok = (
            abs(got.get("beam_index", -(10**9)) - want["beam_index"]) <= tol_idx
            and abs(num(got.get("range")) - want["expected_range_m"]) <= WALL_TOL
        )
        rep.add(
            "scan",
            "summary closest obstacle",
            f"beam {want['beam_index']}, {want['expected_range_m']:.4f} m",
            f"beam {got.get('beam_index')}, {fmt(got.get('range'))} m",
            "PASS" if ok else "FAIL",
            f"tol {WALL_TOL} m",
            wall,
        )
    for key, w in walls.items():
        sector = w.get("sector") or DEFAULT_SECTORS.get(key)
        if not sector:
            continue
        got_sector = (summary.get("sectors") or {}).get(sector) or {}
        closest = got_sector.get("closest")
        if not w.get("in_field_of_view"):
            ok = closest is None and got_sector.get("beam_count") == 0
            rep.add(
                "scan",
                f"summary {sector} sector (outside the field of view)",
                "null, no beams",
                f"closest {closest}, beams {got_sector.get('beam_count')}",
                "PASS" if ok else "FAIL",
                got_sector.get("note") or "",
                wall,
            )
            continue
        closest = closest or {}
        tol_idx = w.get("beam_index_tolerance", 0)
        ok = (
            bool(closest)
            and abs(num(closest.get("range")) - w["expected_range_m"]) <= WALL_TOL
            and abs(closest.get("beam_index", -(10**9)) - w["beam_index"]) <= tol_idx
        )
        rep.add(
            "scan",
            f"summary {sector} sector minimum",
            f"{w['expected_range_m']:.4f} m at beam {w['beam_index']}",
            f"{fmt(closest.get('range'))} m at beam {closest.get('beam_index')}",
            "PASS" if ok else "FAIL",
            f"tol {WALL_TOL} m",
            wall,
        )


def _check_edge_scan(gt: dict, res: Results, rep: Report) -> None:
    edge = gt.get("edge_scan")
    if not edge:
        return
    data = res.load("20_sample_" + slug(edge["topic"]))
    err = tool_err(data)
    if err:
        rep.add("scan", f"{edge['topic']} non-finite ranges", "-", err, "FAIL")
        return
    got = [num(v) for v in objs(data)[0]["samples"][0]["payload"]["ranges"]]
    want = [num(v) for v in edge["ranges"]]
    ok = len(got) == len(want) and all(same_range(a, b) for a, b in zip(got, want, strict=False))
    rep.add(
        "scan",
        f"{edge['topic']} inf/nan/-inf/finite ranges",
        edge["ranges"],
        [str(v) for v in got],
        "PASS" if ok else "FAIL",
        "",
        res.times.get("20_sample_" + slug(edge["topic"])),
    )


# ---------------------------------------------------------------------------- stamps
def _sample_files(res: Results) -> list[tuple[str, dict, str]]:
    out = []
    for name in res.names(*SAMPLE_PREFIXES):
        data = res.load(name)
        topic = ((data or {}).get("_call") or {}).get("args", {}).get("topic")
        if data is not None and topic:
            out.append((name, data, topic))
    return out


@guard("stamps")
def check_stamps(gt: dict, res: Results, rep: Report) -> None:
    expect = gt.get("stamps") or DEFAULT_STAMPS
    for name, data, topic in _sample_files(res):
        want = expect.get(topic)
        wall = res.times.get(name)
        err = tool_err(data)
        if err:
            rep.add("stamps", f"{topic} samples", "-", err, "FAIL", "", wall)
            continue
        samples = objs(data)[0]["samples"]
        sources = {s.get("stamp_source") for s in samples}
        stamps = [s.get("timestamp_ns") for s in samples]
        if want is None:
            rep.add(
                "stamps",
                f"{topic} stamp_source",
                "not in the truth",
                sorted(map(str, sources)),
                "INFO",
                "",
                wall,
            )
            continue
        if want == "none":
            ok = sources == {"none"} and all(t == 0 for t in stamps)
            rep.add(
                "stamps",
                f"{topic} stamp_source",
                "none, timestamp_ns 0",
                sorted(map(str, sources)),
                "PASS" if ok else "FAIL",
                "",
                wall,
            )
            continue
        zero = all(t == 0 for t in stamps)
        if zero and sources == {"none"}:
            rep.add(
                "stamps",
                f"{topic} stamp_source",
                f"{want}, sim time",
                f"timestamp_ns=0, stamp_source={sorted(map(str, sources))}",
                "WARN",
                "D1 (0.6.x): stamp reported as 0 / none although the message carries time",
                wall,
            )
            continue
        small = all(isinstance(t, (int, float)) and 0 < t / 1e9 < 1e7 for t in stamps)
        ok = sources == {want} and small
        rep.add(
            "stamps",
            f"{topic} stamp_source",
            f"{want}, sim time (0 < s < 1e7)",
            f"{sorted(map(str, sources))}, first {stamps[0] / 1e9:.3f} s",
            "PASS" if ok else "FAIL",
            "",
            wall,
        )
        if want == "payload" and not zero:
            rep.add(
                "stamps",
                f"{topic} stamps increasing",
                "monotonic",
                [round(t / 1e9, 3) for t in stamps],
                "PASS" if stamps == sorted(stamps) else "FAIL",
                "",
                wall,
            )
    _check_pose(gt, res, rep)


def _check_pose(gt: dict, res: Results, rep: Report) -> None:
    pose_truth = (gt.get("robot") or {}).get("pose_used_for_geometry")
    for name, data, topic in _sample_files(res):
        if topic != "/odom" or not pose_truth or tool_err(data):
            continue
        pos = objs(data)[0]["samples"][0]["payload"]["pose"]["pose"]["position"]
        ok = (
            abs(pos["x"] - pose_truth["x"]) < POSE_TOL
            and abs(pos["y"] - pose_truth["y"]) < POSE_TOL
        )
        rep.add(
            "stamps",
            "/odom position vs robot pose (stationary)",
            f"({pose_truth['x']:.3g},{pose_truth['y']:.3g})",
            f"({pos['x']:.3g},{pos['y']:.3g})",
            "PASS" if ok else "FAIL",
            f"tol {POSE_TOL} m",
            res.times.get(name),
        )


# ---------------------------------------------------------------------------- rates
@guard("rates")
def check_rates(gt: dict, res: Results, rep: Report) -> None:
    truth = gt.get("rates") or {}
    per_topic = truth.get("per_topic") or {}
    fixed = set(truth.get("fixed_rate_topics") or [])
    verdicts_ok = tuple(truth.get("expected_verdicts") or ("stable", "jittery"))
    blocks: list[tuple[str, dict, float | None]] = []
    scan_rate = res.load("08c_sample_scan_rate")
    if scan_rate and not tool_err(scan_rate):
        blocks.append(
            ("/scan", objs(scan_rate)[0].get("rate"), res.times.get("08c_sample_scan_rate"))
        )
    for name, data, topic in _sample_files(res):
        if not tool_err(data):
            blocks.append((topic, objs(data)[0].get("rate"), res.times.get(name)))
    if not any(b[1] for b in blocks):
        rep.add(
            "rates", "rate block", "present (0.7)", "absent", "WARN", "0.6.x output has no rate"
        )
        return
    for topic, rate, wall in blocks:
        if not rate:
            continue
        count, cv = rate.get("message_count"), rate.get("interval_cv")
        verdict = rate.get("verdict")
        if cv is not None:
            rep.rate_cv[topic] = {
                "interval_cv": cv,
                "message_count": count,
                "verdict": verdict,
                "basis": rate.get("basis"),
            }
        if topic in fixed and cv is not None and verdict != "insufficient" and cv >= STABLE_CV:
            rep.add(
                "rates",
                f"{topic} STABLE_CV",
                f"cv < {STABLE_CV} on a fixed-rate publisher",
                f"cv {cv} (n={count})",
                "WARN",
                "CONTRACT.md section 4: the measuring path would be that noisy, review the threshold",
                wall,
            )
        if verdict == "insufficient" or (count or 0) < RATE_MIN_MESSAGES:
            rep.add(
                "rates",
                f"{topic} rate verdict",
                "sample with count >= 10",
                f"{verdict} (n={count})",
                "INFO",
                "too few messages to compare",
                wall,
            )
            continue
        rep.add(
            "rates",
            f"{topic} rate verdict",
            "/".join(verdicts_ok),
            f"{verdict} (n={count}, cv {cv})",
            "PASS" if verdict in verdicts_ok else "WARN",
            "machine dependent: never FAIL",
            wall,
        )
        _compare_frequencies(per_topic.get(topic) or {}, topic, rate, rep, wall)


def _compare_frequencies(
    truth: dict, topic: str, rate: dict, rep: Report, wall: float | None
) -> None:
    for field, key, label in (
        ("observed_frequency_hz", "rate_wall_hz", "wall"),
        ("sim_frequency_hz", "rate_sim_hz", "sim"),
    ):
        got, want = rate.get(field), truth.get(key)
        if got is None or want is None:
            rep.add(
                "rates", f"{topic} {label} frequency", want, got, "INFO", "not comparable", wall
            )
            continue
        err = abs(got - want) / want
        verdict = "PASS" if err <= RATE_PASS else ("WARN" if err <= RATE_WARN else "FAIL")
        rep.add(
            "rates",
            f"{topic} {label} frequency (Hz)",
            fmt(want),
            fmt(got),
            verdict,
            f"relative error {err:.3f}; pass <= {RATE_PASS}, fail > {RATE_WARN}",
            wall,
        )


# ---------------------------------------------------------------------------- dds
@guard("dds")
def check_dds(gt: dict, res: Results, rep: Report) -> None:
    before = gt["graph"]["before_capture"]
    nodes = len(before.get("nodes") or [])
    rmw = (gt.get("ros") or {}).get("rmw_implementation", "")
    vendor = (gt.get("dds") or {}).get("vendor") or (
        "fast" if "fastrtps" in rmw else "cyclone" if "cyclonedds" in rmw else None
    )
    data = res.load("04_list_participants")
    err = tool_err(data)
    if err:
        rep.add(
            "dds", "list_participants", "-", err, "FAIL", "", res.times.get("04_list_participants")
        )
        return
    parts = listing(data, "participants")
    wall = res.times.get("04_list_participants")
    if (objs(data) or [{}])[0].get("mode_effective") == "mock":
        rep.add(
            "dds",
            "DDS backend",
            "live",
            "mock",
            "WARN",
            "the DDS tools answered from fixtures: nothing to compare",
            wall,
        )
        return
    obs = [p for p in parts if p.get("is_observer")]
    if vendor:
        mine = [p for p in parts if p["vendor"] == vendor and not p.get("is_observer")]
        rep.add(
            "dds",
            f"{vendor} participants (not the observer)",
            f"{nodes} ROS nodes, or +1 with the ros2 daemon",
            len(mine),
            "PASS" if len(mine) in (nodes, nodes + 1) else "WARN",
            "" if len(mine) in (nodes, nodes + 1) else "the daemon and CLI processes come and go",
            wall,
        )
        unnamed = sum(1 for p in mine if vendor == "fast" and p.get("name") in ("/", "", None))
        if vendor == "fast":
            rep.add(
                "dds",
                "Fast DDS participant names usable",
                "node names",
                f"{unnamed}/{len(mine)} named '/'",
                "WARN" if unnamed else "PASS",
                "D3: cannot map participants to nodes" if unnamed else "",
                wall,
            )
    rep.add(
        "dds",
        "observer participants (TopicForge itself)",
        "1, flagged",
        f"{len(obs)} ({[o['vendor'] for o in obs]})",
        "PASS" if len(obs) == 1 else "WARN",
        "",
        wall,
    )
    _check_endpoints(before, res, rep)
    mism = res.load("06_detect_qos_mismatches")
    if mism and not tool_err(mism):
        out = objs(mism)[0]
        total = out.get("reports_total", len(out.get("reports", [])))
        rep.add(
            "dds",
            "QoS mismatch reports",
            "0",
            total,
            "PASS" if not out.get("reports") and not total else "FAIL",
            f"{out.get('matched_total')} pairs matched",
            res.times.get("06_detect_qos_mismatches"),
        )


def _check_endpoints(before: dict, res: Results, rep: Report) -> None:
    data = res.load("05_list_endpoints")
    if not data or tool_err(data):
        return
    eps = listing(data, "endpoints")
    wall = res.times.get("05_list_endpoints")
    count: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    readers: dict[str, list[str]] = collections.defaultdict(list)
    for e in eps:
        ros_topic = e.get("ros_topic") or (
            "/" + pick(e, "dds_topic", "topic", default="")[3:]
            if str(pick(e, "dds_topic", "topic", default="")).startswith("rt/")
            else None
        )
        if not ros_topic:
            continue
        count[ros_topic][e["role"]] += 1
        if e["role"] == "reader":
            readers[ros_topic].append(str(e["qos"]["reliability"]).lower())
    bad, bad_qos = [], []
    for topic in before["topics"]:
        if topic.get("counts") == "ignore":
            continue
        n = topic["name"]
        w, r = count[n].get("writer", 0), count[n].get("reader", 0)
        if (w, r) != (topic["publisher_count"], topic["subscription_count"]):
            bad.append(f"{n} w{w}/r{r} vs {topic['publisher_count']}/{topic['subscription_count']}")
        want = sorted(e["reliability"].lower() for e in topic.get("endpoints", []) if not is_pub(e))
        if want and sorted(readers[n]) != want:
            bad_qos.append(f"{n}: {sorted(readers[n])} vs {want}")
    rep.add(
        "dds",
        "writers/readers per topic (list_endpoints)",
        "= graph counts",
        bad or "identical",
        "PASS" if not bad else "FAIL",
        f"{len(eps)} endpoints total",
        wall,
    )
    rep.add(
        "dds",
        "reader reliability per topic",
        "= subscription endpoints of the truth",
        bad_qos or "identical",
        "PASS" if not bad_qos else "FAIL",
        "",
        wall,
    )


# ---------------------------------------------------------------------------- bag
def _bag_topics_of(analysis: dict) -> dict[str, dict]:
    return {t["name"]: t for t in analysis["topics"]}


@guard("bag")
def check_bag(gt: dict, res: Results, rep: Report) -> None:
    truth = gt.get("bag")
    if not truth or "per_topic" not in truth:
        rep.add("bag", "bag truth", "-", "no bag in ground_truth.json", "INFO")
        return
    data = res.load("13_analyze_bag")
    err = tool_err(data)
    if err:
        rep.add("bag", "analyze_bag", "-", err, "FAIL", "", res.times.get("13_analyze_bag"))
        return
    bag = objs(data)[0]
    wall = res.times.get("13_analyze_bag")
    rep.add(
        "bag",
        "message_count",
        truth["message_count"],
        bag["message_count"],
        "PASS" if bag["message_count"] == truth["message_count"] else "FAIL",
        "",
        wall,
    )
    duration = pick(bag, "duration_s", "duration_seconds")
    want_s = truth["duration_ns"] / 1e9
    rep.add(
        "bag",
        "duration (s)",
        want_s,
        duration,
        "PASS" if abs(duration - want_s) < BAG_DURATION_TOL_S else "FAIL",
        f"tol {BAG_DURATION_TOL_S} s",
        wall,
    )
    rep.add(
        "bag",
        "storage",
        truth["storage_identifier"],
        bag["storage_format"],
        "PASS" if bag["storage_format"] == truth["storage_identifier"] else "FAIL",
        "",
        wall,
    )
    got = _bag_topics_of(bag)
    bad_c, bad_r, bad_ts = [], [], []
    for name, want in truth["per_topic"].items():
        item = got.get(name)
        if not item or item["message_count"] != want["count"]:
            bad_c.append(f"{name} {item and item['message_count']} vs {want['count']}")
            continue
        for field, key in (("first_timestamp_ns", "first_ns"), ("last_timestamp_ns", "last_ns")):
            if item.get(field) is not None and item[field] != want[key]:
                bad_ts.append(f"{name} {field} {item[field]} vs {want[key]}")
        basis = item.get("frequency_basis")
        ref = (
            want.get("count_over_bag_duration_hz")
            if basis == "bag_duration"
            else want.get("rate_recorded_wall_hz")
        )
        freq = item.get("frequency_hz")
        if ref is not None and (freq is None or abs(freq - ref) > max(1e-3 * ref, 1e-3)):
            bad_r.append(f"{name} {freq} vs {ref} ({basis})")
    rep.add(
        "bag",
        "per-topic message counts",
        f"{len(truth['per_topic'])} topics",
        bad_c or "identical",
        "PASS" if not bad_c else "FAIL",
        "",
        wall,
    )
    rep.add(
        "bag",
        "per-topic first/last timestamps",
        "exact when reported",
        bad_ts or "identical",
        "PASS" if not bad_ts else "FAIL",
        "",
        wall,
    )
    rep.add(
        "bag",
        "per-topic rates (frequency_hz)",
        "0.1 % of the basis' rate",
        bad_r or "identical",
        "PASS" if not bad_r else "FAIL",
        "",
        wall,
    )
    extra = sorted(set(got) - set(truth["per_topic"]))
    kinds = {n: got[n].get("kind") for n in extra if got[n].get("kind")}
    rep.add(
        "bag",
        "topics only in TopicForge",
        "-",
        extra or "none",
        "INFO" if extra else "PASS",
        ("D5: rosbag2 housekeeping" + (f"; kind {kinds}" if kinds else "")) if extra else "",
    )
    _check_bag_peeks(gt, truth, res, rep)


def _check_bag_peeks(gt: dict, truth: dict, res: Results, rep: Report) -> None:
    first = {n: v["first_ns"] for n, v in truth["per_topic"].items()}
    expect = gt.get("stamps") or DEFAULT_STAMPS
    published = [num(v) for v in gt["lidar"]["published_ranges"]]
    for name in res.names("14_", "14b_", "15_", "15b_", "16_"):
        data = res.load(name)
        topic = ((data or {}).get("_call") or {}).get("args", {}).get("topic", "?")
        wall = res.times.get(name)
        err = tool_err(data)
        if err:
            rosbags = "rosbags" in err
            rep.add(
                "bag",
                f"peek_bag_samples {topic} ({name})",
                "samples",
                err[:160],
                "WARN" if rosbags else "FAIL",
                "D6 (0.6.x): needs the rosbags extra" if rosbags else "",
                wall,
            )
            continue
        samples = objs(data)[0]["samples"]
        if topic == "/scan":
            bad = sum(
                1
                for s in samples
                if len(s["payload"]["ranges"]) != len(published)
                or any(
                    not same_range(num(a), b)
                    for a, b in zip(s["payload"]["ranges"], published, strict=False)
                )
            )
            rep.add(
                "bag",
                f"peek /scan x{len(samples)} ranges vs published ({name})",
                f"{truth.get('scans_equal_to_published_scan')}/{truth.get('scans_total')} equal",
                f"{bad} of {len(samples)} differ",
                "PASS" if bad == 0 and samples else "FAIL",
                "",
                wall,
            )
            ok = all(
                s.get("stamp_source") == "header" and s.get("recorded_ns") for s in samples
            ) and samples[0].get("recorded_ns") == first.get("/scan")
            rep.add(
                "bag",
                f"peek /scan stamp_source/recorded_ns ({name})",
                f"header; first recorded_ns {first.get('/scan')}",
                f"{samples[0].get('stamp_source')}; {samples[0].get('recorded_ns')}",
                "PASS" if ok else "FAIL",
                "",
                wall,
            )
        elif topic == "/rosout":
            rep.add(
                "bag",
                f"peek /rosout x{len(samples)} ({name})",
                "3 messages",
                f"{len(samples)} messages, source {samples[0].get('stamp_source')}",
                "PASS" if len(samples) == 3 else "FAIL",
                "",
                wall,
            )
        else:
            want = expect.get(topic)
            sources = {s.get("stamp_source") for s in samples}
            small = all(0 < s["timestamp_ns"] / 1e9 < 1e7 for s in samples)
            ok = (sources == {want} and small) if want == "payload" else bool(samples)
            zero = all(s.get("timestamp_ns") == 0 for s in samples)
            verdict = "PASS" if ok else ("WARN" if zero else "FAIL")
            rep.add(
                "bag",
                f"peek {topic} stamp_source ({name})",
                f"{want}, sim time",
                f"{sorted(map(str, sources))}",
                verdict,
                "D1 (0.6.x): stamp 0" if verdict == "WARN" else "",
                wall,
            )


def build_bag_truth(bag_dir: Path, expected_ranges: list[float] | None = None) -> dict[str, Any]:
    """Truth of a recorded bag read independently with rosbags (message timestamps, not metadata).

    `expected_ranges`, when given, is compared with every `/scan` message (tolerance RANGE_TOL).
    """
    from rosbags.highlevel import AnyReader

    per: dict[str, dict[str, Any]] = {}
    equal = total = 0
    with AnyReader([bag_dir]) as reader:
        for conn, stamp, raw in reader.messages():
            item = per.setdefault(
                conn.topic, {"type": conn.msgtype, "count": 0, "first_ns": stamp, "last_ns": stamp}
            )
            item["count"] += 1
            item["first_ns"] = min(item["first_ns"], stamp)
            item["last_ns"] = max(item["last_ns"], stamp)
            if expected_ranges is not None and conn.topic == "/scan":
                got = [num(v) for v in reader.deserialize(raw, conn.msgtype).ranges]
                total += 1
                equal += len(got) == len(expected_ranges) and all(
                    same_range(a, b) for a, b in zip(got, expected_ranges, strict=False)
                )
    first = min(v["first_ns"] for v in per.values())
    last = max(v["last_ns"] for v in per.values())
    duration_ns = last - first
    for item in per.values():
        span = (item["last_ns"] - item["first_ns"]) / 1e9
        item["rate_recorded_wall_hz"] = (item["count"] - 1) / span if span >= 1.0 else None
        item["count_over_bag_duration_hz"] = (
            item["count"] / (duration_ns / 1e9) if duration_ns else None
        )
    storage = re.search(
        r"storage_identifier:\s*(\S+)", (bag_dir / "metadata.yaml").read_text(encoding="utf-8")
    )
    return {
        "bag_dir": str(bag_dir),
        "storage_identifier": storage.group(1) if storage else "unknown",
        "duration_ns": duration_ns,
        "starting_time_ns": first,
        "message_count": sum(v["count"] for v in per.values()),
        "per_topic": per,
        "scans_total": total,
        "scans_equal_to_published_scan": equal,
    }


# ---------------------------------------------------------------------------- report
SECTIONS = (check_graph, check_scan, check_stamps, check_rates, check_bag, check_dds)


def compare(gt: dict[str, Any], results: Path) -> tuple[Report, Results]:
    """Run every section and return the report."""
    res, rep = Results(results), Report()
    for section in SECTIONS:
        section(gt, res, rep)
    return rep, res


def render(
    rep: Report,
    res: Results,
    gt: dict,
    truth_path: str,
    results: Path,
    version: str,
    strict_shape: bool,
) -> tuple[str, str]:
    """The markdown text and the verdict (`PASS` or `FAIL`)."""
    counts = rep.counts()
    failing = counts.get("FAIL", 0) + (counts.get("SHAPE", 0) if strict_shape else 0)
    verdict = "FAIL" if failing else "PASS"
    lines = [
        f"# TopicForge {version} vs ground truth",
        "",
        f"truth: `{truth_path}` (kit {gt.get('kit')}, generated {gt.get('generated_utc')}, "
        f"producer {(gt.get('omnisim') or gt.get('bench') or {}).get('version', 'n/a')})",
        f"results: `{results}`",
        "",
        "| section | metric | ground truth | TopicForge | verdict | wall s | note |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rep.rows:
        cells = [str(r["truth"]).replace("|", "/"), str(r["topicforge"]).replace("|", "/")]
        lines.append(
            f"| {r['section']} | {r['metric']} | {cells[0]} | {cells[1]} | {r['verdict']} | "
            f"{'' if r['wall_s'] is None else r['wall_s']} | {r['note']} |"
        )
    if rep.rate_cv:
        lines += ["", "Rate interval_cv (received_ns; STABLE_CV threshold 0.2):"]
        lines += [
            f"- {t}: cv {v['interval_cv']} n={v['message_count']} verdict {v['verdict']}"
            for t, v in sorted(rep.rate_cv.items())
        ]
    lines += [
        "",
        f"RESULT: {verdict}  (PASS {counts.get('PASS', 0)}, FAIL {counts.get('FAIL', 0)}, "
        f"WARN {counts.get('WARN', 0)}, SHAPE {counts.get('SHAPE', 0)}, INFO {counts.get('INFO', 0)})",
    ]
    return "\n".join(lines) + "\n", verdict


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--truth", required=True)
    ap.add_argument("--results", required=True)
    ap.add_argument("--out", default=None, help="COMPARISON.md path (default: in --results)")
    ap.add_argument("--label", default=None, help="TopicForge version for the title")
    ap.add_argument(
        "--bag-truth", default=None, help="bag directory: compute and add its truth (rosbags)"
    )
    ap.add_argument("--strict-shape", action="store_true", help="count SHAPE rows as failures (CI)")
    args = ap.parse_args(argv)
    gt = json.loads(Path(args.truth).read_text(encoding="utf-8"))
    results = Path(args.results)
    if args.bag_truth:
        gt["bag"] = build_bag_truth(
            Path(args.bag_truth), [num(v) for v in gt["lidar"]["published_ranges"]]
        )
    init = Results(results).load("_initialize") or {}
    version = args.label or init.get("topicforge_version") or "unknown version"
    rep, res = compare(gt, results)
    text, verdict = render(rep, res, gt, args.truth, results, version, args.strict_shape)
    out = Path(args.out) if args.out else results / "COMPARISON.md"
    out.write_text(text, encoding="utf-8")
    counts = rep.counts()
    out.with_name("comparison.json").write_text(
        json.dumps(
            {
                "result": verdict,
                "counts": dict(counts),
                "topicforge_version": version,
                "truth": args.truth,
                "rate_cv": rep.rate_cv,
                "rows": rep.rows,
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(text)
    for topic, v in sorted(rep.rate_cv.items()):
        print(f"RATE_CV {topic} {v['interval_cv']} n={v['message_count']} {v['verdict']}")
    return 1 if verdict == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
