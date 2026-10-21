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
(verdict and frequency, simulated and wall time), bag, dds, tf (the `inspect_tf` tree; only the
bench truth has one), clock (`clock_basis`, stamp age and regressions per
topic; only the bench truth has one), graph_diff (the four `graph_diff` calls of
`calls_graph_diff.json`: first call, steady graph, graph v2, bag baseline; only the bench truth
has one). Reads output contract 2 (0.7:
listing envelopes, publisher_qos, duration_s, dds_topic/ros_topic) and tolerates 0.6.x shapes.

Tolerances (the constants below; each row names the one it used):

    scan header floats      angle_min/max, range_min/max, time_increment, scan_time  1e-6;
                            angle_increment 1e-9 (the publisher rounds to float32 first)
    every range             1e-5 m against lidar.published_ranges; +inf, "inf" and null are equal
    wall / sector minima    0.005 m against the geometry; beam index exact unless the wall has
                            `beam_index_tolerance`
    stamps                  header/payload stamp in (0, 1e7) s (sim time, not epoch); "none" is 0
    rates                   relative error <= 15 % PASS, <= 50 % WARN, above FAIL; FAIL only
                            with message_count >= 10 and kept messages spanning >= 0.9 s, WARN at
                            most below; the verdict is WARN-only; startup_burst_count > 0 is an
                            INFO row
    wall frequency          a FAIL becomes a WARN (the reason is in the note) when the kit's
                            omnisim.real_time_factor_during_window is < 0.5 (LOW_RTF), or when
                            TopicForge's observed/sim frequency ratio differs from the kit's
                            rate_wall_hz / rate_sim_hz by more than 25 % (RATIO_TOL). The
                            simulated-time rows keep gating
    STABLE_CV               a fixed-rate topic (rates.fixed_rate_topics) with interval_cv >= 0.2
                            over >= 0.9 s is a WARN when the cv of its own stamps is < 0.2, an
                            INFO otherwise (CONTRACT.md section 4); every cv is listed at the end
    bag                     counts, duration (1e-6 s), first/last ns exact; rate 0.1 % (>= 1e-3 Hz)
    pose (stationary robot) 1e-4 m
"""

from __future__ import annotations

import argparse
import collections
import itertools
import json
import math
import re
import statistics
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
RATE_GATE_SPAN_S = 0.9
STABLE_CV = 0.2
BAG_DURATION_TOL_S = 1e-6
INFRA_TOPICS = ("/parameter_events", "/rosout")
LOW_RTF = 0.5
RATIO_TOL = 0.25
CLI_NODE_PREFIXES = ("/_ros2cli_", "_ros2cli_")
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
            want_p = sorted({node_full_name(e) for e in pubs})
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


def _check_node_groups(
    before: dict, data: Any, listed: list[dict], truth_nodes: list[str], rep: Report, wall: Any
) -> None:
    """`lifecycle` and `by_namespace` of list_nodes, when the truth states them (bench only)."""
    summary = (objs(data) or [{}])[0]
    if "lifecycle_nodes" in before:
        want = sorted(before["lifecycle_nodes"])
        got = sorted(n["full_name"] for n in listed if n.get("lifecycle"))
        note = summary.get("lifecycle_note")
        ok = got == want and summary.get("lifecycle_count") == len(want) and not note
        rep.add(
            "graph",
            "lifecycle nodes (list_nodes)",
            want,
            got if not note else f"{got} ({note})",
            "PASS" if ok else "FAIL",
            "a node serving <name>/get_state of type lifecycle_msgs/srv/GetState",
            wall,
        )
    if "nodes_by_namespace" in before:
        want_ns = before["nodes_by_namespace"]
        got_ns = summary.get("by_namespace")
        rep.add(
            "graph",
            "nodes per namespace (list_nodes)",
            want_ns,
            got_ns,
            "PASS" if got_ns == want_ns and list(got_ns) == sorted(got_ns) else "FAIL",
            f"{len(truth_nodes)} nodes, sorted by namespace, / is the root",
            wall,
        )


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
    _check_node_groups(before, data, listed, truth_nodes, rep, wall)
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
def _kept_span_s(rate: dict) -> float | None:
    """Time covered by the messages kept after the start-up burst, from the mean interval."""
    kept = (rate.get("message_count") or 0) - (rate.get("startup_burst_count") or 0)
    mean = rate.get("mean_interval_s")
    if mean is None or kept < 2:
        return None
    return round((kept - 1) * mean, 3)


def _stamp_cv(samples: list[dict]) -> float | None:
    """Coefficient of variation of the publisher stamps' steps; None without usable stamps."""
    if any(s.get("stamp_source") not in ("header", "payload") for s in samples):
        return None
    stamps = sorted(s["timestamp_ns"] for s in samples if s.get("timestamp_ns") is not None)
    steps = [b - a for a, b in itertools.pairwise(stamps)]
    if len(steps) < 2 or not any(steps):
        return None
    return round(statistics.pstdev(steps) / statistics.fmean(steps), 3)


def _leading_intervals_ms(samples: list[dict], n: int = 6) -> list[float]:
    """First `n` intervals between received_ns, in milliseconds."""
    times = [s["received_ns"] for s in samples if s.get("received_ns") is not None]
    return [round((b - a) / 1e6, 1) for a, b in itertools.pairwise(times)][:n]


