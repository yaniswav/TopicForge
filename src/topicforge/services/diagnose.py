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
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Literal

from topicforge import budget
from topicforge.adapters.base import AdapterError
from topicforge.adapters.common.ros_names import ros_topic_of
from topicforge.constants import (
    DEFAULT_DIAGNOSE_BUDGET_S,
    DEFAULT_MAX_ARRAY_LENGTH,
    MAX_DIAGNOSE_BUDGET_S,
    MIN_DIAGNOSE_BUDGET_S,
    TRUNCATED_FIELDS_KEY,
)
from topicforge.models import (
    Diagnosis,
    Evidence,
    Finding,
    HealthReport,
    MismatchScan,
    NameLifecycle,
    NodeInfo,
    NotMatchedPair,
    ParticipantEvent,
    ParticipantInfo,
    SkippedCheck,
    SuggestedFix,
    TfReport,
    TopicListItem,
)
from topicforge.models.diagnose import CHECKS, EVIDENCE_VALUE_MAX, MAX_FINDINGS, SEVERITIES
from topicforge.services.dds_views import join_notes
from topicforge.services.graph_diff import BUILTIN_COUNT_TOPICS
from topicforge.services.health import HealthService
from topicforge.services.inspector import Inspector, _validate_node_name
from topicforge.services.params_file import validate_params_file

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
_FRAME_TOPICS = 3  # stamped topics sampled for the frame_id check
_FRAME_SAMPLE_S = 4.0  # whole call for one topic: the CLI start-up, the topic lookup and the echo
_FRAME_MIN_S = 3.0  # budget left below which no further topic is sampled
# Stamped sensor types whose `header.frame_id` names a TF frame, most telling first.
_STAMPED_TYPES: tuple[str, ...] = (
    "sensor_msgs/msg/LaserScan",
    "sensor_msgs/msg/PointCloud2",
    "sensor_msgs/msg/Image",
    "sensor_msgs/msg/CompressedImage",
    "sensor_msgs/msg/Imu",
    "nav_msgs/msg/Odometry",
    "sensor_msgs/msg/Range",
    "sensor_msgs/msg/NavSatFix",
    "sensor_msgs/msg/MagneticField",
)
_LOG_TIMEOUT_S = 5.0
_LOG_COUNT = 50
_NODE_READ_S = 3.0
_NAMED_NODE_READ_S = 6.0
_EVENT_LOOKBACK_S = 300
_NS_PER_S = 1_000_000_000
_STILL_RESTARTING_S = 60  # a node discovered this recently is taken to be still restarting
_MIN_START_SPREAD_S = 2  # starts closer together than this are one discovery burst
_MAX_PARAM_DIFFS = 5  # differing parameters named in one finding
_SAFE_NAME = re.compile(r"[A-Za-z0-9_./~-]+")  # what may go unquoted into a command
_LOG_STOP_RESERVE_S = 3.0  # the live adapter keeps this back from a sampling call
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
    params_file: str | None
    clock: Callable[[], float]
    deadline: float
    plan: tuple[str, ...]
    report: HealthReport | None = None
    nodes: list[str] | None = None
    topics: list[TopicListItem] | None = None  # set by the topics check, read by the TF check
    tf_report: TfReport | None = None  # a complete TF capture, for the frame_id pass
    lifecycle: frozenset[str] = frozenset()
    wall_ns: Callable[[], int] = time.time_ns
    skipped: list[SkippedCheck] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    named_ids: set[int] = field(default_factory=set)  # id() of findings that involve `node`

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
        check=check,
        subject=subject,
        text=text,
        evidence=list(evidence),
        suggested_fixes=list(fixes or []),
    )


def _names(items: list[str], limit: int = _LIST_NAMES) -> str:
    shown = ", ".join(items[:limit])
    return shown + (f" and {len(items) - limit} more" if len(items) > limit else "")


