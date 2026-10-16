"""Build the `inspect_tf` report from a raw capture of `/tf_static` and `/tf`.

Pure functions, no `ros2` and no binding: the live adapter feeds them what it
decoded, the mock adapter feeds them fixtures, so both go through the same
analysis. The tree is read from the transforms themselves, never from the node
graph: a frame exists because a transform names it.

Time. Every age is measured on the transforms' own header stamps against the
newest `/tf` stamp of the capture, so it is correct on sim time and needs no
wall clock. A frame is stale when its newest transform trails that reference by
more than `stale_after_s`. The wall arrival time gives the second rate
(`observed_frequency_hz`) and, against the stamps, the real-time factor.
"""

from __future__ import annotations

import statistics
from collections import defaultdict, deque
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Literal

from topicforge.models import TfCapture, TfFinding, TfFrame, TfReport, TfSample

_NS = 1_000_000_000
# A real-time factor further than this from 1 is reported as sim time or a slow host.
_RTF_TOLERANCE = 0.15

Kind = Literal["static", "dynamic", "both"]


@dataclass
class _Edge:
    """All the transforms seen for one (parent, child) pair."""

    parent: str
    child: str
    static: list[TfSample] = field(default_factory=list)
    dynamic: list[TfSample] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.static) + len(self.dynamic)

    @property
    def last_stamp_ns(self) -> int:
        return max(s.stamp_ns for s in (*self.static, *self.dynamic))

    @property
    def last_dynamic_ns(self) -> int | None:
        return max((s.stamp_ns for s in self.dynamic), default=None)


def build_tf_report(
    capture: TfCapture,
    stale_after_s: float,
    source_frame: str | None,
    target_frame: str | None,
    *,
    window_s: float | None = None,
    mode_effective: Literal["mock", "live"] = "live",
) -> TfReport:
    """Analyse `capture` into the report of `inspect_tf`.

    `source_frame` and `target_frame` are both given or both `None`; when given, the
    report carries the chain of frames between them. `window_s` is the requested
    listening time (the capture's own `window_actual_s` when omitted).
    """
    edges = _group_edges(capture)
    reference_ns = max((s.stamp_ns for s in capture.dynamic), default=None)
    parents_of: dict[str, list[_Edge]] = defaultdict(list)
    for edge in edges.values():
        parents_of[edge.child].append(edge)
    chosen = {child: _latest(group) for child, group in parents_of.items()}

    names = {e.parent for e in edges.values()} | set(parents_of)
    roots = sorted(names - set(parents_of))
    frames = [
        _frame(n, chosen.get(n), parents_of.get(n, []), reference_ns, stale_after_s)
        for n in sorted(names)
    ]
    findings = [
        *_stale_findings(frames, stale_after_s),
        *_two_parent_findings(parents_of),
        *_static_dynamic_findings(parents_of),
        *_disconnected_findings(roots),
        *_cycle_findings(chosen),
    ]
    path, path_note, path_findings = _path(edges, roots, source_frame, target_frame, frames)
    findings += path_findings

    stamp_span_s, rtf = _clock_figures(edges, capture)
    truncated = capture.dropped_count > 0 or capture.capped
    return TfReport(
        frames=frames,
        roots=roots,
        frame_count=len(frames),
        static_count=sum(1 for f in frames if f.kind in ("static", "both")),
        dynamic_count=sum(1 for f in frames if f.kind in ("dynamic", "both")),
        stale_count=sum(1 for f in frames if f.stale),
        transform_count=len(edges),
        message_count=len(capture.static) + len(capture.dynamic),
        findings=findings,
        hints=_hints(rtf, findings, bool(capture.dynamic)),
        path=path,
        path_note=path_note,
        reference_ns=reference_ns,
        window_s=capture.window_actual_s if window_s is None else window_s,
        window_actual_s=capture.window_actual_s,
        stamp_span_s=stamp_span_s,
        real_time_factor=rtf,
        truncated=truncated,
        mode_effective=mode_effective,
        note=_note(capture, bool(frames)),
    )


def _norm(name: str) -> str:
    """Frame names are compared without a leading slash, as tf2 does."""
    return name.lstrip("/")


def _group_edges(capture: TfCapture) -> dict[tuple[str, str], _Edge]:
    edges: dict[tuple[str, str], _Edge] = {}
    for samples, is_static in ((capture.static, True), (capture.dynamic, False)):
        for s in samples:
            key = (_norm(s.parent), _norm(s.child))
            edge = edges.setdefault(key, _Edge(*key))
            (edge.static if is_static else edge.dynamic).append(s)
    return edges


