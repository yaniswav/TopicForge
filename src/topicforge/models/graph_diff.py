"""Result model of `graph_diff`: what changed between two snapshots of the ROS 2 graph.

A snapshot is the live graph (nodes, topics, types, endpoint counts, writer QoS when a DDS
backend is up) or a recorded bag (topics, types, rates, latching). The diff lists what was
added, removed or changed from the baseline to the current snapshot, and names what could
not be compared instead of staying silent about it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_CONFIG = ConfigDict(extra="forbid", frozen=True)

GraphChangeKind = Literal["node", "topic", "type", "qos", "rate", "count"]
"""What a change is about; also the sort order of `GraphDiff.changes`."""

GRAPH_CHANGE_KINDS: tuple[str, ...] = ("node", "topic", "type", "qos", "rate", "count")


class GraphSnapshotRef(BaseModel):
    """One side of the comparison."""

    model_config = _CONFIG

    id: str = Field(description="`snap-N` or the bag path.")
    source: Literal["live", "bag"] = Field()
    taken_ns: int = Field()
    path: str | None = Field(default=None, description="`null` for live.")
    node_count: int | None = Field(default=None, description="`null` for a bag.")
    topic_count: int = Field(ge=0)
    has_qos: bool = Field(description="Writer QoS or latching held.")
    has_rates: bool = Field()


class GraphChange(BaseModel):
    """One difference, from baseline to current."""

    model_config = _CONFIG

    kind: GraphChangeKind = Field()
    change: Literal["added", "removed", "changed"] = Field()
    name: str = Field()
    field: str | None = Field(
        default=None, description="Property that changed; `null` if added or removed."
    )
    before: str | None = Field(default=None, description="Baseline value, if any.")
    after: str | None = Field(default=None, description="Current value, if any.")
    text: str = Field()


class GraphDiff(BaseModel):
    """Envelope returned by `graph_diff`."""

    model_config = _CONFIG

    snapshot_id: str = Field(description="Stored snapshot id.")
    baseline_id: str | None = Field(default=None, description="`null` without baseline (`note`).")
    current: GraphSnapshotRef = Field()
    baseline: GraphSnapshotRef | None = Field(default=None, description="`null` without baseline.")
    changes: list[GraphChange] = Field()
    added_count: int = Field(ge=0)
    removed_count: int = Field(ge=0)
    changed_count: int = Field(ge=0)
    not_compared: dict[str, str] = Field(description="Skipped comparison and why.")
    builtin_changes_count: int = Field(
        ge=0, description="Count changes on `/rosout`, `/parameter_events`: not in `changes`."
    )
    snapshot_ids: list[str] = Field(description="Held ids, oldest first.")
    mode_effective: Literal["mock", "live"] = Field(description="`live` or `mock` (fixtures).")
    note: str | None = Field(default=None, description="One sentence of context, or `None`.")