def _read_fix(
    text: str, target: str, tradeoff: str, verify_with: str, kind: str = "cli"
) -> list[SuggestedFix]:
    """One read-only next step: a command that only observes. Never a repair."""
    return [
        SuggestedFix(
            kind=kind,  # type: ignore[arg-type]
            target=target,
            text=text,
            tradeoff=tradeoff,
            verify_with=verify_with,
        )
    ]


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
    """Topics with one side only, each side folded into one info.

    A subscriber waiting on a topic nobody publishes is normal on a healthy robot (a simulator
    listening on `/cmd_vel`, RViz on `/goal_pose`), so it is never above info.
    """
    topics = [t for t in ctx.inspector.list_topics().topics if t.name not in BUILTIN_COUNT_TOPICS]
    ctx.topics = topics
    out = []
    unknown = [t.name for t in topics if t.counts_unknown]
    if unknown:
        out.append(
            _finding(
                "info",
                "topics",
                None,
                f"{len(unknown)} topic(s) whose publisher and subscriber counts could not be "
                f"read, so they are not judged: {_names(unknown)}.",
                _ev("list_topics", "counts_unknown", f"true on {_names(unknown, 4)}"),
            )
        )
    topics = [t for t in topics if not t.counts_unknown]
    waiting = [t.name for t in topics if t.subscriber_count > 0 and t.publisher_count == 0]
    if waiting:
        out.append(
            _finding(
                "info",
                "topics",
                None,
                f"{len(waiting)} topic(s) have a subscriber and no publisher (normal for command "
                f"inputs): {_names(waiting)}.",
                _ev("list_topics", "publisher_count", f"0 on {_names(waiting, 4)}"),
            )
        )
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
    out = _qos_findings(scan, ctx)
    recent = _recent_node_lives(
        parts.participants, events.events, ctx.wall_ns() - _EVENT_LOOKBACK_S * _NS_PER_S
    )
    lives = _lives(recent, events.restarts_by_name)
    starts = _discovery_times(parts.participants, events.events)
    cli_up = ctx.report is None or ctx.report.ros_backend != "none"
    ros_nodes = {n.lstrip("/") for n in recent} | {n.lstrip("/") for n in ctx.nodes or ()}
    out += _restart_findings(lives, starts, ctx.wall_ns(), ros_nodes if cli_up else frozenset())
    left = [
        (p.node_names[0] if p.node_names else (p.name or p.guid[:8]))
        for p in parts.participants
        if p.status == "left" and not p.is_topicforge_cli and not p.is_observer
    ]
    running = {
        n.lstrip("/") for p in parts.participants if p.status == "active" for n in p.node_names
    }
    left = sorted(
        {n for n in left if lives.get(n.lstrip("/"), 0) < 2 and n.lstrip("/") not in running}
    )
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


def _topic_label(dds_topic: str) -> str:
    """A DDS topic with its ROS 2 name beside it: `rt/scan (ROS /scan)`; as is when there is none."""
    ros, _ = ros_topic_of(dds_topic)
    return f"{dds_topic} (ROS {ros})" if ros else dds_topic