def _latest(group: list[_Edge]) -> _Edge:
    """The edge seen last (ties: dynamic first, then parent name)."""
    return max(group, key=lambda e: (e.last_stamp_ns, bool(e.dynamic), e.parent))


def _kind(group: list[_Edge]) -> Kind | None:
    if not group:
        return None
    has_static = any(e.static for e in group)
    has_dynamic = any(e.dynamic for e in group)
    if has_static and has_dynamic:
        return "both"
    return "static" if has_static else "dynamic"


def _rate(samples: list[TfSample], attr: str) -> float | None:
    if len(samples) < 2:
        return None
    values = [getattr(s, attr) for s in samples]
    span = (max(values) - min(values)) / _NS
    return round((len(samples) - 1) / span, 3) if span > 0 else None


def _frame(
    name: str,
    edge: _Edge | None,
    group: list[_Edge],
    reference_ns: int | None,
    stale_after_s: float,
) -> TfFrame:
    if edge is None:
        return TfFrame(
            name=name,
            parent=None,
            kind=None,
            message_count=0,
            last_stamp_ns=None,
            age_s=None,
            stale=False,
            observed_frequency_hz=None,
            sim_frequency_hz=None,
        )
    last_dynamic = edge.last_dynamic_ns
    age = None
    if last_dynamic is not None and reference_ns is not None:
        age = round(max(reference_ns - last_dynamic, 0) / _NS, 3)
    return TfFrame(
        name=name,
        parent=edge.parent,
        kind=_kind(group),
        message_count=edge.count,
        last_stamp_ns=edge.last_stamp_ns,
        age_s=age,
        stale=age is not None and age > stale_after_s,
        observed_frequency_hz=_rate(edge.dynamic, "received_ns"),
        sim_frequency_hz=_rate(edge.dynamic, "stamp_ns"),
    )


def _stale_findings(frames: list[TfFrame], stale_after_s: float) -> list[TfFinding]:
    return [
        TfFinding(
            kind="stale",
            frames=[f.parent or "", f.name],
            text=(
                f"{f.parent} -> {f.name} was last published {f.age_s:g} s before the newest "
                f"transform (limit {stale_after_s:g} s): its publisher stopped or is blocked."
            ),
        )
        for f in frames
        if f.stale
    ]


def _two_parent_findings(parents_of: dict[str, list[_Edge]]) -> list[TfFinding]:
    out = []
    for child in sorted(parents_of):
        parents = sorted({e.parent for e in parents_of[child]})
        if len(parents) > 1:
            out.append(
                TfFinding(
                    kind="two_parents",
                    frames=[child, *parents],
                    text=(
                        f"Frame {child} is published with {len(parents)} parents "
                        f"({', '.join(parents)}): a frame has one parent, so lookups through "
                        "it are unreliable. Two publishers or a duplicate launch."
                    ),
                )
            )
    return out


def _static_dynamic_findings(parents_of: dict[str, list[_Edge]]) -> list[TfFinding]:
    out = []
    for child in sorted(parents_of):
        group = parents_of[child]
        if any(e.static for e in group) and any(e.dynamic for e in group):
            out.append(
                TfFinding(
                    kind="static_and_dynamic",
                    frames=[child],
                    text=(
                        f"Frame {child} is published on both /tf_static and /tf: the same "
                        "transform has a static and a moving source."
                    ),
                )
            )
    return out


def _disconnected_findings(roots: list[str]) -> list[TfFinding]:
    if len(roots) < 2:
        return []
    return [
        TfFinding(
            kind="disconnected",
            frames=roots,
            text=(
                f"The tree has {len(roots)} roots ({', '.join(roots)}): these frames are not "
                "connected, so no transform exists between their subtrees."
            ),
        )
    ]


def _cycle_findings(chosen: dict[str, _Edge]) -> list[TfFinding]:
    seen: set[frozenset[str]] = set()
    out = []
    for start in sorted(chosen):
        walk: list[str] = []
        node: str | None = start
        while node is not None and node not in walk and node in chosen:
            walk.append(node)
            node = chosen[node].parent
        if node is None or node not in walk:
            continue
        loop = walk[walk.index(node) :][::-1]  # parent to child, like the edges
        key = frozenset(loop)
        if key in seen:
            continue
        seen.add(key)
        ordered = loop[loop.index(min(loop)) :] + loop[: loop.index(min(loop))]
        out.append(
            TfFinding(
                kind="cycle",
                frames=ordered,
                text=f"Frames {' -> '.join([*ordered, ordered[0]])} are each other's parent.",
            )
        )
    return out


