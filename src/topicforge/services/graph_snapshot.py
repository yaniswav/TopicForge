"""Snapshots of the ROS 2 graph or of a bag, and the in-memory ring that holds them.

`graph_diff` takes one snapshot per call and compares it with an earlier one. A snapshot is
plain data built through the adapter protocol only (`list_nodes`, `list_topics`,
`list_endpoints`, `analyze_bag`, and `sample_messages` / `peek_bag_samples` for one
`/robot_description` message), so the module is tested without ROS 2. Nothing is written
to disk: the ring lives in the server process and a restart empties it.

The robot model. A snapshot holds `description_hash`, a short SHA-256 of the text of
`/robot_description`, so a changed model shows in the diff. A live snapshot reads that latched
topic once, only when the topic has a publisher, with the same bound as `inspect_tf` (6 s,
3 s of the call budget kept back, skipped under 2 s). A bag is read for its first message of
that topic. Any failure, or a bag without the topic, leaves the hash `None`: unknown, never a
change.
"""

from __future__ import annotations

import hashlib
import threading
from collections import deque
from dataclasses import dataclass, field, replace
from typing import Literal

from topicforge import budget as call_budget
from topicforge.adapters.base import AdapterError, MiddlewareAdapter
from topicforge.adapters.common.spawned_pids import recent_spawns

RING_SIZE = 16
"""Snapshots held; the oldest is dropped past this."""

RECENT_CLI_S = 5.0
"""A `ros2` CLI call younger than this when a snapshot starts may leave a lingering reader."""

QosKind = Literal["writer_qos", "latched"]
"""What `GraphSnapshot.qos` holds: live writers' `reliability/durability`, or a bag's latching."""

SnapshotSource = Literal["live", "bag"]

DESCRIPTION_TOPIC = "/robot_description"
_DESCRIPTION_READ_S = 6.0
_DESCRIPTION_RESERVE_S = 3.0
_DESCRIPTION_MIN_READ_S = 2.0


@dataclass(frozen=True)
class TopicState:
    """One topic inside a snapshot. A `None` field is not held by this kind of snapshot."""

    message_type: str
    publishers: int | None = None
    subscribers: int | None = None
    rate_hz: float | None = None


@dataclass(frozen=True)
class GraphSnapshot:
    """The graph (or bag) at one moment. `nodes` is `None` for a bag."""

    id: str
    source: SnapshotSource
    taken_ns: int
    topics: dict[str, TopicState]
    nodes: frozenset[str] | None = None
    path: str | None = None
    qos: dict[str, str] = field(default_factory=dict)
    qos_kind: QosKind | None = None
    qos_truncated: bool = False
    after_cli_call: bool = False
    real: bool = False
    """A bag read from a file (`analyze_bag` `reader` is not `fixture`); `False` for a graph."""
    description_hash: str | None = None
    """Short SHA-256 of the `/robot_description` text; `None` when unknown."""


class SnapshotRing:
    """Thread-safe ring of the last `RING_SIZE` snapshots, with ids `snap-N` that never repeat."""

    def __init__(self, size: int = RING_SIZE) -> None:
        self._items: deque[GraphSnapshot] = deque(maxlen=size)
        self._next = 1
        self._lock = threading.Lock()

    def store(self, snapshot: GraphSnapshot) -> GraphSnapshot:
        """Give `snapshot` the next id, keep it (dropping the oldest when full) and return it."""
        with self._lock:
            stored = replace(snapshot, id=f"snap-{self._next}")
            self._next += 1
            self._items.append(stored)
            return stored

    def seed(self, snapshot: GraphSnapshot) -> None:
        """Keep `snapshot` under its own id, without using up a `snap-N` number."""
        with self._lock:
            self._items.append(snapshot)

    def latest(self) -> GraphSnapshot | None:
        with self._lock:
            return self._items[-1] if self._items else None

    def get(self, snapshot_id: str) -> GraphSnapshot | None:
        with self._lock:
            return next((s for s in self._items if s.id == snapshot_id), None)

    def ids(self) -> list[str]:
        """Held ids, oldest first."""
        with self._lock:
            return [s.id for s in self._items]