def _qos_findings(scan: MismatchScan, ctx: _Ctx) -> list[Finding]:
    out: list[Finding] = []
    groups: dict[tuple[str, str, str, tuple[str, ...]], list[int]] = {}
    for i, r in enumerate(scan.reports):  # one finding per (topic, writer, severity, policies)
        writer = r.writer_guid or r.writer_node or r.writer_participant_name or f"#{i}"
        key = (r.topic, writer, r.severity, tuple(sorted(r.incompatible_policies)))
        groups.setdefault(key, []).append(i)  # a writer with no identity never merges
    for indexes in groups.values():
        members = [scan.reports[i] for i in indexes]
        r = members[0]
        writer = r.writer_node or r.writer_participant_name or "a writer"
        readers = [
            x.reader_node or x.reader_participant_name or x.reader_guid or "a reader"
            for x in members
        ]
        policies = ", ".join(r.incompatible_policies)
        blocked = r.severity == "incompatible"
        verb = "cannot connect to" if blocked else "may lose data towards"
        who = f"reader {readers[0]}"
        if len(readers) > 1:
            who = f"{len(readers)} readers ({_names(readers)})"
        distinct = list(
            dict.fromkeys(
                "; ".join(
                    f"{d.policy}: requested {d.requested}, offered {d.offered}" for d in x.details
                )
                for x in members
            )
        )
        values = " | ".join(distinct[:3]) + (" | ..." if len(distinct) > 3 else "")
        fixes = next((x.suggested_fixes for x in members if x.suggested_fixes), [])
        finding = _finding(
            "critical" if blocked else "warning",
            "dds",
            r.topic,
            f"Writer {writer} {verb} {who} on {_topic_label(r.topic)} ({policies}). {values}".strip(),
            _ev(
                "detect_qos_mismatches",
                f"reports[{indexes[0]}].incompatible_policies",
                policies,
            ),
            _ev("detect_qos_mismatches", f"reports[{indexes[0]}].details", values),
            fixes=list(fixes),
        )
        out.append(finding)
        if ctx.node is not None and ctx.node in (
            {x.writer_node for x in members} | {x.reader_node for x in members}
        ):
            ctx.named_ids.add(id(finding))
    seen: dict[tuple[str, str], list[NotMatchedPair]] = {}
    for pair in scan.not_matched:
        seen.setdefault((pair.topic, pair.reason), []).append(pair)
    for (topic, reason), pairs in sorted(seen.items()):
        first = pairs[0]
        fixes = next((p.suggested_fixes for p in pairs if p.suggested_fixes), [])
        finding = _finding(
            "critical",
            "dds",
            topic,
            f"{len(pairs)} reader/writer pair(s) on {_topic_label(topic)} never match ({reason}): "
            f"{first.detail}. No data flows between them.",
            _ev("detect_qos_mismatches", "not_matched[].reason", reason),
            fixes=list(fixes),
        )
        out.append(finding)
        if ctx.node is not None and ctx.node in (
            {x.writer_node for x in pairs} | {x.reader_node for x in pairs}
        ):
            ctx.named_ids.add(id(finding))
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


def _recent_node_lives(
    participants: list[ParticipantInfo], events: list[ParticipantEvent], cutoff_ns: int
) -> dict[str, int]:
    """Lives per ROS node name inside the lookback window, from the participant table.

    A participant is a life of its node when it is active, was seen since `cutoff_ns`, has a
    discovery or loss event in the window. Participants that left before the window (an old
    relaunch), or left with no timestamp and no event in it, are history, not a restart loop.
    """
    in_window = {e.guid for e in events}
    guids: dict[str, set[str]] = {}
    for p in participants:
        if p.is_topicforge_cli:
            continue
        recent = (
            p.status == "active"
            or p.guid in in_window
            or (p.last_seen_ns is not None and p.last_seen_ns >= cutoff_ns)
        )
        if recent:
            for node in p.node_names:
                guids.setdefault(node, set()).add(p.guid)
    return {node: len(found) for node, found in guids.items()}


def _lives(by_node: dict[str, int], by_name: Mapping[str, NameLifecycle]) -> dict[str, int]:
    """Lives per participant or node name; the largest count wins when both views have one."""
    lives: dict[str, int] = {}
    for name, count in by_node.items():
        key = name.lstrip("/")
        lives[key] = max(lives.get(key, 0), count)
    for name, life in by_name.items():
        key = name.lstrip("/")
        lives[key] = max(lives.get(key, 0), life.lives)
    return lives


