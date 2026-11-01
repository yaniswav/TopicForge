"""`diagnose`: one bounded sweep of the ROS 2 graph and the DDS bus, ranked by severity.

The service sits above the existing services and adds no adapter method. It calls
`Inspector.list_nodes`, `list_topics`, `get_node_info`, `inspect_tf`, `sample_messages`,
`detect_qos_mismatches`, `participant_events` and `list_participants`, and the health report
builder. On the DDS side it never calls `peek_dds_samples` or `topic_metrics`: the three DDS
calls it makes are answered by the discovery caches, never by the binding.

One function per check, `check_<name>(ctx) -> list[Finding]`, in the order the orchestrator runs
them. `responsiveness` runs last because it is the budget sink: it reads node parameters for as
many nodes as the remaining budget allows. A check that raises `AdapterError` becomes a
`checks_skipped` entry; a check that no longer fits in the budget becomes one with reason
`budget`. The severity table is in docs/CONTRACT.md section 9.21: every `critical` maps to a row.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from topicforge import budget
from topicforge.adapters.base import AdapterError
from topicforge.constants import (
    DEFAULT_DIAGNOSE_BUDGET_S,
    MAX_DIAGNOSE_BUDGET_S,
    MIN_DIAGNOSE_BUDGET_S,
)
from topicforge.models import (
    Diagnosis,
    Evidence,
    Finding,
    HealthReport,
    MismatchScan,
    NotMatchedPair,
    SkippedCheck,
    SuggestedFix,
)
from topicforge.models.diagnose import CHECKS, EVIDENCE_VALUE_MAX, MAX_FINDINGS, SEVERITIES
from topicforge.services.dds_views import join_notes
from topicforge.services.graph_diff import BUILTIN_COUNT_TOPICS
from topicforge.services.health import HealthService
from topicforge.services.inspector import Inspector, _validate_node_name

Scope = Literal["all", "graph", "tf", "dds", "logs"]

SCOPES: tuple[str, ...] = ("all", "graph", "tf", "dds", "logs")
_SCOPE_CHECKS: dict[str, tuple[str, ...]] = {
    "all": CHECKS,
    "graph": ("nodes", "topics", "responsiveness"),
    "tf": ("tf",),
    "dds": ("dds",),
    "logs": ("logs",),
}

# Seconds that must be left for a check to start; below it the check is skipped for `budget`.
_FLOOR_S: dict[str, float] = {
    "nodes": 3.0,
    "topics": 2.0,
    "dds": 1.0,
    "tf": 3.0,
    "logs": 4.0,
    "responsiveness": 3.0,
}
_ROS_CHECKS = frozenset({"nodes", "topics", "tf", "logs", "responsiveness"})
_RESERVE_S = 2.0  # kept back inside the call's own deadline
_TF_WINDOW_S = 8.0
_TF_STALE_AFTER_S = 2.0
_TF_OVERHEAD_S = 2.0  # CLI start-up, URDF read and parsing around the capture
_LOG_TIMEOUT_S = 3.0
_LOG_COUNT = 50
_NODE_READ_S = 3.0
_NAMED_NODE_READ_S = 6.0
_EVENT_LOOKBACK_S = 300
_LIST_NAMES = 8
_UNTESTED_NAMES = 20
_TYPO_HINT = "Likely a topic name typo"
_TYPO_TOPIC_RE = re.compile(r"^Topic '([^']+)' has a writer")
_NO_ENDPOINT_MARK = "announce no endpoint"


class _BudgetSkip(Exception):
    """A check found too little budget left to run."""


@dataclass
class _Ctx:
    """State shared by the checks of one sweep."""

    inspector: Inspector
    health: HealthService
    node: str | None
    clock: Callable[[], float]
    deadline: float
    plan: tuple[str, ...]
    report: HealthReport | None = None
    nodes: list[str] | None = None
    lifecycle: frozenset[str] = frozenset()
    skipped: list[SkippedCheck] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def left(self) -> float:
        return self.deadline - self.clock()


def _ev(tool: str, name: str, value: object) -> Evidence:
    text = str(value)
    if len(text) > EVIDENCE_VALUE_MAX:
        text = text[: EVIDENCE_VALUE_MAX - 3] + "..."
    return Evidence(tool=tool, field=name, value=text)


def _finding(
    severity: Literal["critical", "warning", "info"],
    check: str,
    subject: str | None,
    text: str,
    *evidence: Evidence,
    fixes: list[SuggestedFix] | None = None,
) -> Finding:
    return Finding(
        severity=severity,
        check=check,  # type: ignore[arg-type]
        subject=subject,
        text=text,
        evidence=list(evidence),
        suggested_fixes=list(fixes or []),
    )


def _names(items: list[str], limit: int = _LIST_NAMES) -> str:
    shown = ", ".join(items[:limit])
    return shown + (f" and {len(items) - limit} more" if len(items) > limit else "")


# ---- checks -------------------------------------------------------------------------------


def check_environment(ctx: _Ctx) -> list[Finding]:
    """Mode and SROS2 facts from the health report."""
    report = ctx.health.report()
    ctx.report = report
    out: list[Finding] = []
    if report.mode == "mock":
        out.append(
            _finding(
                "info",
                "environment",
                None,
                "Mock mode: every result is a fixture of a demo robot, not your robot.",
                _ev("health_check", "mode", report.mode),
            )
        )
    if report.ros_security_enabled:
        out.append(
            _finding(
                "info",
                "environment",
                None,
                report.ros_security_note or "SROS2 is enabled in the environment.",
                _ev("health_check", "ros_security_enabled", True),
            )
        )
    return out


def check_nodes(ctx: _Ctx) -> list[Finding]:
    """Duplicate names, an empty live graph, lifecycle nodes."""
    listing = ctx.inspector.list_nodes()
    ctx.nodes = [n.full_name for n in listing.nodes]
    ctx.lifecycle = frozenset(n.full_name for n in listing.nodes if n.lifecycle)
    out = [
        _finding(
            "warning",
            "nodes",
            name,
            f"Several nodes use the name {name}: per-node tools answer for one of them.",
            _ev("list_nodes", "duplicates", name),
        )
        for name in listing.duplicates
    ]
    if listing.total == 0 and listing.mode_effective == "live":
        out.append(
            _finding(
                "warning",
                "nodes",
                None,
                "No node on the ROS 2 graph: nothing is running, or it uses another ROS_DOMAIN_ID.",
                _ev("list_nodes", "total", 0),
            )
        )
    if listing.lifecycle_count:
        out.append(
            _finding(
                "info",
                "nodes",
                None,
                f"{listing.lifecycle_count} lifecycle node(s): their state is not read.",
                _ev("list_nodes", "lifecycle_count", listing.lifecycle_count),
            )
        )
    return out


def check_topics(ctx: _Ctx) -> list[Finding]:
    """Subscribed topics nobody publishes; publishers nobody reads, folded into one info."""
    topics = [t for t in ctx.inspector.list_topics().topics if t.name not in BUILTIN_COUNT_TOPICS]
    out = [
        _finding(
            "warning",
            "topics",
            t.name,
            f"{t.name} has {t.subscriber_count} subscriber(s) and no publisher: they wait for data.",
            _ev("list_topics", "publisher_count", f"{t.name}={t.publisher_count}"),
        )
        for t in topics
        if t.subscriber_count > 0 and t.publisher_count == 0
    ]
    silent = [t.name for t in topics if t.publisher_count > 0 and t.subscriber_count == 0]
    if silent:
        out.append(
            _finding(
                "info",
                "topics",
                None,
                f"{len(silent)} topic(s) have a publisher and no subscriber (often normal): "
                f"{_names(silent)}.",
                _ev("list_topics", "subscriber_count", f"0 on {_names(silent, 4)}"),
            )
        )
    return out


def check_dds(ctx: _Ctx) -> list[Finding]:
    """QoS pairs that cannot connect, restart loops and departures, from the discovery caches."""
    scan = ctx.inspector.detect_qos_mismatches()
    events = ctx.inspector.participant_events(0, _EVENT_LOOKBACK_S)
    parts = ctx.inspector.list_participants(0)
    out = _qos_findings(scan)
    lives = _lives(parts.restarts_by_node, events.restarts_by_name)
    out += _restart_findings(lives)
    left = [
        (p.node_names[0] if p.node_names else (p.name or p.guid[:8]))
        for p in parts.participants
        if p.status == "left" and not p.is_topicforge_cli and not p.is_observer
    ]
    left = sorted({n for n in left if n.lstrip("/") not in lives})
    if left:
        out.append(
            _finding(
                "info",
                "dds",
                None,
                f"{len(left)} participant(s) left and did not come back: {_names(left)}.",
                _ev("list_participants", "participants[].status", f"left: {_names(left, 4)}"),
            )
        )
    if parts.note and _NO_ENDPOINT_MARK in parts.note:
        out.append(
            _finding("info", "dds", None, parts.note, _ev("list_participants", "note", parts.note))
        )
    return out


def _qos_findings(scan: MismatchScan) -> list[Finding]:
    out: list[Finding] = []
    for i, r in enumerate(scan.reports):
        writer = r.writer_node or r.writer_participant_name or "a writer"
        reader = r.reader_node or r.reader_participant_name or "a reader"
        policies = ", ".join(r.incompatible_policies)
        blocked = r.severity == "incompatible"
        verb = "cannot connect to" if blocked else "may lose data towards"
        values = "; ".join(
            f"{d.policy}: requested {d.requested}, offered {d.offered}" for d in r.details
        )
        out.append(
            _finding(
                "critical" if blocked else "warning",
                "dds",
                r.topic,
                f"Writer {writer} {verb} reader {reader} on {r.topic} ({policies}). {values}".strip(),
                _ev("detect_qos_mismatches", f"reports[{i}].incompatible_policies", policies),
                _ev("detect_qos_mismatches", f"reports[{i}].details", values),
                fixes=list(r.suggested_fixes),
            )
        )
    seen: dict[tuple[str, str], list[NotMatchedPair]] = {}
    for pair in scan.not_matched:
        seen.setdefault((pair.topic, pair.reason), []).append(pair)
    for (topic, reason), pairs in sorted(seen.items()):
        first = pairs[0]
        fixes = next((p.suggested_fixes for p in pairs if p.suggested_fixes), [])
        out.append(
            _finding(
                "critical",
                "dds",
                topic,
                f"{len(pairs)} reader/writer pair(s) on {topic} never match ({reason}): "
                f"{first.detail}. No data flows between them.",
                _ev("detect_qos_mismatches", "not_matched[].reason", reason),
                fixes=list(fixes),
            )
        )
    for hint in scan.hints:
        if _TYPO_HINT in hint:
            m = _TYPO_TOPIC_RE.match(hint)
            out.append(
                _finding(
                    "warning",
                    "dds",
                    m.group(1) if m else None,
                    hint,
                    _ev("detect_qos_mismatches", "hints", hint),
                )
            )
    return out


def _lives(by_node: dict, by_name: dict) -> dict[str, int]:
    """Lives per participant or node name; the largest count wins when both views have one."""
    lives: dict[str, int] = {}
    for table in (by_node, by_name):
        for name, life in table.items():
            key = name.lstrip("/")
            lives[key] = max(lives.get(key, 0), life.lives)
    return lives


def _restart_findings(lives: dict[str, int]) -> list[Finding]:
    out = []
    for name, count in sorted(lives.items()):
        if count < 2:
            continue
        loop = count >= 3
        out.append(
            _finding(
                "critical" if loop else "warning",
                "dds",
                f"/{name}",
                (
                    f"/{name} appeared with {count} different participants: a restart loop."
                    if loop
                    else f"/{name} restarted once (2 participants carried its name)."
                ),
                _ev("list_participants", f"restarts_by_node[/{name}].lives", count),
            )
        )
    return out


def check_tf(ctx: _Ctx) -> list[Finding]:
    """Stale, doubled or cyclic transforms, several roots, URDF links without a frame."""
    ahead = _FLOOR_S["logs"] if "logs" in ctx.plan else 0.0
    window = min(_TF_WINDOW_S, ctx.left() - _TF_OVERHEAD_S - ahead)
    if window < 1.0:
        raise _BudgetSkip
    window = round(window, 1)
    report = ctx.inspector.inspect_tf(window, _TF_STALE_AFTER_S)
    out: list[Finding] = []
    for f in report.findings:
        if f.kind in ("stale", "two_parents", "cycle", "static_and_dynamic"):
            subject = "->".join(f.frames) if f.kind == "stale" else ",".join(f.frames)
            out.append(
                _finding(
                    "warning",
                    "tf",
                    subject,
                    f.text,
                    _ev("inspect_tf", "findings[].kind", f"{f.kind}: {','.join(f.frames)}"),
                )
            )
    for frame in report.declared_frames_missing:
        out.append(
            _finding(
                "warning",
                "tf",
                frame,
                f"The URDF declares link {frame} but no TF frame carries it.",
                _ev("inspect_tf", "declared_frames_missing", report.declared_frames_missing),
            )
        )
    if len(report.roots) > 1:
        out.append(
            _finding(
                "info",
                "tf",
                None,
                f"{len(report.roots)} TF roots ({_names(report.roots)}): the tree is in pieces.",
                _ev("inspect_tf", "roots", report.roots),
            )
        )
    if report.urdf_note:
        out.append(
            _finding(
                "info",
                "tf",
                None,
                report.urdf_note,
                _ev("inspect_tf", "urdf_note", report.urdf_note),
            )
        )
    if not report.frames and report.note:
        out.append(
            _finding("info", "tf", None, report.note, _ev("inspect_tf", "note", report.note))
        )
    if window < _TF_WINDOW_S:
        out.append(
            _finding(
                "info",
                "tf",
                None,
                f"The budget cut the TF window to {window:g} s: a transform older than "
                f"{window:g} s may be missed.",
                _ev("inspect_tf", "window_s", window),
            )
        )
    return out


def check_logs(ctx: _Ctx) -> list[Finding]:
    """One warning per node that logged an error or fatal line while `/rosout` was listened to."""
    result = ctx.inspector.sample_messages(
        "/rosout", _LOG_COUNT, timeout_s=_LOG_TIMEOUT_S, min_log_level="error"
    )
    latest: dict[str, tuple[int, str]] = {}
    for sample in result.samples:  # oldest first: the last line of a node is its newest
        name = str(sample.payload.get("name") or "")
        count = latest.get(name, (0, ""))[0] + 1
        latest[name] = (count, str(sample.payload.get("msg") or ""))
    if not latest:
        return [
            _finding(
                "info",
                "logs",
                None,
                f"No error-level line on /rosout in the {_LOG_TIMEOUT_S:g} s listened to.",
                _ev("sample_messages", "count", result.count),
            )
        ]
    out = []
    for name, (count, msg) in sorted(latest.items()):
        subject = name if name.startswith("/") or not name else "/" + name.replace(".", "/")
        out.append(
            _finding(
                "warning",
                "logs",
                subject or None,
                f"{subject or 'A node'} logged {count} error or fatal line(s) on /rosout; "
                f"latest: {msg[:160]}",
                _ev("sample_messages", "samples[].payload.msg", msg),
            )
        )
    return out


def _candidates(ctx: _Ctx) -> list[str]:
    """Nodes to read, named one first, then lifecycle nodes, then the rest by name."""
    pool = [n for n in (ctx.nodes or []) if not n.rsplit("/", 1)[-1].startswith("_ros2cli")]
    ordered = sorted(pool, key=lambda n: (n not in ctx.lifecycle, n))
    if ctx.node is None:
        return ordered
    return [ctx.node] + [n for n in ordered if n != ctx.node]


def check_responsiveness(ctx: _Ctx) -> list[Finding]:
    """Read parameters of as many nodes as the remaining budget allows; silence is a finding."""
    candidates = _candidates(ctx)
    if not candidates:
        raise AdapterError("no node list to read from (the nodes check did not run)")
    out: list[Finding] = []
    untested: list[str] = []
    for i, name in enumerate(candidates):
        named = name == ctx.node
        timeout = min(_NAMED_NODE_READ_S if named else _NODE_READ_S, ctx.left())
        if timeout < _NODE_READ_S:
            untested = candidates[i:]
            break
        try:
            info = ctx.inspector.get_node_info(name, timeout)
        except AdapterError as exc:
            ctx.skipped.append(SkippedCheck(check="responsiveness", reason=f"{name}: {exc}"))
            continue
        if info.parameters is not None:
            continue
        evidence = _ev("get_node_info", "parameters_note", info.parameters_note or "null")
        if named:
            out.append(
                _finding(
                    "critical",
                    "responsiveness",
                    name,
                    f"{name} did not answer a parameter read within {timeout:g} s: its executor "
                    "is probably blocked.",
                    evidence,
                )
            )
        else:
            out.append(
                _finding(
                    "warning",
                    "responsiveness",
                    name,
                    f"{name} did not answer a parameter read within {timeout:g} s (blocked "
                    "executor, or a loaded host): confirm with get_node_info(node, timeout_s=8).",
                    evidence,
                )
            )
    if untested:
        out.append(
            _finding(
                "info",
                "responsiveness",
                None,
                f"{len(untested)} node(s) not tested, the budget ran out: "
                f"{_names(untested, _UNTESTED_NAMES)}.",
                _ev("list_nodes", "nodes[].full_name", _names(untested, 4)),
            )
        )
    return out


_CHECK_FUNCTIONS: dict[str, Callable[[_Ctx], list[Finding]]] = {
    "environment": check_environment,
    "nodes": check_nodes,
    "topics": check_topics,
    "dds": check_dds,
    "tf": check_tf,
    "logs": check_logs,
    "responsiveness": check_responsiveness,
}


# ---- orchestrator -------------------------------------------------------------------------


class DiagnoseService:
    """Runs the checks of `scope` inside a wall budget and ranks what they found."""

    def __init__(
        self,
        inspector: Inspector,
        health: HealthService,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._inspector = inspector
        self._health = health
        self._clock = clock

    def diagnose(
        self,
        scope: str = "all",
        budget_s: float = DEFAULT_DIAGNOSE_BUDGET_S,
        node: str | None = None,
    ) -> Diagnosis:
        """The sweep. Raises `AdapterError` only on invalid input; a failing check is skipped."""
        scope, budget_s, node_name = _validate(scope, budget_s, node)
        start = self._clock()
        left = budget.remaining()
        horizon = budget_s if left is None else min(budget_s, left - _RESERVE_S)
        plan = _SCOPE_CHECKS[scope]
        if scope != "all":
            plan = ("environment", *plan)
        ctx = _Ctx(self._inspector, self._health, node_name, self._clock, start + horizon, plan)
        ran: list[str] = []
        findings: list[Finding] = []
        with budget.deadline_at(time.monotonic() + horizon):
            for name in plan:
                found = self._run_check(ctx, name)
                if found is not None:
                    ran.append(name)
                    findings += found
        return _assemble(ctx, scope, budget_s, start, ran, findings)

    def _run_check(self, ctx: _Ctx, name: str) -> list[Finding] | None:
        """The findings of one check, or `None` after recording why it did not run."""
        reason = self._unavailable(ctx, name)
        if reason is None and ctx.left() < _FLOOR_S.get(name, 0.0):
            reason = "budget"
        if reason is None:
            try:
                return _CHECK_FUNCTIONS[name](ctx)
            except _BudgetSkip:
                reason = "budget"
            except AdapterError as exc:
                reason = str(exc) or "adapter error"
        ctx.skipped.append(SkippedCheck(check=name, reason=reason))  # type: ignore[arg-type]
        return None

    @staticmethod
    def _unavailable(ctx: _Ctx, name: str) -> str | None:
        """Why the backend of `name` is absent, from the health report, else `None`."""
        report = ctx.report
        if report is None:
            return None
        if name in _ROS_CHECKS and report.ros_backend == "none":
            return "no ROS 2 backend: the `ros2` CLI is not available"
        if name == "dds" and report.dds_backend == "none":
            return report.dds_inactive_note or "no DDS backend is active"
        return None


def _validate(scope: str, budget_s: float, node: str | None) -> tuple[str, float, str | None]:
    if scope not in SCOPES:
        raise AdapterError(f"scope must be one of {', '.join(SCOPES)}, got {scope!r}")
    if isinstance(budget_s, bool) or not isinstance(budget_s, (int, float)):
        raise AdapterError(f"budget_s must be a number, got {type(budget_s).__name__}")
    if not MIN_DIAGNOSE_BUDGET_S <= budget_s <= MAX_DIAGNOSE_BUDGET_S:
        raise AdapterError(
            f"budget_s must be in {MIN_DIAGNOSE_BUDGET_S:g}..{MAX_DIAGNOSE_BUDGET_S:g}, "
            f"got {budget_s}"
        )
    return scope, float(budget_s), None if node is None else _validate_node_name(node)


def _verdict(scope: str, counts: tuple[int, int], ran: int, skipped: list[str]) -> str:
    critical, warning = counts
    base = (
        f"{critical} critical and {warning} warning finding(s) in the {ran} checks run"
        if critical + warning
        else f"no critical or warning finding in the {ran} checks run"
    )
    caveats = ""
    if skipped:
        caveats += f", but {', '.join(skipped)} did not run (not verified)"
    if scope != "all":
        caveats += f"; scope {scope} only, the other checks were not run"
    verdict = f"{base}{caveats}; data flow itself is not observed"
    return f"Incomplete sweep: {verdict}" if skipped else verdict


def _assemble(
    ctx: _Ctx,
    scope: str,
    budget_s: float,
    start: float,
    ran: list[str],
    findings: list[Finding],
) -> Diagnosis:
    rank = {name: i for i, name in enumerate(SEVERITIES)}
    ordered = sorted(findings, key=lambda f: (rank[f.severity], f.check, f.subject or ""))
    counts = {s: sum(1 for f in ordered if f.severity == s) for s in SEVERITIES}
    if len(ordered) > MAX_FINDINGS:
        ctx.notes.append(
            f"Showing {MAX_FINDINGS} of {len(ordered)} findings; the counts cover all."
        )
    skipped_names = sorted({s.check for s in ctx.skipped}, key=CHECKS.index)
    return Diagnosis(
        findings=ordered[:MAX_FINDINGS],
        findings_total=len(ordered),
        critical_count=counts["critical"],
        warning_count=counts["warning"],
        info_count=counts["info"],
        checks_run=ran,  # type: ignore[arg-type]
        checks_skipped=ctx.skipped,
        budget_s=budget_s,
        elapsed_s=round(max(0.0, ctx.clock() - start), 3),
        verdict=_verdict(scope, (counts["critical"], counts["warning"]), len(ran), skipped_names),
        mode_effective=ctx.inspector.effective_mode,
        note=join_notes(*ctx.notes),
    )


__all__ = ["SCOPES", "DiagnoseService", "Scope"]