def build_live_snapshot(adapter: MiddlewareAdapter, now_ns: int) -> GraphSnapshot:
    """Snapshot the live (or mock) graph: nodes, topics with counts, writer QoS when known.

    Runs the same `ros2` calls as `list_nodes` and `list_topics`. The QoS comes from the
    discovery cache (`list_endpoints`) and is left out when no DDS backend is active.
    """
    after_cli = any((now_ns - ns) / 1e9 < RECENT_CLI_S for _, ns in recent_spawns())
    nodes = adapter.list_nodes()
    topics = {
        t.name: TopicState(t.message_type, t.publisher_count, t.subscriber_count)
        for t in adapter.list_topics()
    }
    qos, truncated = _writer_qos(adapter)
    return GraphSnapshot(
        id="",
        source="live",
        taken_ns=now_ns,
        topics=topics,
        nodes=frozenset(n.full_name for n in nodes.nodes),
        qos=qos if qos is not None else {},
        qos_kind="writer_qos" if qos is not None else None,
        qos_truncated=truncated,
        after_cli_call=after_cli,
        description_hash=_live_description_hash(adapter, topics),
    )


def build_bag_snapshot(adapter: MiddlewareAdapter, path: str, now_ns: int) -> GraphSnapshot:
    """Snapshot a bag: its topics, types, rates and latching. Bag-internal topics are left out."""
    analysis = adapter.analyze_bag(path)
    topics = {
        t.name: TopicState(t.message_type, rate_hz=t.frequency_hz)
        for t in analysis.topics
        if t.kind != "rosbag2_internal"
    }
    latched = {
        t.name: "transient_local" if t.latched else "volatile"
        for t in analysis.topics
        if t.kind != "rosbag2_internal" and t.latched is not None
    }
    return GraphSnapshot(
        id="",
        source="bag",
        taken_ns=now_ns,
        topics=topics,
        path=path,
        qos=latched,
        qos_kind="latched" if latched else None,
        real=analysis.reader != "fixture",
        description_hash=_bag_description_hash(adapter, path, topics),
    )


def _text_hash(result: object) -> str | None:
    """Short SHA-256 of the `data` of the first sample of a `SampleResult`, `None` without one."""
    samples = getattr(result, "samples", None)
    text = samples[0].payload.get("data") if samples else None
    if not isinstance(text, str) or not text.strip():
        return None
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:12]


def _live_description_hash(adapter: MiddlewareAdapter, topics: dict[str, TopicState]) -> str | None:
    state = topics.get(DESCRIPTION_TOPIC)
    if state is None or state.publishers == 0:
        return None
    wait = call_budget.clamp(_DESCRIPTION_READ_S, _DESCRIPTION_RESERVE_S)
    if wait < _DESCRIPTION_MIN_READ_S:
        return None
    try:
        result = adapter.sample_messages(
            DESCRIPTION_TOPIC, 1, max_array_length=None, timeout_s=wait
        )
    except AdapterError:
        return None
    return _text_hash(result)


def _bag_description_hash(
    adapter: MiddlewareAdapter, path: str, topics: dict[str, TopicState]
) -> str | None:
    if DESCRIPTION_TOPIC not in topics:
        return None
    try:
        return _text_hash(adapter.peek_bag_samples(path, DESCRIPTION_TOPIC, 1))
    except AdapterError:
        return None


def _writer_qos(adapter: MiddlewareAdapter) -> tuple[dict[str, str] | None, bool]:
    """`({topic: "reliability/durability"}, truncated)`; `(None, False)` without a DDS backend.

    Reads the endpoint cache, never the binding. A topic with writers of several profiles
    lists them all, sorted and comma-separated.
    """
    hook = getattr(adapter, "graph_writer_qos", None)
    if callable(hook):
        return dict(hook()), False
    try:
        listing = adapter.list_endpoints(include_internal=False)
    except AdapterError:
        return None, False
    profiles: dict[str, set[str]] = {}
    for e in listing.endpoints:
        if e.role == "writer" and e.ros_topic is not None and e.qos is not None:
            token = f"{e.qos.reliability.lower()}/{e.qos.durability.lower()}"
            profiles.setdefault(e.ros_topic, set()).add(token)
    return {topic: ", ".join(sorted(p)) for topic, p in profiles.items()}, listing.truncated