def _discovery_times(
    participants: list[ParticipantInfo], events: list[ParticipantEvent]
) -> dict[str, list[int]]:
    """Local times of the `discovered` events, per node or participant name, oldest first.

    `observed_ns` is preferred: `timestamp_ns` can come from a remote clock.
    """
    names: dict[str, set[str]] = {}
    for p in participants:
        for name in (*p.node_names, p.name):
            if name:
                names.setdefault(p.guid, set()).add(name.lstrip("/"))
    times: dict[str, list[int]] = {}
    for e in events:
        if e.event_type != "discovered":
            continue
        for key in names.get(e.guid, ()):
            times.setdefault(key, []).append(
                e.timestamp_ns if e.observed_ns is None else e.observed_ns
            )
    return {key: sorted(found) for key, found in times.items()}


def _restart_trend(name: str, starts: Mapping[str, list[int]], now_ns: int) -> str:
    """Whether a loop goes on, judged against its own pace; empty when the events cannot tell.

    Starts all seen within `_MIN_START_SPREAD_S` are one discovery burst of a server that has
    just joined the bus, not a pace.
    """
    stamps = starts.get(name, [])
    if len(stamps) < 2 or stamps[-1] - stamps[0] < _MIN_START_SPREAD_S * _NS_PER_S:
        return ""
    gap = max(later - earlier for earlier, later in pairwise(stamps))
    ago = max(0, (now_ns - stamps[-1]) // _NS_PER_S)
    if ago * _NS_PER_S <= max(_STILL_RESTARTING_S * _NS_PER_S, 2 * gap):
        return (
            f" Still restarting: the last start was {ago} s ago."
            " The count is taken when this check runs and may differ on a later call."
        )
    return f" It stopped restarting: the last start was {ago} s ago."


def _restart_findings(
    lives: dict[str, int],
    starts: Mapping[str, list[int]],
    now_ns: int,
    log_nodes: Collection[str] = (),
) -> list[Finding]:
    """One finding per name carried by several participants.

    The `/rosout` command is offered only for a name in `log_nodes`: a ROS node, with a `ros2`
    CLI to run it. A bare DDS participant logs nothing there, and without the CLI the line
    could not be run.
    """
    out = []
    for name, count in sorted(lives.items()):
        if count < 2:
            continue
        loop = count >= 3
        trend = _restart_trend(name, starts, now_ns) if loop else ""
        out.append(
            _finding(
                "critical" if loop else "warning",
                "dds",
                f"/{name}",
                (
                    f"/{name} appeared with {count} different participants: a restart loop."
                    if loop
                    else f"/{name} restarted once (2 participants carried its name)."
                )
                + trend,
                _ev(
                    "participant_events",
                    f"participants of /{name} in the last {_EVENT_LOOKBACK_S} s",
                    count,
                ),
                fixes=(
                    _read_fix(
                        "ros2 topic echo /rosout",
                        f"/{name}",
                        "Streams the logs of every node; read those whose `name` is this one. "
                        "Observes only.",
                        "participant_events",
                    )
                    if name in log_nodes
                    else []
                ),
            )
        )
    return out


_TF_WARNING_KINDS = (
    "stale",
    "two_parents",
    "cycle",
    "static_and_dynamic",
    "fixed_joint_on_tf",
    "movable_joint_on_tf_static",
)


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
        if f.kind in _TF_WARNING_KINDS:
            subject = "->".join(f.frames) if f.kind == "stale" else ",".join(f.frames)
            out.append(
                _finding(
                    "warning",
                    "tf",
                    subject,
                    f.text,
                    _ev("inspect_tf", "findings[].kind", f"{f.kind}: {','.join(f.frames)}"),
                    fixes=_tf_fix(f.kind, f.frames),
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
                fixes=_read_fix(
                    "ros2 topic echo /tf_static",
                    "robot_state_publisher",
                    "Shows whether a fixed-joint link is published; a movable joint's link is "
                    "on /tf instead. Observes only.",
                    "inspect_tf",
                ),
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
    if report.frames and not report.truncated and window >= _TF_WINDOW_S:
        ctx.tf_report = report  # only a full, uncut capture can tell a frame is absent
    return out


def _stamped_topics(topics: list[TopicListItem]) -> list[TopicListItem]:
    """At most `_FRAME_TOPICS` published topics of a stamped sensor type, by type then name."""
    rank = {t: i for i, t in enumerate(_STAMPED_TYPES)}
    found = [
        t
        for t in topics
        if t.message_type in rank and t.publisher_count > 0 and not t.counts_unknown
    ]
    found.sort(key=lambda t: (rank[t.message_type], t.name))
    return found[:_FRAME_TOPICS]


def _frame_id_findings(ctx: _Ctx) -> list[Finding]:
    """An info per sampled sensor topic whose `header.frame_id` is not a frame of the TF tree.

    Optional and silent on purpose. It runs after every other check, on the budget they left,
    so it can never take any from them. It needs the topics check and a complete TF capture
    (not cut by the budget, not truncated), reads one message from at most three stamped
    topics, and stops without a trace when the budget is spent. Never above `info`: a missing
    frame is a lead, and a dynamic frame can simply be quiet during the capture.
    """
    report = ctx.tf_report
    if ctx.topics is None or report is None:
        return []
    known = {f.name for f in report.frames} | {f.parent for f in report.frames if f.parent}
    known = {name.lstrip("/") for name in known}
    out: list[Finding] = []
    for topic in _stamped_topics(ctx.topics):
        if ctx.left() < _FRAME_MIN_S:
            break
        try:
            result = ctx.inspector.sample_messages(
                topic.name,
                1,
                max_array_length=DEFAULT_MAX_ARRAY_LENGTH,  # the CLI default: no flag, strings whole
                arrays_summary_only=True,  # `--no-arr` leaves strings alone
                timeout_s=min(_FRAME_SAMPLE_S, ctx.left()),
            )
        except AdapterError:
            continue
        payload = result.samples[0].payload if result.samples else {}
        cut = payload.get(TRUNCATED_FIELDS_KEY)
        header = payload.get("header")
        frame = header.get("frame_id") if isinstance(header, dict) else None
        if isinstance(cut, list) and any(str(c).startswith("header.frame_id") for c in cut):
            continue  # a cut string is not a frame name
        if not isinstance(frame, str) or not frame or frame.lstrip("/") in known:
            continue
        root = report.roots[0] if report.roots else None
        safe = all(_SAFE_NAME.fullmatch(n) for n in (frame, root or ""))
        out.append(
            _finding(
                "info",
                "tf",
                topic.name,
                f"{topic.name} stamps its messages with frame {frame}, which is not among the "
                f"{len(known)} frames seen on /tf and /tf_static in the capture: no transform "
                "leads to it (or its publisher was quiet meanwhile).",
                _ev("sample_messages", "samples[0].payload.header.frame_id", frame),
                fixes=(
                    _read_fix(
                        f"ros2 run tf2_ros tf2_echo {root} {frame}",
                        f"the publisher of {frame}",
                        "Prints the transform once it exists; a frame nobody publishes "
                        "never appears. Observes only.",
                        "inspect_tf",
                    )
                    if root and safe
                    else []
                ),
            )
        )
    return out


_JOINT_STREAMS = {"fixed_joint_on_tf": "/tf", "movable_joint_on_tf_static": "/tf_static"}


def _tf_fix(kind: str, frames: list[str]) -> list[SuggestedFix]:
    """A read-only look at a stale edge (parent, child) or at who publishes a stream."""
    if kind in _JOINT_STREAMS:
        stream = _JOINT_STREAMS[kind]
        return _read_fix(
            f"ros2 topic info {stream} --verbose",
            f"the publishers of {stream}",
            "Lists the nodes publishing the stream the URDF joint type excludes. Observes only.",
            "inspect_tf",
        )
    if kind != "stale" or len(frames) != 2 or not all(_SAFE_NAME.fullmatch(f) for f in frames):
        return []
    return _read_fix(
        f"ros2 run tf2_ros tf2_echo {frames[0]} {frames[1]}",
        f"the publisher of {frames[0]}->{frames[1]}",
        "Prints the transform live; a stalled publisher shows as a stamp that stops "
        "advancing. Observes only.",
        "inspect_tf",
    )


def check_logs(ctx: _Ctx) -> list[Finding]:
    """One warning per node that logged an error or fatal line while `/rosout` was listened to."""
    # The live adapter listens for the timeout less its own stop reserve out of what is left.
    window = max(min(_LOG_TIMEOUT_S, ctx.left() - _LOG_STOP_RESERVE_S), 0.0)  # an upper bound
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
                (
                    f"No error-level line on /rosout in the {_LOG_TIMEOUT_S:g} s listened to."
                    if window >= _LOG_TIMEOUT_S
                    else "No error-level line on /rosout in up to "
                    f"{window:g} s listened to (the budget cut the window)."
                ),
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
                fixes=_read_fix(
                    "ros2 topic echo /rosout",
                    subject or "the logging node",
                    "Streams the logs of every node, each with its `name`; read the lines "
                    "around the error. Observes only.",
                    "diagnose",
                ),
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
    if ctx.nodes is None and ctx.node is None:
        raise AdapterError("no node list to read from (the nodes check did not run)")
    candidates = _candidates(ctx)
    out: list[Finding] = []
    untested: list[str] = []
    unreadable: list[tuple[str, str]] = []
    answered = 0
    for i, name in enumerate(candidates):
        named = name == ctx.node
        timeout = min(_NAMED_NODE_READ_S if named else _NODE_READ_S, ctx.left())
        if timeout < _NODE_READ_S:
            untested += candidates[i:]
            break
        try:
            info = ctx.inspector.get_node_info(name, timeout, ctx.params_file if named else None)
        except AdapterError as exc:
            unreadable.append((name, str(exc)))
            continue
        if named and ctx.params_file is not None:
            out += _param_findings(name, info)
        status = info.parameters_status
        if info.parameters is None and status == "no_budget":
            untested.append(name)
        elif info.parameters is None and status == "failed":
            unreadable.append((name, info.parameters_note or "parameter read failed"))
        elif info.parameters is None and status is None:  # no answer and no reason: unknown
            unreadable.append((name, info.parameters_note or "parameters not reported"))
        elif info.parameters is None and status == "timeout":
            answered += 1
            if named or not _answers_on_retry(ctx, name):
                out.append(_silent_finding(name, named, info.parameters_note))
        else:  # read, or no parameter services (nothing to ask): the node is not shown silent
            answered += 1
    if unreadable and not answered:
        raise AdapterError(f"no node could be read: {unreadable[0][0]}: {unreadable[0][1]}")
    if candidates and not answered:
        raise AdapterError("no node could be read: the call budget ran out before any answer")
    if unreadable:
        names = [n for n, _ in unreadable]
        out.append(
            _finding(
                "info",
                "responsiveness",
                None,
                f"{len(names)} node(s) could not be read, so they are not judged: "
                f"{_names(names)} ({unreadable[0][1][:120]}).",
                _ev("get_node_info", "error", unreadable[0][1]),
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


def _answers_on_retry(ctx: _Ctx, name: str) -> bool:
    """One more read of an unnamed node that timed out, if the budget allows it.

    A CLI start on a loaded host can eat most of the 3 s, and the warning then vanishes on the
    next sweep. A node the caller named has had 6 s already, and its finding is what they asked.
    """
    if ctx.left() < _NODE_READ_S:
        return False
    try:
        return ctx.inspector.get_node_info(name, _NODE_READ_S).parameters_status != "timeout"
    except AdapterError:
        return False


def _param_findings(name: str, info: NodeInfo) -> list[Finding]:
    """The named node's parameters against its `params_file`: a warning, and an info on gaps.

    Filed under `responsiveness`, the check that reads node parameters: a new check name
    would change the contract. Values are left out, and a secret-named parameter is never a
    difference (it is masked, so it cannot be compared) nor the target of a command.
    """
    entries = info.param_diff or []
    diff = [d for d in entries if d.status != "masked"]
    gaps = [info.param_diff_note] if info.param_diff_note else []
    if info.param_diff is None and not gaps:
        gaps.append("the node's parameters were not read")
    if len(entries) > len(diff):
        gaps.append(f"{len(entries) - len(diff)} secret-named parameter(s) not compared")
    out: list[Finding] = []
    if diff:
        shown = [f"{d.name} ({d.status})" for d in diff[:_MAX_PARAM_DIFFS]]
        more = len(diff) - len(shown)
        listed = ", ".join(shown) + (f" and {more} more" if more > 0 else "")
        safe = next((d.name for d in diff if _SAFE_NAME.fullmatch(d.name)), None)
        out.append(
            _finding(
                "warning",
                "responsiveness",
                name,
                f"{name} runs with {len(diff)} parameter(s) that differ from params_file: "
                f"{listed}.",
                _ev("get_node_info", "param_diff[].name", ", ".join(d.name for d in diff[:4])),
                fixes=(
                    _read_fix(
                        f"ros2 param get {name} {safe}",
                        name,
                        "Reads the live value to compare by eye with the file. Observes only.",
                        "get_node_info",
                    )
                    if safe
                    else []
                ),
            )
        )
    if gaps:
        joined = "; ".join(gaps)
        out.append(
            _finding(
                "info",
                "responsiveness",
                name,
                f"The comparison of {name} with params_file is incomplete: {joined[:200]}.",
                _ev("get_node_info", "param_diff_note", joined),
            )
        )
    return out


def _silent_finding(name: str, named: bool, note: str | None) -> Finding:
    """The finding for a node whose parameter services were announced but never answered.

    The wait actually granted (the budget can shorten it) is in the evidence note, not here.
    """
    evidence = _ev("get_node_info", "parameters_note", note or "null")
    if named:
        return _finding(
            "critical",
            "responsiveness",
            name,
            f"{name} did not answer a parameter read in time: its executor is probably blocked.",
            evidence,
            fixes=_silent_fix(name),
        )
    return _finding(
        "warning",
        "responsiveness",
        name,
        f"{name} did not answer a parameter read in time (blocked executor, or a loaded "
        "host): confirm with get_node_info(node, timeout_s=8).",
        evidence,
        fixes=_silent_fix(name),
    )


def _silent_fix(name: str) -> list[SuggestedFix]:
    return _read_fix(
        f"ros2 param list {name}",
        name,
        "A blocked node answers nothing here either; watch its CPU and log output as well.",
        "get_node_info",
    )


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
        wall_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        self._inspector = inspector
        self._health = health
        self._clock = clock
        self._wall_ns = wall_ns

    def diagnose(
        self,
        scope: str = "all",
        budget_s: float = DEFAULT_DIAGNOSE_BUDGET_S,
        node: str | None = None,
        params_file: str | None = None,
    ) -> Diagnosis:
        """The sweep. Raises `AdapterError` only on invalid input; a failing check is skipped.

        `params_file` (a parameter `.yaml`, with `node` only) is compared with the named node's
        live parameters; each difference is folded into one `responsiveness` warning.
        """
        scope, budget_s, node_name = _validate(scope, budget_s, node)
        params_path = _validate_params_file(params_file, node_name, scope)
        start = self._clock()
        left = budget.remaining()
        horizon = budget_s if left is None else min(budget_s, left - _RESERVE_S)
        plan = _SCOPE_CHECKS[scope]
        if scope != "all":
            plan = ("environment", *plan)
        ctx = _Ctx(
            self._inspector,
            self._health,
            node_name,
            params_path,
            self._clock,
            start + horizon,
            plan,
            wall_ns=self._wall_ns,
        )
        ran: list[str] = []
        findings: list[Finding] = []
        with budget.deadline_at(time.monotonic() + horizon):
            for name in plan:
                found = self._run_check(ctx, name)
                if found is not None:
                    ran.append(name)
                    findings += found
            findings += _frame_id_findings(ctx)  # last, on what the other checks left
        return _assemble(ctx, scope, budget_s, start, ran, findings)

    def _run_check(self, ctx: _Ctx, name: str) -> list[Finding] | None:
        """The findings of one check, or `None` after recording why it did not run."""
        reason = self._unavailable(ctx, name)
        if reason is None and name in _FLOOR_S and ctx.left() < _FLOOR_S[name]:
            reason = "budget"
        if reason is None:
            try:
                return _CHECK_FUNCTIONS[name](ctx)
            except _BudgetSkip:
                reason = "budget"
            except AdapterError as exc:
                reason = str(exc) or "adapter error"
        ctx.skipped.append(SkippedCheck(check=name, reason=reason))
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


def _validate_params_file(params_file: str | None, node: str | None, scope: str) -> str | None:
    if params_file is None:
        return None
    if node is None:
        raise AdapterError("params_file needs node: it is compared with that node's parameters")
    if "responsiveness" not in _SCOPE_CHECKS[scope]:
        raise AdapterError(f"params_file needs a scope that reads parameters, not {scope!r}")
    return validate_params_file(params_file)


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
    return f"Incomplete sweep: {verdict}" if skipped or scope != "all" else verdict


def _concerns_node(ctx: _Ctx, finding: Finding) -> bool:
    return ctx.node is not None and (finding.subject == ctx.node or id(finding) in ctx.named_ids)


def _cap(ctx: _Ctx, ordered: list[Finding]) -> list[Finding]:
    """The first MAX_FINDINGS, except that findings about the named node always make it in."""
    if len(ordered) <= MAX_FINDINGS:
        return ordered
    keep = [i for i, f in enumerate(ordered) if _concerns_node(ctx, f)][:MAX_FINDINGS]
    chosen = set(keep)
    for i in range(len(ordered)):
        if len(chosen) >= MAX_FINDINGS:
            break
        chosen.add(i)
    return [f for i, f in enumerate(ordered) if i in chosen]


def _assemble(
    ctx: _Ctx,
    scope: str,
    budget_s: float,
    start: float,
    ran: list[str],
    findings: list[Finding],
) -> Diagnosis:
    rank = {name: i for i, name in enumerate(SEVERITIES)}
    ordered = sorted(
        findings,
        key=lambda f: (
            rank[f.severity],
            not _concerns_node(ctx, f),  # the named node's findings go first
            f.check,
            f.subject or "",
        ),
    )
    counts = {s: sum(1 for f in ordered if f.severity == s) for s in SEVERITIES}
    if len(ordered) > MAX_FINDINGS:
        ctx.notes.append(
            f"Showing {MAX_FINDINGS} of {len(ordered)} findings; the counts cover all."
        )
    skipped_names = sorted({s.check for s in ctx.skipped} - set(ran), key=CHECKS.index)
    return Diagnosis(
        findings=_cap(ctx, ordered),
        findings_total=len(ordered),
        critical_count=counts["critical"],
        warning_count=counts["warning"],
        info_count=counts["info"],
        checks_run=ran,
        checks_skipped=ctx.skipped,
        budget_s=budget_s,
        elapsed_s=round(max(0.0, ctx.clock() - start), 3),
        verdict=_verdict(scope, (counts["critical"], counts["warning"]), len(ran), skipped_names),
        mode_effective=ctx.inspector.effective_mode,
        note=join_notes(*ctx.notes),
    )


__all__ = ["SCOPES", "DiagnoseService", "Scope"]
