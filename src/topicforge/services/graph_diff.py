"""`graph_diff`: compare two snapshots of the ROS 2 graph (or of a bag) and say what changed.

`diff_snapshots` is pure: it takes two `GraphSnapshot` values and returns the changes and what
could not be compared. `GraphDiffService` takes the snapshot, keeps it in the ring and builds
the `GraphDiff` envelope. Only comparisons both snapshots can support are made: nodes live
against live, publisher and subscriber counts live against live, rates bag against bag, QoS
when both sides hold the same kind of it (docs/CONTRACT.md section 9.14).
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Literal

from topicforge.adapters.base import AdapterError, MiddlewareAdapter
from topicforge.adapters.common.endpoints import MAX_LISTED_ENDPOINTS
from topicforge.models.graph_diff import (
    GRAPH_CHANGE_KINDS,
    GraphChange,
    GraphDiff,
    GraphSnapshotRef,
)
from topicforge.services.graph_snapshot import (
    GraphSnapshot,
    SnapshotRing,
    build_bag_snapshot,
    build_live_snapshot,
)

RATE_TOLERANCE = 0.2
"""A bag rate counts as changed past this relative difference."""

BUILTIN_COUNT_TOPICS = frozenset({"/rosout", "/parameter_events"})
"""Topics whose endpoint counts move with every node and CLI call: counted, never reported."""

_SNAPSHOT_ID_RE = re.compile(r"^snap-\d+$")
_CLI_LINGER_NOTE = " A TopicForge `ros2` CLI reader may linger 2 s: read again before trusting it."

BaselineKind = Literal["previous", "id", "bag"]


@dataclass
class DiffResult:
    """Output of `diff_snapshots`."""

    changes: list[GraphChange] = field(default_factory=list)
    builtin_changes_count: int = 0
    not_compared: dict[str, str] = field(default_factory=dict)


def parse_baseline(text: str) -> tuple[BaselineKind, str]:
    """Classify a `baseline` string: `previous`, a snapshot id (`snap-N`), or a bag path."""
    clean = text.strip()
    if not clean:
        raise AdapterError("baseline must be `previous`, a snapshot id (`snap-N`) or a bag path")
    if clean == "previous":
        return "previous", clean
    if _SNAPSHOT_ID_RE.match(clean):
        return "id", clean
    return "bag", clean


def diff_snapshots(baseline: GraphSnapshot, current: GraphSnapshot) -> DiffResult:
    """Compare `current` with `baseline`; `added` means present now and absent from the baseline."""
    result = DiffResult()
    _diff_nodes(baseline, current, result)
    _diff_topics(baseline, current, result)
    _diff_types(baseline, current, result)
    _diff_qos(baseline, current, result)
    _diff_rates(baseline, current, result)
    _diff_counts(baseline, current, result)
    result.changes.sort(key=lambda c: (GRAPH_CHANGE_KINDS.index(c.kind), c.name, c.field or ""))
    return result


def _diff_nodes(base: GraphSnapshot, cur: GraphSnapshot, out: DiffResult) -> None:
    if base.nodes is None or cur.nodes is None:
        out.not_compared["nodes"] = "a bag holds no node graph"
        return
    for name in sorted(cur.nodes - base.nodes):
        out.changes.append(_added("node", name, f"Node {name} appeared."))
    for name in sorted(base.nodes - cur.nodes):
        out.changes.append(_removed("node", name, f"Node {name} is gone."))


def _diff_topics(base: GraphSnapshot, cur: GraphSnapshot, out: DiffResult) -> None:
    for name in sorted(cur.topics.keys() - base.topics.keys()):
        kind = cur.topics[name].message_type
        out.changes.append(_added("topic", name, f"Topic {name} ({kind}) appeared.", after=kind))
    for name in sorted(base.topics.keys() - cur.topics.keys()):
        kind = base.topics[name].message_type
        out.changes.append(_removed("topic", name, f"Topic {name} ({kind}) is gone.", before=kind))


def _diff_types(base: GraphSnapshot, cur: GraphSnapshot, out: DiffResult) -> None:
    for name in sorted(base.topics.keys() & cur.topics.keys()):
        before, after = base.topics[name].message_type, cur.topics[name].message_type
        if before != after:
            text = f"Topic {name} changed type from {before} to {after}."
            out.changes.append(_changed("type", name, "message_type", before, after, text))


def _diff_qos(base: GraphSnapshot, cur: GraphSnapshot, out: DiffResult) -> None:
    if base.qos_kind is None or base.qos_kind != cur.qos_kind:
        out.not_compared["qos"] = _qos_reason(base, cur)
        return
    kind = base.qos_kind
    for name in sorted(base.topics.keys() & cur.topics.keys()):
        before, after = base.qos.get(name), cur.qos.get(name)
        if before is not None and after is not None and before != after:
            what = "writer QoS" if kind == "writer_qos" else "latching"
            text = f"Topic {name} {what} changed from {before} to {after}."
            out.changes.append(_changed("qos", name, kind, before, after, text))


def _qos_reason(base: GraphSnapshot, cur: GraphSnapshot) -> str:
    if base.source == "live" and cur.source == "live":
        return "no DDS backend is active: writer QoS is not read (see `health_check`)"
    if base.source == "bag" and cur.source == "bag":
        return "a bag holds no QoS information"
    return "a bag holds only whether a topic was latched, a live graph holds writer QoS"


def _diff_rates(base: GraphSnapshot, cur: GraphSnapshot, out: DiffResult) -> None:
    if base.source != "bag" or cur.source != "bag":
        out.not_compared["rates"] = (
            "rates are compared bag against bag only: a live graph has no rate without sampling"
        )
        return
    for name in sorted(base.topics.keys() & cur.topics.keys()):
        before, after = base.topics[name].rate_hz, cur.topics[name].rate_hz
        if before is None or after is None or not _rate_changed(before, after):
            continue
        text = f"Topic {name} rate changed from {before:.4g} Hz to {after:.4g} Hz."
        out.changes.append(
            _changed("rate", name, "frequency_hz", f"{before:.4g}", f"{after:.4g}", text)
        )


def _rate_changed(before: float, after: float) -> bool:
    if before == 0:
        return after != 0
    return abs(after - before) / before > RATE_TOLERANCE


def _diff_counts(base: GraphSnapshot, cur: GraphSnapshot, out: DiffResult) -> None:
    if base.source != "live" or cur.source != "live":
        out.not_compared["counts"] = "a bag holds no publisher or subscriber counts"
        return
    for name in sorted(base.topics.keys() & cur.topics.keys()):
        for label, before, after in (
            ("publisher_count", base.topics[name].publishers, cur.topics[name].publishers),
            ("subscriber_count", base.topics[name].subscribers, cur.topics[name].subscribers),
        ):
            if before is None or after is None or before == after:
                continue
            if name in BUILTIN_COUNT_TOPICS:
                out.builtin_changes_count += 1
                continue
            text = f"Topic {name} {label} went from {before} to {after}."
            if label == "subscriber_count" and cur.after_cli_call:
                text += _CLI_LINGER_NOTE
            out.changes.append(_changed("count", name, label, str(before), str(after), text))


def _added(kind: str, name: str, text: str, after: str | None = None) -> GraphChange:
    return GraphChange(kind=kind, change="added", name=name, after=after, text=text)  # type: ignore[arg-type]


def _removed(kind: str, name: str, text: str, before: str | None = None) -> GraphChange:
    return GraphChange(kind=kind, change="removed", name=name, before=before, text=text)  # type: ignore[arg-type]


def _changed(kind: str, name: str, field_: str, before: str, after: str, text: str) -> GraphChange:
    return GraphChange(
        kind=kind,  # type: ignore[arg-type]
        change="changed",
        name=name,
        field=field_,
        before=before,
        after=after,
        text=text,
    )


class GraphDiffService:
    """Takes a snapshot per call, keeps it in the ring and compares it with a baseline."""

    def __init__(
        self,
        adapter: MiddlewareAdapter,
        clock_ns: Callable[[], int],
        ring: SnapshotRing | None = None,
    ) -> None:
        self._adapter = adapter
        self._clock_ns = clock_ns
        self._ring = ring or SnapshotRing()
        self._seed_lock = threading.Lock()
        # Only the mock adapter offers a seed (`graph_baseline_seed`, outside the protocol).
        self._seed = getattr(adapter, "graph_baseline_seed", None)
        self._seeded = self._seed is None

    def diff(self, baseline: str, bag_path: str | None) -> GraphDiff:
        """Snapshot the graph (or `bag_path`), store it and compare it with `baseline`.

        `baseline` is `previous`, a snapshot id or a bag path. It is resolved before the new
        snapshot is taken, so a bad value stores nothing.
        """
        kind, value = parse_baseline(baseline)
        self._seed_once()
        base = self._resolve(kind, value)
        current = self._ring.store(self._take(bag_path))
        if base is None:
            return self._envelope(current, None, DiffResult(), _first_note(current.id))
        result = diff_snapshots(base, current)
        return self._envelope(current, base, result, _notes(base, current))

    def _resolve(self, kind: BaselineKind, value: str) -> GraphSnapshot | None:
        if kind == "previous":
            return self._ring.latest()
        if kind == "id":
            found = self._ring.get(value)
            if found is None:
                held = ", ".join(self._ring.ids()) or "none yet"
                raise AdapterError(f"unknown snapshot id {value!r}; held snapshots: {held}")
            return found
        return replace(build_bag_snapshot(self._adapter, value, self._clock_ns()), id=value)

    def _take(self, bag_path: str | None) -> GraphSnapshot:
        if bag_path is not None:
            return build_bag_snapshot(self._adapter, bag_path, self._clock_ns())
        return build_live_snapshot(self._adapter, self._clock_ns())

    def _seed_once(self) -> None:
        """Seed the mock baseline on the first call (the constructor stays free of adapter calls)."""
        with self._seed_lock:
            if not self._seeded and self._seed is not None:
                self._seed_mock_baseline(self._seed())
            self._seeded = True

    def _seed_mock_baseline(self, seed: dict[str, str]) -> None:
        """Store `snap-0`: the mock graph minus a node, a topic and a QoS (never a frozen copy)."""
        now = self._clock_ns()
        current = build_live_snapshot(self._adapter, now)
        topics = {n: s for n, s in current.topics.items() if n != seed["missing_topic"]}
        qos = {n: q for n, q in current.qos.items() if n in topics}
        qos[seed["qos_topic"]] = seed["qos"]
        nodes = (current.nodes or frozenset()) - {seed["missing_node"]}
        self._ring.seed(
            GraphSnapshot(
                id="snap-0",
                source="live",
                taken_ns=now,
                topics=topics,
                nodes=nodes,
                qos=qos,
                qos_kind=current.qos_kind,
            )
        )

    def _envelope(
        self,
        current: GraphSnapshot,
        base: GraphSnapshot | None,
        result: DiffResult,
        note: str | None,
    ) -> GraphDiff:
        changes = result.changes
        return GraphDiff(
            snapshot_id=current.id,
            baseline_id=base.id if base else None,
            current=_ref(current),
            baseline=_ref(base) if base else None,
            changes=changes,
            added_count=sum(c.change == "added" for c in changes),
            removed_count=sum(c.change == "removed" for c in changes),
            changed_count=sum(c.change == "changed" for c in changes),
            not_compared=result.not_compared,
            builtin_changes_count=result.builtin_changes_count,
            snapshot_ids=self._ring.ids(),
            mode_effective=self._adapter.effective_mode,
            note=note,
        )


def _ref(snap: GraphSnapshot) -> GraphSnapshotRef:
    return GraphSnapshotRef(
        id=snap.id,
        source=snap.source,
        taken_ns=snap.taken_ns,
        path=snap.path,
        node_count=None if snap.nodes is None else len(snap.nodes),
        topic_count=len(snap.topics),
        has_qos=snap.qos_kind is not None,
        has_rates=snap.source == "bag",
    )


def _first_note(snapshot_id: str) -> str:
    return f"No baseline yet: snapshot {snapshot_id} stored; call again to compare with it."


def _notes(base: GraphSnapshot, cur: GraphSnapshot) -> str | None:
    parts: list[str] = []
    if base.id == "snap-0":
        parts.append(
            "snap-0 is the mock robot before /imu_driver, /imu/data and a /scan QoS change."
        )
    if base.source == "bag" and cur.source == "live":
        parts.append(
            "A bag holds only the recorded topics: added means present live and absent "
            "from the bag."
        )
    elif base.source == "live" and cur.source == "bag":
        parts.append(
            "A bag holds only the recorded topics: removed means present live and absent "
            "from the bag."
        )
    if cur.qos_truncated or base.qos_truncated:
        parts.append(f"Writer QoS covers the first {MAX_LISTED_ENDPOINTS} endpoints only.")
    return " ".join(parts) or None
