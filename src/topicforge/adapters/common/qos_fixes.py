"""Suggested fixes for QoS mismatches and partition splits: pure text, binding-free.

TopicForge diagnoses, proposes a fix as text, a human applies it, TopicForge
verifies (`SuggestedFix.verify_with`). Nothing here executes anything, and no
suggestion contains a verb that stops, removes or changes a running system
(`tests/test_qos_fixes.py` pins the list). For each finding there are up to two
fixes, one per side that can change, with the tradeoff of each:

- a ROS 2 topic whose policies ROS can override (Reliability, Durability) gets a
  `qos_override` parameter file entry (works only if the node's code enabled the override
  for that policy);
- everything else gets a `dds_profile`: a Fast DDS XML profile plus the equivalent
  cyclonedds-python code (Cyclone DDS reads no QoS policy from its configuration file).

Identical findings (same topic, same compared values) share one set of fixes, carried
by the first report; the others stay empty and a hint says so.

Verified sources (checked 2026-10-14 against primary documents and source code):

- ROS 2 QoS overrides. Names are `qos_overrides.<topic>.<publisher|subscription>[_<id>].<policy>`
  and every such parameter is declared read-only, so the value is fixed at node start.
  Only the policies the node author listed are declared (rclcpp default set: history, depth,
  reliability; durability only if listed). Strings are `reliable`/`best_effort` and
  `volatile`/`transient_local`. A params file may nest the keys or write the dotted name flat
  (the dotted name is the parameter name). Durations (deadline, liveliness_lease_duration,
  lifespan) are integer nanoseconds and exist as overridable kinds, but this module does not
  emit them.
  https://design.ros2.org/articles/qos_configurability.html
  https://github.com/ros2/rclcpp/blob/jazzy/rclcpp/include/rclcpp/detail/qos_parameters.hpp
  https://github.com/ros2/rclcpp/blob/jazzy/rclcpp/src/rclcpp/qos_overriding_options.cpp
  https://github.com/ros2/rclpy/blob/jazzy/rclpy/rclpy/qos_overriding_options.py
  https://github.com/ros2/rmw/blob/jazzy/rmw/src/qos_string_conversions.c
- Fast DDS XML (2.6 for Humble, 2.14 for Jazzy): `<dds>` root with `<profiles>`, then
  `<data_writer>` / `<data_reader>` carrying `profile_name` and a `<qos>` child; kinds
  RELIABLE / BEST_EFFORT, VOLATILE / TRANSIENT_LOCAL, AUTOMATIC / MANUAL_BY_PARTICIPANT /
  MANUAL_BY_TOPIC, SHARED / EXCLUSIVE; `<deadline><period>`, `<latencyBudget><duration>`,
  `<liveliness><kind><lease_duration>`, `<partition><names><name>`; a duration is
  `<sec>`/`<nanosec>` and `<sec>DURATION_INFINITY</sec>` is accepted. The parser ignores the
  xmlns value; the official example uses the one below. Variable: FASTRTPS_DEFAULT_PROFILES_FILE.
  https://github.com/eProsima/Fast-DDS/blob/2.14.x/resources/xsd/fastRTPS_profiles.xsd
  https://github.com/eProsima/Fast-DDS/blob/2.6.x/src/cpp/rtps/xmlparser/XMLElementParser.cpp
  https://github.com/eProsima/Fast-DDS-docs/blob/2.14.x/code/XMLTesterExample.xml
- rmw_fastrtps: RMW_FASTRTPS_USE_QOS_FROM_XML=1 is documented for the publishing mode and history
  memory policy; ROS QoS values other than system default still override XML QoS; profiles
  are looked up by topic name.
  https://github.com/ros2/rmw_fastrtps/blob/jazzy/README.md
  https://github.com/ros2/rmw_fastrtps/blob/jazzy/rmw_fastrtps_shared_cpp/src/qos.cpp
- cyclonedds-python 11.0.1 (wheel read, not installed): `Qos(*policies)`,
  `Policy.Reliability.BestEffort`, `Policy.Reliability.Reliable(max_blocking_time=...)`,
  `Policy.Durability.TransientLocal`, `Policy.Deadline(deadline)`,
  `Policy.Liveliness.Automatic(lease_duration)`, `Policy.Ownership.Exclusive`,
  `Policy.LatencyBudget(budget)`, `Policy.Partition(partitions)`,
  `cyclonedds.util.duration(nanoseconds=..., infinite=True)`.
  https://github.com/eclipse-cyclonedds/cyclonedds-python (cyclonedds/qos.py, util.py)
  https://cyclonedds.io/docs/cyclonedds/latest/config/config_file_reference.html
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from topicforge.adapters.common.qos_analyzer import effective_partitions
from topicforge.models import (
    EndpointInfo,
    MismatchReport,
    NotMatchedPair,
    QosProfile,
    SuggestedFix,
)

Side = Literal["reader", "writer"]

SUPPORTED_POLICIES: tuple[str, ...] = (
    "Reliability",
    "Durability",
    "Deadline",
    "Liveliness",
    "Ownership",
    "LatencyBudget",
)
"""Policies a fix can be built for. Other findings (DestinationOrder, DataRepresentation,
History) yield no fix, only a hint."""

_ROS_OVERRIDABLE = {"Reliability", "Durability"}
_ROS_DURABILITY = {"VOLATILE": "volatile", "TRANSIENT_LOCAL": "transient_local"}
_LIVELINESS_ORDER = ("AUTOMATIC", "MANUAL_BY_PARTICIPANT", "MANUAL_BY_TOPIC")
_INF = float("inf")
_MAX_NAMED = 3
_NODE_PLACEHOLDER = "<node_name>"

_FAST_URL = "https://fast-dds.docs.eprosima.com/en/latest/fastdds/xml_configuration/"
_ROS_NOTE = (
    "Only works if the node's code enabled QoS overrides for this policy "
    "(rclcpp/rclpy QosOverridingOptions; the default policies are history, depth and "
    "reliability, not durability). "
    "The value is read once when the node creates the endpoint, so pass the file when the "
    "node starts: --ros-args --params-file <file>. If the node gave its endpoint an id, the "
    "key reads publisher_<id> or subscription_<id>."
)
_ROS_XML_NOTE = (
    " For a ROS 2 node this profile may have no effect: rmw_fastrtps looks profiles up by "
    "topic name, documents RMW_FASTRTPS_USE_QOS_FROM_XML=1 and FASTRTPS_DEFAULT_PROFILES_FILE "
    "only for the publishing mode and the history memory policy, and QoS values the node code gives "
    "explicitly still override the XML. rmw_cyclonedds takes QoS from the node's code, so "
    "change it there."
)


@dataclass(frozen=True)
class _Values:
    """Target QoS of the side that changes, one entry per fixed policy."""

    reliability: str | None = None
    durability: str | None = None
    deadline: tuple[int | None] | None = None
    liveliness: tuple[str, int | None] | None = None
    ownership: str | None = None
    latency_budget: tuple[int | None] | None = None


def _num(ns: int | None) -> float:
    return _INF if ns is None else float(ns)


def _targets(side: Side, policies: set[str], reader: QosProfile, writer: QosProfile) -> _Values:
    """Value the changing `side` must announce to satisfy the other side."""
    to_reader = side == "reader"
    other = writer if to_reader else reader
    mine = reader if to_reader else writer
    live: tuple[str, int | None] | None = None
    if "Liveliness" in policies and reader.liveliness_kind and writer.liveliness_kind:
        order = _LIVELINESS_ORDER.index
        kind_ok = order(writer.liveliness_kind) >= order(reader.liveliness_kind)
        lease_ok = _num(writer.liveliness_lease_ns) <= _num(reader.liveliness_lease_ns)
        if to_reader:
            kind = reader.liveliness_kind if kind_ok else writer.liveliness_kind
            lease = reader.liveliness_lease_ns if lease_ok else writer.liveliness_lease_ns
        else:
            kind = writer.liveliness_kind if kind_ok else reader.liveliness_kind
            lease = writer.liveliness_lease_ns if lease_ok else reader.liveliness_lease_ns
        live = (kind, lease)
    return _Values(
        reliability=("BEST_EFFORT" if to_reader else "RELIABLE")
        if "Reliability" in policies
        else None,
        durability=other.durability if "Durability" in policies else None,
        deadline=(other.deadline_ns,) if "Deadline" in policies else None,
        liveliness=live,
        ownership=(other.ownership_kind or mine.ownership_kind)
        if "Ownership" in policies
        else None,
        latency_budget=(other.latency_budget_ns,) if "LatencyBudget" in policies else None,
    )


# ---- rendering -----------------------------------------------------------------------


def _fast_duration(tag: str, ns: int | None) -> str:
    if ns is None:
        return f"<{tag}><sec>DURATION_INFINITY</sec></{tag}>"
    sec, nano = divmod(ns, 1_000_000_000)
    return f"<{tag}><sec>{sec}</sec><nanosec>{nano}</nanosec></{tag}>"


def _py_duration(ns: int | None) -> str:
    if ns is None:
        return "duration(infinite=True)"
    return f"duration(nanoseconds={ns})"


def _fast_qos(values: _Values, partitions: list[str] | None) -> list[str]:
    out: list[str] = []
    if values.reliability:
        out.append(f"<reliability><kind>{values.reliability}</kind></reliability>")
    if values.durability:
        out.append(f"<durability><kind>{values.durability}</kind></durability>")
    if values.deadline:
        out.append(f"<deadline>{_fast_duration('period', values.deadline[0])}</deadline>")
    if values.liveliness:
        kind, lease = values.liveliness
        out.append(
            f"<liveliness><kind>{kind}</kind>{_fast_duration('lease_duration', lease)}</liveliness>"
        )
    if values.ownership:
        out.append(f"<ownership><kind>{values.ownership}</kind></ownership>")
    if values.latency_budget:
        out.append(
            f"<latencyBudget>{_fast_duration('duration', values.latency_budget[0])}</latencyBudget>"
        )
    if partitions is not None:
        names = "".join(f"<name>{p}</name>" for p in partitions)
        out.append(f"<partition><names>{names}</names></partition>")
    return out


def _cyclone_qos(values: _Values, partitions: list[str] | None) -> list[str]:
    out: list[str] = []
    if values.reliability:
        out.append(
            "Policy.Reliability.BestEffort"
            if values.reliability == "BEST_EFFORT"
            else "Policy.Reliability.Reliable(max_blocking_time=duration(seconds=1))"
        )
    if values.durability:
        camel = "".join(w.capitalize() for w in values.durability.split("_"))
        out.append(f"Policy.Durability.{camel}")
    if values.deadline:
        out.append(f"Policy.Deadline({_py_duration(values.deadline[0])})")
    if values.liveliness:
        kind, lease = values.liveliness
        camel = "".join(w.capitalize() for w in kind.split("_"))
        out.append(f"Policy.Liveliness.{camel}({_py_duration(lease)})")
    if values.ownership:
        out.append(f"Policy.Ownership.{values.ownership.capitalize()}")
    if values.latency_budget:
        out.append(f"Policy.LatencyBudget({_py_duration(values.latency_budget[0])})")
    if partitions is not None:
        out.append(f"Policy.Partition({partitions!r})")
    return out


def _profile_text(side: Side, values: _Values, partitions: list[str] | None = None) -> str:
    """Fast DDS XML profile plus the cyclonedds-python equivalent."""
    kind = "data_reader" if side == "reader" else "data_writer"
    qos = "\n".join(f"        {line}" for line in _fast_qos(values, partitions))
    code = ",\n    ".join(_cyclone_qos(values, partitions))
    return (
        '<?xml version="1.0" encoding="UTF-8" ?>\n'
        f"<!-- Fast DDS profile, see {_FAST_URL} -->\n"
        '<dds xmlns="http://www.eprosima.com/XMLSchemas/fastRTPS_Profiles">\n'
        "  <profiles>\n"
        f'    <{kind} profile_name="topicforge_{side}_fix">\n'
        "      <qos>\n"
        f"{qos}\n"
        "      </qos>\n"
        f"    </{kind}>\n"
        "  </profiles>\n"
        "</dds>\n"
        "# Cyclone DDS takes QoS from the application, not from CYCLONEDDS_URI. cyclonedds-python:\n"
        "from cyclonedds.core import Policy, Qos\n"
        "from cyclonedds.util import duration\n"
        f"qos = Qos(\n    {code},\n)"
    )


def _ros_text(side: Side, ros_topic: str, nodes: list[str], values: _Values) -> str:
    where = "subscription" if side == "reader" else "publisher"
    lines: list[tuple[str, str]] = []
    if values.reliability:
        lines.append(("reliability", values.reliability.lower()))
    if values.durability:
        lines.append(("durability", _ROS_DURABILITY[values.durability]))
    blocks = []
    for node in nodes:
        entries = "\n".join(
            f"    qos_overrides.{ros_topic}.{where}.{key}: {val}" for key, val in lines
        )
        blocks.append(f"{node}:\n  ros__parameters:\n{entries}")
    return "\n".join(blocks)


# ---- tradeoffs -----------------------------------------------------------------------

_TRADEOFFS: dict[tuple[str, Side], str] = {
    ("Reliability", "reader"): (
        "The reader stops requesting retransmission, so it can miss samples."
    ),
    ("Reliability", "writer"): (
        "The writer keeps and resends unacknowledged samples: more bandwidth and memory, "
        "and a slow reader can add latency or stall a KEEP_ALL writer."
    ),
    ("Durability", "reader"): ("The reader no longer receives the history kept for late joiners."),
    ("Durability", "writer"): (
        "The writer keeps its history in memory for late joiners and holds it after readers leave."
    ),
    ("Deadline", "reader"): (
        "The reader no longer detects a writer that writes slower than its old deadline."
    ),
    ("Deadline", "writer"): (
        "The writer must really write at least that often, or deadline-missed events fire."
    ),
    ("Liveliness", "reader"): (
        "The reader accepts a weaker or slower liveliness, so a dead writer is noticed later."
    ),
    ("Liveliness", "writer"): (
        "A MANUAL kind obliges the application to assert liveliness; a shorter lease "
        "needs more frequent assertions."
    ),
    ("Ownership", "reader"): (
        "SHARED delivers every writer's samples; EXCLUSIVE only the strongest live writer's."
    ),
    ("Ownership", "writer"): (
        "Changing the kind changes who delivers when several writers exist on the topic."
    ),
    ("LatencyBudget", "reader"): ("The reader tolerates a larger delivery-delay hint."),
    ("LatencyBudget", "writer"): ("The writer hint becomes stricter; transports may batch less."),
}

_SIDE_ORDER: dict[str, tuple[Side, Side]] = {
    # Usual safer choice first. Reliability and Durability: keep the guarantee the reader
    # asked for. Deadline, Liveliness, Ownership, LatencyBudget: relax the reader, which
    # does not oblige any publisher code to change.
    "Reliability": ("writer", "reader"),
    "Durability": ("writer", "reader"),
}


def _tradeoff(side: Side, policies: list[str], first: bool) -> str:
    text = " ".join(_TRADEOFFS[(p, side)] for p in policies)
    other = "reader" if side == "writer" else "writer"
    if first:
        return f"{text} Preferred when you control the {side}; otherwise change the {other}."
    return f"{text} Alternative to changing the {other}."


# ---- grouping and names --------------------------------------------------------------


def _name(ep: EndpointInfo) -> str:
    n = ep.participant_name
    return n if n and n != "/" else f"participant {ep.participant_guid[:8]}"


def _distinct(eps: Sequence[EndpointInfo]) -> list[EndpointInfo]:
    seen: dict[str, EndpointInfo] = {}
    for ep in eps:
        seen.setdefault(ep.participant_guid, ep)
    return list(seen.values())


def _target(side: Side, eps: Sequence[EndpointInfo], topic: str) -> str:
    named = _distinct(eps)
    shown = ", ".join(_name(e) for e in named[:_MAX_NAMED])
    more = f" and {len(named) - _MAX_NAMED} more" if len(named) > _MAX_NAMED else ""
    plural = f" ({len(named)} {side}s with the same finding)" if len(named) > 1 else ""
    return f"{side} {shown}{more} on {topic}{plural}"


def _node_names(eps: Sequence[EndpointInfo]) -> list[str]:
    names = [e.participant_name for e in _distinct(eps)[:_MAX_NAMED]]
    known = [n if n.startswith("/") else f"/{n}" for n in names if n and n != "/"]
    return known or [_NODE_PLACEHOLDER]


def _verify(dds_topic: str) -> str:
    return f"detect_qos_mismatches(topic='{dds_topic}')"


def _order(policies: list[str]) -> tuple[Side, Side]:
    return _SIDE_ORDER.get(policies[0], ("reader", "writer"))


def _rxo_fixes(
    readers: Sequence[EndpointInfo], writers: Sequence[EndpointInfo], policies: list[str]
) -> list[SuggestedFix]:
    r0, w0 = readers[0], writers[0]
    assert r0.qos and w0.qos
    topic = r0.ros_topic or r0.dds_topic
    ros = r0.ros_topic is not None and r0.dds_topic.startswith("rt/")
    fixes: list[SuggestedFix] = []
    for index, side in enumerate(_order(policies)):
        eps = readers if side == "reader" else writers
        values = _targets(side, set(policies), r0.qos, w0.qos)
        tradeoff = _tradeoff(side, policies, index == 0)
        if ros and set(policies) <= _ROS_OVERRIDABLE and _ros_expressible(values):
            kind: Literal["qos_override", "dds_profile"] = "qos_override"
            text = _ros_text(side, topic, _node_names(eps), values)
            tradeoff = f"{tradeoff} {_ROS_NOTE}"
        else:
            kind = "dds_profile"
            text = _profile_text(side, values)
            if ros:
                tradeoff += _ROS_XML_NOTE
        fixes.append(
            SuggestedFix(
                kind=kind,
                target=_target(side, eps, topic),
                text=text,
                tradeoff=tradeoff,
                verify_with=_verify(r0.dds_topic),
            )
        )
    return fixes


def _ros_expressible(values: _Values) -> bool:
    return values.durability is None or values.durability in _ROS_DURABILITY


def _signature(report: MismatchReport) -> tuple[object, ...]:
    return (report.topic, tuple((d.policy, d.requested, d.offered) for d in report.details))


def attach_qos_fixes(
    entries: Sequence[tuple[MismatchReport, EndpointInfo, EndpointInfo]],
) -> tuple[list[MismatchReport], list[str]]:
    """Fill `suggested_fixes` on RxO reports, grouped by topic and compared values.

    `entries` holds each report with its (reader, writer). Returns the reports in the
    same order plus hints for what could not be fixed or was grouped.
    """
    groups: dict[tuple[object, ...], list[int]] = {}
    for i, (report, _, _) in enumerate(entries):
        groups.setdefault(_signature(report), []).append(i)
    out = [e[0] for e in entries]
    hints: list[str] = []
    for indices in groups.values():
        first = entries[indices[0]][0]
        policies = [d.policy for d in first.details if d.policy in SUPPORTED_POLICIES]
        skipped = sorted({d.policy for d in first.details} - set(policies))
        if skipped:
            hints.append(
                f"No suggested fix for {', '.join(skipped)} on {first.topic}: "
                "TopicForge proposes fixes for "
                f"{', '.join(SUPPORTED_POLICIES)} only."
            )
        if not policies or not (entries[indices[0]][1].qos and entries[indices[0]][2].qos):
            continue
        readers = [entries[i][1] for i in indices]
        writers = [entries[i][2] for i in indices]
        out[indices[0]] = first.model_copy(
            update={"suggested_fixes": _rxo_fixes(readers, writers, policies)}
        )
        if len(indices) > 1:
            hints.append(
                f"Suggested fixes for {first.topic} sit on its first report and also cover "
                f"the {len(indices) - 1} other report(s) with identical findings."
            )
    return out, hints


# ---- partitions ----------------------------------------------------------------------


def _is_wild(name: str) -> bool:
    return "*" in name or "?" in name


def _shown(parts: list[str]) -> str:
    return ", ".join(repr(p) if p else "'' (default partition)" for p in parts)


def _partition_fix(
    side: Side, eps: Sequence[EndpointInfo], other_parts: list[str], topic: str, dds_topic: str
) -> SuggestedFix | None:
    mine = effective_partitions(eps[0].qos.partitions if eps[0].qos else None)
    concrete = next((p for p in other_parts if not _is_wild(p)), None)
    if concrete is None:
        return None
    new_parts = [*mine, concrete] if concrete not in mine else mine
    other = "writer" if side == "reader" else "reader"
    effect = (
        f"Every {other} on the partition {_shown([concrete])} also reaches this {side}, "
        "and the partition no longer isolates this "
        f"{side}'s data from them."
    )
    return SuggestedFix(
        kind="dds_profile",
        target=_target(side, eps, topic) + f" (add partition {_shown([concrete])})",
        text=_profile_text(side, _Values(), new_parts),
        tradeoff=effect,
        verify_with=_verify(dds_topic),
    )


def attach_partition_fixes(
    entries: Sequence[tuple[NotMatchedPair, EndpointInfo, EndpointInfo]],
) -> tuple[list[NotMatchedPair], list[str]]:
    """Fill `suggested_fixes` on partition-separated pairs (`type_name` ones get none)."""
    groups: dict[tuple[object, ...], list[int]] = {}
    for i, (pair, r, w) in enumerate(entries):
        if pair.reason != "partition" or not (r.qos and w.qos):
            continue
        key = (
            pair.topic,
            tuple(effective_partitions(r.qos.partitions)),
            tuple(effective_partitions(w.qos.partitions)),
        )
        groups.setdefault(key, []).append(i)
    out = [e[0] for e in entries]
    hints: list[str] = []
    for indices in groups.values():
        r0, w0 = entries[indices[0]][1], entries[indices[0]][2]
        assert r0.qos and w0.qos
        topic = r0.ros_topic or r0.dds_topic
        readers = [entries[i][1] for i in indices]
        writers = [entries[i][2] for i in indices]
        fixes = [
            f
            for f in (
                _partition_fix(
                    "reader", readers, effective_partitions(w0.qos.partitions), topic, r0.dds_topic
                ),
                _partition_fix(
                    "writer", writers, effective_partitions(r0.qos.partitions), topic, r0.dds_topic
                ),
            )
            if f is not None
        ]
        if not fixes:
            hints.append(
                f"No suggested fix for the partition split on {topic}: both sides use "
                "wildcard partitions only, and two wildcards never match each other."
            )
            continue
        out[indices[0]] = entries[indices[0]][0].model_copy(update={"suggested_fixes": fixes})
        if len(indices) > 1:
            hints.append(
                f"Suggested fixes for the partition split on {topic} sit on its first "
                f"not-matched pair and also cover the {len(indices) - 1} other pair(s)."
            )
    return out, hints


__all__ = ["SUPPORTED_POLICIES", "attach_partition_fixes", "attach_qos_fixes"]