def _rate_blocks(res: Results) -> list[tuple[str, dict, float | None]]:
    """(topic, sample result, wall time) for every call that carries a rate block."""
    blocks: list[tuple[str, dict, float | None]] = []
    for name in ("08c_sample_scan_rate", "08d_sample_clock_rate"):
        data = res.load(name)
        if data and not tool_err(data):
            result = objs(data)[0]
            args = (data.get("_call") or {}).get("args") or {}
            blocks.append(
                (result.get("topic") or args.get("topic") or name, result, res.times.get(name))
            )
    for name, data, topic in _sample_files(res):
        if not tool_err(data):
            blocks.append((topic, objs(data)[0], res.times.get(name)))
    return blocks


@guard("rates")
def check_rates(gt: dict, res: Results, rep: Report) -> None:
    truth = gt.get("rates") or {}
    per_topic = truth.get("per_topic") or {}
    fixed = set(truth.get("fixed_rate_topics") or [])
    verdicts_ok = tuple(truth.get("expected_verdicts") or ("stable", "jittery"))
    rtf = (gt.get("omnisim") or {}).get("real_time_factor_during_window")
    blocks = _rate_blocks(res)
    if not any(b[1].get("rate") for b in blocks):
        rep.add(
            "rates", "rate block", "present (0.7)", "absent", "WARN", "0.6.x output has no rate"
        )
        return
    for topic, result, wall in blocks:
        rate = result.get("rate")
        if not rate:
            continue
        samples = result.get("samples") or []
        count, cv = rate.get("message_count"), rate.get("interval_cv")
        verdict, burst = rate.get("verdict"), rate.get("startup_burst_count") or 0
        span = _kept_span_s(rate)
        gated = (count or 0) >= RATE_MIN_MESSAGES and span is not None and span >= RATE_GATE_SPAN_S
        stamp_cv = _stamp_cv(samples)
        if cv is not None:
            known = rep.rate_cv.get(topic)
            if known is None or (count or 0) >= (known["message_count"] or 0):
                rep.rate_cv[topic] = {
                    "interval_cv": cv,
                    "message_count": count,
                    "verdict": verdict,
                    "basis": rate.get("basis"),
                    "span_s": span,
                    "stamp_cv": stamp_cv,
                    "startup_burst_count": burst,
                }
        if burst > 0:
            rep.add(
                "rates",
                f"{topic} start-up burst (n={count})",
                "none",
                f"{burst} leading message(s) set aside",
                "INFO",
                f"first received intervals (ms): {_leading_intervals_ms(samples)}",
                wall,
            )
        if topic in fixed and cv is not None and verdict != "insufficient" and cv >= STABLE_CV:
            warn = gated and stamp_cv is not None and stamp_cv < STABLE_CV
            if warn:
                note = (
                    "CONTRACT.md section 4: the measuring path would be that noisy, "
                    "review the threshold"
                )
            elif not gated:
                note = f"span {span} s or n={count} too short to gate (>= {RATE_GATE_SPAN_S} s)"
            elif stamp_cv is None:
                note = "no usable publisher stamps to cross-check"
            else:
                note = f"the publisher's own stamps are irregular too (cv {stamp_cv})"
            rep.add(
                "rates",
                f"{topic} STABLE_CV (n={count})",
                f"cv < {STABLE_CV} on a fixed-rate publisher",
                f"cv {cv} (n={count}, span {span} s, stamp cv {stamp_cv})",
                "WARN" if warn else "INFO",
                note,
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
        _compare_frequencies(
            per_topic.get(topic) or {}, topic, rate, rep, wall, gated, span, rtf=rtf
        )


def _wall_relaxation(truth: dict, rate: dict, rtf: float | None) -> str | None:
    """Why a wall-clock frequency cannot gate on this machine, else None.

    Wall rates depend on how busy the host was while the kit measured: a low real-time factor
    (the simulator ran slower than real time) or a wall/sim ratio that differs between the kit
    and TopicForge (the two measured different stretches of an uneven run) both void the gate.
    """
    if isinstance(rtf, (int, float)) and rtf < LOW_RTF:
        return f"low RTF {rtf:.3f} < {LOW_RTF}"
    obs, sim = rate.get("observed_frequency_hz"), rate.get("sim_frequency_hz")
    k_wall, k_sim = truth.get("rate_wall_hz"), truth.get("rate_sim_hz")
    if all(isinstance(v, (int, float)) and v > 0 for v in (obs, sim, k_wall, k_sim)):
        ours, kit = obs / sim, k_wall / k_sim
        if abs(ours - kit) / kit > RATIO_TOL:
            return (
                f"wall/sim ratio mismatch: TopicForge {ours:.3f} vs kit {kit:.3f} "
                f"(differs by more than {RATIO_TOL:.0%})"
            )
    return None


def _compare_frequencies(
    truth: dict,
    topic: str,
    rate: dict,
    rep: Report,
    wall: float | None,
    gated: bool,
    span: float | None,
    rtf: float | None = None,
) -> None:
    n = rate.get("message_count")
    relaxed = _wall_relaxation(truth, rate, rtf)
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
        if err <= RATE_PASS:
            verdict = "PASS"
        elif err <= RATE_WARN or not gated:
            verdict = "WARN"
        else:
            verdict = "FAIL"
        note = f"relative error {err:.3f}; pass <= {RATE_PASS}, fail > {RATE_WARN}"
        if not gated:
            note += f"; span {span} s under {RATE_GATE_SPAN_S}: WARN at most"
        if label == "wall" and relaxed and verdict == "FAIL":
            verdict = "WARN"
            note += f"; WARN at most, {relaxed}: wall-clock rows do not gate here"
        rep.add(
            "rates",
            f"{topic} {label} frequency (Hz, n={n})",
            fmt(want),
            fmt(got),
            verdict,
            note,
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
        # Only participants still on the bus: the `ros2` CLI processes that
        # get_node_info starts come and go and stay listed with status "left".
        mine = [
            p
            for p in parts
            if p["vendor"] == vendor
            and not p.get("is_observer")
            and p.get("status", "active") == "active"
        ]
        # A `ros2 node info` process TopicForge spawned stays a participant for a few
        # seconds: ignore it (flag `is_topicforge_cli`, or a `_ros2cli_<pid>` node name).
        cli = [p for p in mine if _is_cli_participant(p)]
        mine = [p for p in mine if p not in cli]
        if cli:
            rep.add(
                "dds",
                "TopicForge ros2 CLI participants ignored",
                "0 or more",
                f"{len(cli)} ignored",
                "INFO",
                "spawned by get_node_info / topic calls: flag is_topicforge_cli or a "
                "_ros2cli_ node name",
                wall,
            )
        rep.add(
            "dds",
            f"{vendor} active participants (not the observer)",
            f"{nodes} ROS nodes, or +1 with the ros2 daemon",
            len(mine),
            "PASS" if len(mine) in (nodes, nodes + 1) else "WARN",
            "" if len(mine) in (nodes, nodes + 1) else "the daemon and CLI processes come and go",
            wall,
        )
        # A Fast DDS participant is named "/"; since 0.7.2 node_names (read from
        # ros_discovery_info) maps it to its nodes, so only a participant with
        # neither is unusable.
        unnamed = sum(
            1
            for p in mine
            if vendor == "fast" and p.get("name") in ("/", "", None) and not p.get("node_names")
        )
        if vendor == "fast":
            rep.add(
                "dds",
                "Fast DDS participant names usable",
                "node names",
                f"{unnamed}/{len(mine)} without a name or node_names",
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


def _is_cli_participant(p: dict[str, Any]) -> bool:
    """True for a participant that is a `ros2` CLI process started by TopicForge itself."""
    if p.get("is_topicforge_cli", False):
        return True
    names = [p.get("name"), *(p.get("node_names") or [])]
    return any(isinstance(n, str) and n.startswith(CLI_NODE_PREFIXES) for n in names)


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
        if n in INFRA_TOPICS:
            # Presence and type only (see check_graph): the hidden CLI and daemon nodes
            # add writers and readers that the truth cannot count.
            continue
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
            # The recorder may store few or no /rosout lines (late discovery on a loaded
            # runner): expect what the bag holds, read independently.
            held = truth["per_topic"].get("/rosout", {}).get("count")
            want_n = 3 if held is None else min(3, held)
            source = samples[0].get("stamp_source") if samples else None
            rep.add(
                "bag",
                f"peek /rosout x{len(samples)} ({name})",
                f"{want_n} messages",
                f"{len(samples)} messages, source {source}",
                "PASS" if len(samples) == want_n else "FAIL",
                "" if held is None else f"the bag holds {held} /rosout messages",
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


# Topics whose every recorded timestamp is kept (`stamps_ns`), so a time-range answer can be
# counted exactly: the recorder starts the other topics late on a loaded runner.
STAMPED_TOPICS = ("/clock",)


def build_bag_truth(bag_dir: Path, expected_ranges: list[float] | None = None) -> dict[str, Any]:
    """Truth of a recorded bag read independently with rosbags (message timestamps, not metadata).

    `expected_ranges`, when given, is compared with every `/scan` message (tolerance RANGE_TOL).
    The topics in `STAMPED_TOPICS` also carry the sorted list of their timestamps.
    """
    from rosbags.highlevel import AnyReader
    from rosbags.typesys import Stores, get_typestore

    per: dict[str, dict[str, Any]] = {}
    equal = total = 0
    # Humble's rosbag2 stores no message definitions; the Humble typestore covers
    # every type the bench records, and bags that embed definitions ignore it.
    typestore = get_typestore(Stores.ROS2_HUMBLE)
    with AnyReader([bag_dir], default_typestore=typestore) as reader:
        for conn, stamp, raw in reader.messages():
            item = per.setdefault(
                conn.topic, {"type": conn.msgtype, "count": 0, "first_ns": stamp, "last_ns": stamp}
            )
            item["count"] += 1
            item["first_ns"] = min(item["first_ns"], stamp)
            item["last_ns"] = max(item["last_ns"], stamp)
            if conn.topic in STAMPED_TOPICS:
                item.setdefault("stamps_ns", []).append(stamp)
            if conn.topic == "/rosout":
                level = reader.deserialize(raw, conn.msgtype).level
                item["error_count"] = item.get("error_count", 0) + (level == 40)
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
        if "stamps_ns" in item:
            item["stamps_ns"].sort()
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


# ---------------------------------------------------------------------------- tf
# Self-contained: the `tf` section of the truth (only the bench writes one) against the
# `inspect_tf` answer saved as `17_inspect_tf`. No other section reads either.
TF_CALL_NAME = "17_inspect_tf"
TF_RTF_TOL = 0.25


def _tf_expected_parents(truth: dict) -> dict[str, set[str]]:
    parents: dict[str, set[str]] = {}
    for parent, child in [*truth["static_edges"], *truth["dynamic_edges"]]:
        parents.setdefault(child, set()).add(parent)
    return parents


def _tf_frame_rows(truth: dict, frames: dict[str, dict], rep: Report, wall: float | None) -> None:
    got_names, want_names = sorted(frames), sorted(truth["frames"])
    rep.add(
        "tf",
        "frame set (inspect_tf)",
        want_names,
        got_names,
        "PASS" if got_names == want_names else "FAIL",
        "",
        wall,
    )
    static_children = {child for _, child in truth["static_edges"]}
    for child, parents in sorted(_tf_expected_parents(truth).items()):
        frame = frames.get(child)
        if frame is None:
            continue
        ok = frame.get("parent") in parents
        if len(parents) == 1:
            kind = "static" if child in static_children else "dynamic"
            ok = ok and frame.get("kind") == kind
            want = f"{next(iter(parents))} ({kind})"
        else:
            want = f"one of {sorted(parents)}"
        rep.add(
            "tf",
            f"{child} parent and kind (inspect_tf)",
            want,
            f"{frame.get('parent')} ({frame.get('kind')})",
            "PASS" if ok else "FAIL",
            "",
            wall,
        )


def _tf_finding_rows(truth: dict, report: dict, rep: Report, wall: float | None) -> None:
    findings = report.get("findings") or []
    kinds = sorted({f.get("kind") for f in findings})
    want_kinds = sorted(truth["finding_kinds"])
    rep.add(
        "tf",
        "finding kinds (inspect_tf)",
        want_kinds,
        kinds,
        "PASS" if kinds == want_kinds else "FAIL",
        "one finding per fault and nothing else",
        wall,
    )
    stale = sorted(f["name"] for f in report.get("frames") or [] if f.get("stale"))
    rep.add(
        "tf",
        "stale frames (inspect_tf)",
        sorted(truth["stale"]),
        stale,
        "PASS" if stale == sorted(truth["stale"]) else "FAIL",
        "the edge that stopped publishing during the window",
        wall,
    )
    for child, parents in truth["two_parents"].items():
        want = [child, *parents]
        got = [f.get("frames") for f in findings if f.get("kind") == "two_parents"]
        rep.add(
            "tf",
            f"two_parents {child} (inspect_tf)",
            want,
            got,
            "PASS" if [want] == got else "FAIL",
            "",
            wall,
        )


def _tf_rate_rows(truth: dict, report: dict, rep: Report, wall: float | None) -> None:
    steady = next((f for f in report.get("frames") or [] if f.get("name") == "base_link"), {})
    for field, label in (("observed_frequency_hz", "wall"), ("sim_frequency_hz", "sim")):
        got, want = steady.get(field), truth["steady_hz"]
        if got is None:
            rep.add("tf", f"base_link {label} frequency", want, got, "INFO", "not comparable", wall)
            continue
        err = abs(got - want) / want
        rep.add(
            "tf",
            f"base_link {label} frequency (Hz)",
            fmt(want),
            fmt(got),
            "PASS" if err <= RATE_PASS else "WARN",
            f"relative error {err:.3f}; `echo` can drop messages: WARN at most",
            wall,
        )
    rtf = report.get("real_time_factor")
    ok = rtf is not None and abs(rtf - truth["real_time_factor"]) <= TF_RTF_TOL
    rep.add(
        "tf",
        "real_time_factor (inspect_tf)",
        f"{truth['real_time_factor']} +- {TF_RTF_TOL}",
        rtf,
        "PASS" if ok else "WARN",
        "machine dependent: never FAIL",
        wall,
    )


@guard("tf")
def check_tf(gt: dict, res: Results, rep: Report) -> None:
    truth = gt.get("tf")
    if not truth:
        return
    data = res.load(TF_CALL_NAME)
    wall = res.times.get(TF_CALL_NAME)
    err = tool_err(data)
    if err == "missing":
        rep.add("tf", "inspect_tf", "present (0.7.2)", "absent", "WARN", "older server", wall)
        return
    if err:
        rep.add("tf", "inspect_tf", "a report", err, "FAIL", "", wall)
        return
    report = objs(data)[0]
    frames = {f["name"]: f for f in report["frames"]}
    _tf_frame_rows(truth, frames, rep, wall)
    roots = sorted(report["roots"])
    rep.add(
        "tf",
        "roots (inspect_tf)",
        sorted(truth["roots"]),
        roots,
        "PASS" if roots == sorted(truth["roots"]) else "FAIL",
        "",
        wall,
    )
    _tf_finding_rows(truth, report, rep, wall)
    rep.add(
        "tf",
        "path source to target (inspect_tf)",
        truth["path"],
        report.get("path"),
        "PASS" if report.get("path") == truth["path"] else "FAIL",
        "",
        wall,
    )
    _tf_rate_rows(truth, report, rep, wall)


# ---------------------------------------------------------------------------- clock
@guard("clock")
def check_clock(gt: dict, res: Results, rep: Report) -> None:
    """`TopicRate.clock_basis` and the stamp fields per topic (0.7.3); bench truth only."""
    topics = (gt.get("clock") or {}).get("topics") or {}
    best: dict[str, tuple[dict, float | None]] = {}
    for topic, result, wall in _rate_blocks(res):
        rate = result.get("rate")
        if rate and topic in topics:
            known = best.get(topic)
            if known is None or (rate.get("message_count") or 0) >= (
                known[0].get("message_count") or 0
            ):
                best[topic] = (rate, wall)
    for topic, want in sorted(topics.items()):
        if topic not in best:
            rep.add("clock", f"{topic} rate block", "sampled", "absent", "WARN", "not sampled")
            continue
        rate, wall = best[topic]
        if "clock_basis" not in rate:
            rep.add("clock", f"{topic} clock_basis", want["basis"], "absent", "WARN", "pre-0.7.3")
            continue
        _clock_rows(topic, want, rate, rep, wall)


def _clock_rows(topic: str, want: dict, rate: dict, rep: Report, wall: float | None) -> None:
    basis = rate["clock_basis"]
    rep.add(
        "clock",
        f"{topic} clock_basis",
        want["basis"],
        basis,
        "PASS" if basis == want["basis"] else "FAIL",
        "",
        wall,
    )
    median, p95 = rate.get("stamp_age_median_s"), rate.get("stamp_age_p95_s")
    if want["basis"] != "wall":
        ok = median is None and p95 is None
        rep.add(
            "clock",
            f"{topic} stamp age",
            "null (not wall)",
            [median, p95],
            "PASS" if ok else "FAIL",
            "",
            wall,
        )
    else:
        low, high = want["age_median_s_range"]
        ok = isinstance(median, (int, float)) and low <= median <= high
        rep.add(
            "clock",
            f"{topic} stamp_age_median_s",
            f"{low}..{high}",
            median,
            "PASS" if ok else "FAIL",
            f"published {want.get('published_age_s')} s behind; the age also holds delivery",
            wall,
        )
        ok = isinstance(p95, (int, float)) and p95 < want["age_p95_s_max"]
        rep.add(
            "clock",
            f"{topic} stamp_age_p95_s",
            f"< {want['age_p95_s_max']}",
            p95,
            "PASS" if ok else "FAIL",
            "",
            wall,
        )
    if "min_regressions" in want:
        count, got = rate.get("message_count") or 0, rate.get("stamp_regression_count")
        ok = isinstance(got, int) and got >= want["min_regressions"]
        rep.add(
            "clock",
            f"{topic} stamp_regression_count (n={count})",
            f">= {want['min_regressions']}",
            got,
            "PASS" if ok else ("FAIL" if count >= want.get("min_messages", 12) else "WARN"),
            "a stamp that goes back every 4th message",
            wall,
        )


# ---------------------------------------------------------------------------- logs
# `/rosout` filter and bag ranges (0.7.3), bench truth only. The live calls use a short
# `timeout_s` so the 10 s transient_local buffer must supply most lines: every row reports
# the exact counts (and the level histogram) so a CI run shows whether the depth-2000
# reader really gets the buffer on this RMW.
LOG_LEVEL_NAMES = {10: "debug", 20: "info", 30: "warn", 40: "error", 50: "fatal"}
_STARTING_TIME = re.compile(r"starting_time:\s*nanoseconds_since_epoch:\s*(\d+)")


def _log_samples(res: Results, name: str) -> tuple[list[dict], dict, float | None] | str:
    """`(samples, result, wall)` of a saved `sample_messages` / `peek_bag_samples`, else the error."""
    data = res.load(name)
    if data is None:
        return "not run"
    err = tool_err(data)
    if err:
        return err[:200]
    result = objs(data)[0]
    return result["samples"], result, res.times.get(name)


def _level_counts(samples: list[dict]) -> dict[str, int]:
    counts = collections.Counter(s["payload"].get("level") for s in samples)
    return {LOG_LEVEL_NAMES.get(k, str(k)): v for k, v in sorted(counts.items(), key=str)}


def _log_rows(
    rep: Report,
    metric: str,
    want: str,
    samples: list[dict],
    result: dict,
    ok: bool,
    wall: float | None,
    warn_if_few: bool = False,
) -> None:
    got = f"{len(samples)} msgs {_level_counts(samples)}"
    note = (result.get("note") or "")[:220]
    rep.add(
        "logs", metric, want, got, "PASS" if ok else ("WARN" if warn_if_few else "FAIL"), note, wall
    )


@guard("logs")
def check_logs(gt: dict, res: Results, rep: Report) -> None:
    truth = gt.get("logs")
    if not truth:
        return
    _check_live_logs(truth, res, rep)
    _check_bag_logs(gt, truth, res, rep)


def _check_live_logs(truth: dict, res: Results, rep: Report) -> None:
    logger = truth["logger"]
    for name, metric in (
        ("21_log_error_node", "live /rosout error + node /bench_robot"),
        ("22_log_warn", "live /rosout min_log_level warn"),
        ("23_log_blocked_node", "live /rosout node /bench_blocked"),
        ("24_log_lifecycle_node", "live /rosout node /robot1/bench_lifecycle"),
    ):
        loaded = _log_samples(res, name)
        if isinstance(loaded, str):
            rep.add("logs", metric, "samples", loaded, "WARN" if loaded == "not run" else "FAIL")
            continue
        samples, result, wall = loaded
        names = {s["payload"].get("name") for s in samples}
        levels = [s["payload"].get("level") for s in samples]
        stamps = [s["timestamp_ns"] for s in samples]
        ordered = stamps == sorted(stamps)
        if name == "21_log_error_node":
            want = f">= {truth['min_error_msgs']} msgs, all level 40, name {logger}"
            ok = (
                len(samples) >= truth["min_error_msgs"]
                and set(levels) == {40}
                and names == {logger}
                and ordered
            )
            _log_rows(rep, metric, want, samples, result, ok, wall)
        elif name == "22_log_warn":
            ok = bool(samples) and min(levels) >= 30 and ordered
            _log_rows(rep, metric, "levels >= 30 only, stamp order", samples, result, ok, wall)
        elif name == "23_log_blocked_node":
            ok = not samples and bool(result.get("note"))
            _log_rows(rep, metric, "0 msgs and a note", samples, result, ok, wall)
        else:
            want = f">= 1 msg, all name {truth['lifecycle_logger']}"
            ok = bool(samples) and names == {truth["lifecycle_logger"]}
            _log_rows(rep, metric, want, samples, result, ok, wall)


def _starting_time_ns(gt: dict) -> tuple[int | None, str]:
    """`starting_time` of the bench bag: from its metadata.yaml, else the first message."""
    bag = gt.get("bag") or {}
    meta = Path(str(bag.get("bag_dir", ""))) / "metadata.yaml"
    if meta.is_file():
        found = _STARTING_TIME.search(meta.read_text(encoding="utf-8"))
        if found:
            return int(found.group(1)), "metadata.yaml"
    first = bag.get("starting_time_ns")
    return (int(first), "first message") if first is not None else (None, "none")


def _check_bag_logs(gt: dict, truth: dict, res: Results, rep: Report) -> None:
    start_ns, source = _starting_time_ns(gt)
    analysis = res.load("13_analyze_bag")
    if analysis is not None and not tool_err(analysis) and start_ns is not None:
        got = objs(analysis)[0].get("start_ns")
        rep.add(
            "logs",
            "analyze_bag.start_ns",
            f"{start_ns} ({source})",
            got,
            "PASS" if got == start_ns else "FAIL",
            "",
            res.times.get("13_analyze_bag"),
        )
    loaded = _log_samples(res, "25_peek_bag_rosout_error")
    if isinstance(loaded, str):
        rep.add("logs", "bag /rosout min_log_level error", "samples", loaded, "WARN")
    else:
        samples, result, wall = loaded
        # Expect what the bag holds (read independently): the recorder may miss /rosout.
        held = (gt.get("bag") or {}).get("per_topic", {}).get("/rosout", {}).get("error_count")
        if held is None:
            want = f">= {truth['bag_min_errors']} msgs, all level 40"
            ok = len(samples) >= truth["bag_min_errors"]
        else:
            want = f"{min(10, held)} msgs (the bag holds {held} ERROR lines), all level 40"
            ok = len(samples) == min(10, held)
        ok = ok and {s["payload"].get("level") for s in samples} <= {40}
        _log_rows(
            rep,
            "bag /rosout min_log_level error",
            want,
            samples,
            result,
            ok,
            wall,
        )
    rng = truth["bag_range"]
    loaded = _log_samples(res, "26_peek_bag_clock_range")
    if isinstance(loaded, str):
        rep.add("logs", "bag /clock start_s 2 end_s 4", "samples", loaded, "WARN")
        return
    samples, result, wall = loaded
    if start_ns is None:
        rep.add("logs", "bag /clock range", "bag start", "unknown", "WARN", "", wall)
        return
    low, high = start_ns + int(rng["start_s"] * 1e9), start_ns + int(rng["end_s"] * 1e9)
    recorded = [s.get("recorded_ns") or 0 for s in samples]
    inside = all(low <= r < high for r in recorded)
    # The recorder starts /clock late on a loaded runner, so the window may hold fewer than
    # `count` messages: the truth is the number of recorded /clock stamps inside the window.
    stamps = ((gt.get("bag") or {}).get("per_topic", {}).get(rng["topic"]) or {}).get("stamps_ns")
    in_window = sum(low <= t < high for t in stamps) if stamps is not None else None
    want = rng["count"] if in_window is None else min(rng["count"], in_window)
    basis = "" if in_window is None else f" (min of {rng['count']} and {in_window} in the window)"
    note = (result.get("note") or "")[:200]
    ok = len(samples) == want and inside and (want > 0 or bool(note))
    rep.add(
        "logs",
        f"bag /clock start_s {rng['start_s']} end_s {rng['end_s']}",
        f"{want} samples{basis} with recorded_ns in [{low}, {high})"
        + (" and a note" if want == 0 else ""),
        f"{len(samples)} samples; first {min(recorded, default=0) - start_ns} ns, "
        f"last {max(recorded, default=0) - start_ns} ns after start",
        "PASS" if ok else "FAIL",
        note,
        wall,
    )


# ---------------------------------------------------------------------------- report
LISTING_MAX_BYTES = 100_000


def _drop_none(node: Any) -> Any:
    """A copy of a JSON value without null-valued keys (the wire form omits them)."""
    if isinstance(node, dict):
        return {k: _drop_none(v) for k, v in node.items() if v is not None}
    if isinstance(node, list):
        return [_drop_none(v) for v in node]
    return node


@guard("listing")
def check_listing(gt: dict, res: Results, rep: Report) -> None:
    """The tools/list size that drive.py saved in `_tools.json`; some clients truncate it."""
    file = res.path / "_tools.json"
    if not file.is_file():
        rep.add(
            "listing",
            "tools/list bytes",
            f"< {LISTING_MAX_BYTES}",
            "absent",
            "WARN",
            "no _tools.json",
        )
        return
    tools = json.loads(file.read_text(encoding="utf-8"))
    size = len(json.dumps(_drop_none(tools), separators=(",", ":")))
    rep.add(
        "listing",
        "tools/list bytes",
        f"< {LISTING_MAX_BYTES}",
        size,
        "PASS" if size < LISTING_MAX_BYTES else "FAIL",
        f"{len(tools)} tools, compact JSON of the saved listing",
    )


# ---------------------------------------------------------------------------- graph_diff
# Self-contained: the `graph_diff` section of the truth (only the bench writes one) against the
# four calls of `calls_graph_diff.json` (0.8.0). The steady call is the healthy line: a graph
# diffed against itself must report nothing, and it has to pass before the v2 call means anything.
def _graph_diff_report(res: Results, name: str) -> tuple[dict | None, float | None, str | None]:
    """`(report, wall_s, error)` of one saved `graph_diff` call."""
    data = res.load(name)
    err = tool_err(data)
    if err:
        return None, res.times.get(name), err
    return objs(data)[0], res.times.get(name), None


def _change_keys(report: dict) -> list[tuple[str, str, str]]:
    return sorted((c["kind"], c["change"], c["name"]) for c in report.get("changes") or [])


def _gd_row(
    rep: Report, metric: str, truth: Any, got: Any, ok: bool, wall: float | None, note: str = ""
) -> None:
    rep.add("graph_diff", metric, truth, got, "PASS" if ok else "FAIL", note, wall)


def _gd_unavailable(rep: Report, metric: str, err: str, wall: float | None) -> None:
    if err == "missing":
        rep.add("graph_diff", metric, "a report", "absent", "WARN", "older server", wall)
    else:
        rep.add("graph_diff", metric, "a report", err, "FAIL", "", wall)


def _check_gd_first(truth: dict, res: Results, rep: Report) -> None:
    report, wall, err = _graph_diff_report(res, truth["call"])
    if err:
        return _gd_unavailable(rep, "first call (graph_diff)", err, wall)
    note = str(report.get("note") or "")
    ok = report.get("baseline_id") is None and not report["changes"]
    _gd_row(
        rep,
        "first call: no baseline, no change",
        "no baseline",
        report.get("baseline_id"),
        ok and note.startswith("No baseline yet"),
        wall,
        note,
    )


def _check_gd_steady(truth: dict, res: Results, rep: Report) -> None:
    report, wall, err = _graph_diff_report(res, truth["call"])
    if err:
        return _gd_unavailable(rep, "steady graph (graph_diff)", err, wall)
    got = _change_keys(report)
    _gd_row(
        rep,
        "steady graph: zero changes",
        truth["changes"],
        got,
        not got,
        wall,
        "any change here is a false alarm",
    )
    rep.add(
        "graph_diff",
        "steady graph: builtin_changes_count",
        "-",
        report.get("builtin_changes_count"),
        "INFO",
        "/rosout and /parameter_events counter changes, folded",
        wall,
    )


def _check_gd_v2(truth: dict, res: Results, rep: Report) -> None:
    report, wall, err = _graph_diff_report(res, truth["call"])
    if err:
        return _gd_unavailable(rep, "graph v2 (graph_diff)", err, wall)
    want = sorted((c["kind"], c["change"], c["name"]) for c in truth["changes"])
    got = _change_keys(report)
    _gd_row(
        rep,
        "v2: exactly the three planned changes",
        want,
        got,
        got == want,
        wall,
        "any other change is a false alarm",
    )
    counts = [c for c in report["changes"] if c["kind"] == "count"]
    for planned in (c for c in truth["changes"] if c["kind"] == "count"):
        found = next((c for c in counts if c["name"] == planned["name"]), {})
        pair = (found.get("field"), found.get("before"), found.get("after"))
        wanted = (planned["field"], planned["before"], planned["after"])
        _gd_row(rep, f"v2: {planned['name']} count", wanted, pair, pair == wanted, wall)


def _check_gd_bag(truth: dict, res: Results, rep: Report) -> None:
    report, wall, err = _graph_diff_report(res, truth["call"])
    if err == "missing":
        return  # the bench ran without a bag
    if err:
        return _gd_unavailable(rep, "bag baseline (graph_diff)", err, wall)
    baseline = (report.get("baseline") or {}).get("source")
    _gd_row(rep, "bag baseline: source", "bag", baseline, baseline == "bag", wall)
    _gd_row(
        rep,
        "bag baseline: removed_count",
        truth["removed_count"],
        report.get("removed_count"),
        report.get("removed_count") == truth["removed_count"],
        wall,
        "every recorded topic is still live",
    )
    bad = sorted({c["kind"] for c in report["changes"]} & set(truth["absent_kinds"]))
    _gd_row(rep, "bag baseline: no type or qos change", [], bad, not bad, wall)
    missing = sorted(set(truth["not_compared"]) - set(report.get("not_compared") or {}))
    _gd_row(
        rep,
        "bag baseline: not_compared names nodes and rates",
        truth["not_compared"],
        sorted(report.get("not_compared") or {}),
        not missing,
        wall,
    )


@guard("graph_diff")
def check_graph_diff(gt: dict, res: Results, rep: Report) -> None:
    """`graph_diff`: first call, steady graph, graph v2 and a bag baseline (0.8.0)."""
    truth = gt.get("graph_diff")
    if not truth:
        return
    _check_gd_first(truth["first"], res, rep)
    _check_gd_steady(truth["steady"], res, rep)
    _check_gd_v2(truth["v2"], res, rep)
    _check_gd_bag(truth["bag"], res, rep)


# ---------------------------------------------------------------------------- visibility
# Self-contained: the `visibility` section of the truth (only the bench writes one) against the
# health, participant and endpoint calls it names (0.8.0). The node name is what ties an endpoint
# to a ROS 2 node when every participant is called `/` (Fast DDS), so it has to hold on both RMWs.
def _check_vis_endpoints(truth: dict, res: Results, rep: Report) -> None:
    name = truth["endpoints_call"]
    data = res.load(name)
    err = tool_err(data)
    wall = res.times.get(name)
    if data is None or err:
        verdict, got = ("WARN", "absent") if data is None else ("FAIL", err)
        rep.add(
            "visibility", "list_endpoints node_name", truth["writer_nodes"], got, verdict, "", wall
        )
        return
    writers = [e for e in listing(data, "endpoints") if e.get("role") == "writer"]
    got_nodes = sorted({e["node_name"] for e in writers if e.get("node_name")})
    rep.add(
        "visibility",
        f"{truth['topic']} writer node_name",
        truth["writer_nodes"],
        got_nodes,
        "PASS"
        if got_nodes == truth["writer_nodes"] and all(e.get("node_name") for e in writers)
        else "FAIL",
        f"{len(writers)} writer(s); the node name comes from ros_discovery_info",
        wall,
    )
    summaries = [s for s in listing(data, "by_topic") if s.get("ros_topic") == truth["topic"]]
    got_summary = sorted(summaries[0].get("writer_nodes") or []) if summaries else None
    rep.add(
        "visibility",
        f"{truth['topic']} writer_nodes",
        truth["writer_nodes"],
        got_summary,
        "PASS" if got_summary == truth["writer_nodes"] else "FAIL",
        "",
        wall,
    )


def _check_vis_participant(truth: dict, res: Results, rep: Report) -> None:
    name = truth["participants_call"]
    data = res.load(name)
    err = tool_err(data)
    wall = res.times.get(name)
    metric = f"{truth['node']} participant endpoint_count"
    if data is None or err:
        verdict, got = ("WARN", "absent") if data is None else ("FAIL", err)
        rep.add("visibility", metric, f">= {truth['min_endpoint_count']}", got, verdict, "", wall)
        return
    hosts = [
        p for p in listing(data, "participants") if truth["node"] in (p.get("node_names") or [])
    ]
    counts = [p.get("endpoint_count") for p in hosts]
    ok = bool(hosts) and all(
        isinstance(c, int) and c >= truth["min_endpoint_count"] for c in counts
    )
    rep.add(
        "visibility",
        metric,
        f">= {truth['min_endpoint_count']}",
        counts or "no participant hosts the node",
        "PASS" if ok else "FAIL",
        "writers plus readers, plumbing included",
        wall,
    )


def _check_vis_health(truth: dict, res: Results, rep: Report) -> None:
    name = truth["health_call"]
    data = res.load(name)
    wall = res.times.get(name)
    if data is None or tool_err(data):
        return  # the graph section already reports the missing health_check
    health = (objs(data) or [{}])[0]
    got = health.get("ros_security_enabled", "absent")
    rep.add(
        "visibility",
        "health ros_security_enabled",
        truth["ros_security_enabled"],
        got,
        "PASS" if got is truth["ros_security_enabled"] else "FAIL",
        "the bench sets no ROS_SECURITY_* variable",
        wall,
    )
    note = health.get("ros_security_note")
    rep.add(
        "visibility",
        "health ros_security_note",
        None,
        note,
        "PASS" if note is None else "FAIL",
        "",
        wall,
    )


@guard("visibility")
def check_visibility(gt: dict, res: Results, rep: Report) -> None:
    """DDS visibility: endpoint node names, participant endpoint counts, SROS2 health (0.8.0)."""
    truth = gt.get("visibility")
    if not truth:
        return
    _check_vis_endpoints(truth, res, rep)
    _check_vis_participant(truth, res, rep)
    _check_vis_health(truth, res, rep)


SECTIONS = (
    check_graph,
    check_scan,
    check_stamps,
    check_rates,
    check_bag,
    check_dds,
    check_tf,
    check_clock,
    check_logs,
    check_graph_diff,
    check_visibility,
    check_listing,
)


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
            f"- {t}: cv {v['interval_cv']} n={v['message_count']} verdict {v['verdict']} "
            f"span_s {v['span_s']} stamp_cv {v['stamp_cv']} burst {v['startup_burst_count']}"
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
        print(
            f"RATE_CV {topic} {v['interval_cv']} n={v['message_count']} {v['verdict']} "
            f"span_s={v['span_s']} stamp_cv={v['stamp_cv']} burst={v['startup_burst_count']}"
        )
    return 1 if verdict == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