def _path(
    edges: dict[tuple[str, str], _Edge],
    roots: list[str],
    source: str | None,
    target: str | None,
    frames: list[TfFrame],
) -> tuple[list[str] | None, str | None, list[TfFinding]]:
    if source is None or target is None:
        return None, None, []
    src, dst = _norm(source), _norm(target)
    known = {f.name for f in frames}
    missing = [n for n in (src, dst) if n not in known]
    if missing:
        return None, f"Unknown frame(s): {', '.join(missing)}.", [_no_path(src, dst, "unknown")]
    chain = _bfs(edges, src, dst)
    if chain is None:
        why = f"in separate trees (roots {', '.join(roots)})" if len(roots) > 1 else "unconnected"
        return None, f"No chain of transforms joins {src} and {dst}.", [_no_path(src, dst, why)]
    stale = {f.name for f in frames if f.stale}
    crossed = [n for a, b in pairwise(chain) for n in (a, b) if n in stale]
    note = f"{max(len(chain) - 1, 0)} transform(s)."
    if crossed:
        note += f" The chain crosses stale frame(s): {', '.join(sorted(set(crossed)))}."
    return chain, note, []


def _no_path(src: str, dst: str, why: str) -> TfFinding:
    text = (
        f"No frame named {src} or {dst} was seen."
        if why == "unknown"
        else f"No path from {src} to {dst}: they are {why}."
    )
    return TfFinding(kind="no_path", frames=[src, dst], text=text)


def _bfs(edges: dict[tuple[str, str], _Edge], src: str, dst: str) -> list[str] | None:
    """Shortest chain of frames over the transforms, read in either direction."""
    adjacent: dict[str, set[str]] = defaultdict(set)
    for parent, child in edges:
        adjacent[parent].add(child)
        adjacent[child].add(parent)
    previous: dict[str, str | None] = {src: None}
    queue = deque([src])
    while queue:
        node = queue.popleft()
        if node == dst:
            break
        for nxt in sorted(adjacent[node]):
            if nxt not in previous:
                previous[nxt] = node
                queue.append(nxt)
    if dst not in previous:
        return None
    chain = []
    cursor: str | None = dst
    while cursor is not None:
        chain.append(cursor)
        cursor = previous[cursor]
    return chain[::-1]


def _clock_figures(
    edges: dict[tuple[str, str], _Edge], capture: TfCapture
) -> tuple[float | None, float | None]:
    """`(stamp_span_s, real_time_factor)` of the dynamic transforms."""
    if not capture.dynamic:
        return None, None
    stamps = [s.stamp_ns for s in capture.dynamic]
    span = round((max(stamps) - min(stamps)) / _NS, 3)
    ratios = []
    for edge in edges.values():
        if len(edge.dynamic) < 2:
            continue
        stamp = max(s.stamp_ns for s in edge.dynamic) - min(s.stamp_ns for s in edge.dynamic)
        wall = max(s.received_ns for s in edge.dynamic) - min(s.received_ns for s in edge.dynamic)
        if stamp > 0 and wall > 0:
            ratios.append(stamp / wall)
    rtf = round(statistics.median(ratios), 3) if ratios else None
    return (span if span > 0 else None), rtf


def _hints(rtf: float | None, findings: list[TfFinding], has_dynamic: bool) -> list[str]:
    hints = []
    if rtf is not None and abs(rtf - 1.0) > _RTF_TOLERANCE:
        hints.append(
            f"Header stamps advance {rtf:g}x as fast as the wall clock (sim time or a loaded "
            "host): `age_s` and `stale_after_s` are in stamp time, `sim_frequency_hz` is the "
            "rate in stamp time and `observed_frequency_hz` the rate on arrival."
        )
    if any(f.kind == "stale" for f in findings):
        hints.append(
            "A stale frame stopped being published (or the sim clock stopped): check its "
            "publisher with `get_topic_info` on /tf."
        )
    if not has_dynamic:
        hints.append("No /tf message arrived: only static frames are known, so no age is given.")
    return hints


def _note(capture: TfCapture, has_frames: bool) -> str | None:
    parts = list(capture.notes)
    if not has_frames:
        parts.append("No transform was received on /tf_static or /tf: there is no tree to read.")
    if capture.capped:
        parts.append("The message cap ended the capture, so later transforms are missing.")
    if capture.dropped_count:
        parts.append(f"{capture.dropped_count} message(s) were not decoded.")
    return " ".join(parts) or None


__all__ = ["build_tf_report"]
