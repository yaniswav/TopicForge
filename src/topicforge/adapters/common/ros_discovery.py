"""ROS 2 node discovery over DDS: binding-free.

Every ROS 2 participant publishes `rmw_dds_common/msg/ParticipantEntitiesInfo`
on the DDS topic `ros_discovery_info` (no `rt/` prefix): its own gid plus, for
each node it hosts, the namespace, the name and the gids of the node's readers
and writers. That is the only wire-level link between a DDS participant and the
ROS 2 node names it carries, so reading it turns "one anonymous participant" into
"these nodes".

This module decodes that message by hand (XCDR1, the encoding every ROS 2 RMW
uses for it) and caches the result per participant. It also holds the pure
identification of the short-lived participants TopicForge's own `ros2` CLI calls
leave behind. Nothing here imports a DDS binding.

Wire layout after the 4-byte encapsulation header (alignment is relative to
the byte after the header)::

    Gid gid;                          // octet[24] on Humble, octet[16] later
    sequence<NodeEntitiesInfo> nodes; // u32 count, then the elements

    NodeEntitiesInfo { string node_namespace; string node_name;
                       sequence<Gid> reader_gid_seq; sequence<Gid> writer_gid_seq; }
"""

from __future__ import annotations

import os
import struct
from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from topicforge.models import ParticipantInfo

ROS_DISCOVERY_TOPIC = "ros_discovery_info"
ROS_DISCOVERY_TYPE = "rmw_dds_common::msg::dds_::ParticipantEntitiesInfo_"

GID_WIDTHS = (24, 16)
"""Gid sizes in the wild: 24 bytes up to Humble, 16 bytes from Iron on."""

MAX_NODES_PER_PARTICIPANT = 1024
MAX_CACHED_PARTICIPANTS = 1024
_MAX_STRING = 4096
_MAX_GIDS = 65536
CLI_NODE_PREFIX = "_ros2cli_"
"""Node-name prefix of the `ros2` command line tool, followed by the process id."""

_HEADER_LEN = 4


class RosDiscoveryError(ValueError):
    """The bytes are not a `ParticipantEntitiesInfo` in any known layout."""


@dataclass(frozen=True)
class NodeEntities:
    """One node announced by a participant."""

    namespace: str
    name: str
    reader_gids: tuple[bytes, ...] = ()
    writer_gids: tuple[bytes, ...] = ()

    @property
    def full_name(self) -> str:
        """Fully qualified node name, e.g. `/demo/talker`."""
        return node_full_name(self.namespace, self.name)


@dataclass(frozen=True)
class ParticipantEntities:
    """Decoded `ParticipantEntitiesInfo`: the participant gid and its nodes."""

    gid: bytes
    gid_width: int
    nodes: tuple[NodeEntities, ...]

    @property
    def prefix_key(self) -> str:
        """The participant's 12-byte GUID prefix, as `prefix_key_of_bytes` renders it."""
        return prefix_key_of_bytes(self.gid)

    @property
    def node_names(self) -> list[str]:
        """Fully qualified node names, in announcement order."""
        return [n.full_name for n in self.nodes]


def node_full_name(namespace: str, name: str) -> str:
    """Join a node namespace and name: `("/", "a")` -> `/a`, `("/ns", "a")` -> `/ns/a`."""
    return f"{namespace.rstrip('/')}/{name}"


def prefix_key_of_bytes(gid: bytes) -> str:
    """First 12 bytes of a gid as three dotted 4-byte hex groups (a GUID prefix)."""
    data = gid[:12].ljust(12, b"\x00")
    return ".".join(data[i : i + 4].hex() for i in range(0, 12, 4))


def prefix_key_of_guid(guid: str) -> str:
    """The prefix key of a participant guid formatted `xxxxxxxx.xxxxxxxx.xxxxxxxx.xxxxxxxx`."""
    return ".".join(guid.lower().split(".")[:3])


# ----- decoder -----


class _Reader:
    """Cursor over an XCDR1 body; alignment is relative to its start (after the header)."""

    def __init__(self, body: bytes, little: bool) -> None:
        self._body = body
        self._little = little
        self.pos = 0

    def _align(self, n: int) -> None:
        self.pos += (-self.pos) % n

    def _take(self, n: int) -> bytes:
        end = self.pos + n
        if n < 0 or end > len(self._body):
            raise RosDiscoveryError("truncated ParticipantEntitiesInfo")
        chunk = self._body[self.pos : end]
        self.pos = end
        return chunk

    def u32(self) -> int:
        self._align(4)
        return int(struct.unpack("<I" if self._little else ">I", self._take(4))[0])

    def raw(self, n: int) -> bytes:
        return self._take(n)

    def string(self) -> str:
        length = self.u32()
        if length == 0 or length > _MAX_STRING:
            raise RosDiscoveryError(f"implausible string length {length}")
        data = self._take(length)
        if data[-1:] != b"\x00":
            raise RosDiscoveryError("string without terminator")
        try:
            return data[:-1].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RosDiscoveryError("string is not UTF-8") from exc

    def gids(self, width: int) -> tuple[bytes, ...]:
        count = self.u32()
        if count > _MAX_GIDS or count * width > len(self._body) - self.pos:
            raise RosDiscoveryError(f"implausible gid count {count}")
        return tuple(self.raw(width) for _ in range(count))

    def at_end(self) -> bool:
        # Writers may pad the payload to a multiple of 4 bytes, with any value.
        return len(self._body) - self.pos < 4


def _decode_body(body: bytes, little: bool, width: int) -> ParticipantEntities:
    reader = _Reader(body, little)
    gid = reader.raw(width)
    count = reader.u32()
    if count > MAX_NODES_PER_PARTICIPANT:
        raise RosDiscoveryError(f"implausible node count {count}")
    nodes = []
    for _ in range(count):
        namespace = reader.string()
        name = reader.string()
        readers = reader.gids(width)
        writers = reader.gids(width)
        if not namespace.startswith("/") or not name or "/" in name:
            raise RosDiscoveryError(f"implausible node {namespace!r} {name!r}")
        nodes.append(NodeEntities(namespace, name, readers, writers))
    if not reader.at_end():
        raise RosDiscoveryError("trailing bytes after the last node")
    return ParticipantEntities(gid=gid, gid_width=width, nodes=tuple(nodes))


def gid_width_order(distro: str | None = None) -> tuple[int, ...]:
    """Gid widths to try, the one matching `distro` (default `$ROS_DISTRO`) first.

    `humble` and older use 24 bytes, anything newer 16. An unknown or missing
    distro tries 24 first (the distro TopicForge is most often pointed at).
    """
    name = (os.environ.get("ROS_DISTRO", "") if distro is None else distro).lower()
    modern = {"iron", "jazzy", "kilted", "rolling"}
    return (16, 24) if name in modern else (24, 16)


def decode_participant_entities(data: bytes, distro: str | None = None) -> ParticipantEntities:
    """Decode a serialized `ParticipantEntitiesInfo` (with its 4-byte header).

    Tries each gid width (the `distro` hint first) and accepts the one that
    consumes the buffer exactly with plausible node names. Raises
    `RosDiscoveryError` when none does, or on a header that is not plain CDR.
    """
    data = bytes(data)
    if len(data) < _HEADER_LEN + 4:
        raise RosDiscoveryError("buffer shorter than a ParticipantEntitiesInfo")
    kind = data[1]
    if data[0] != 0 or kind not in (0, 1):
        raise RosDiscoveryError(f"unsupported encapsulation 0x{data[0]:02x}{data[1]:02x}")
    body = data[_HEADER_LEN:]
    first_error: RosDiscoveryError | None = None
    for width in gid_width_order(distro):
        try:
            return _decode_body(body, little=kind == 1, width=width)
        except RosDiscoveryError as exc:
            first_error = first_error or exc
    raise RosDiscoveryError(str(first_error))


def try_decode_participant_entities(
    data: bytes, distro: str | None = None
) -> ParticipantEntities | None:
    """Like `decode_participant_entities`, `None` instead of an error."""
    try:
        return decode_participant_entities(data, distro)
    except RosDiscoveryError:
        return None


# ----- cache -----


class NodeNameCache:
    """Latest node names per participant prefix key, bounded (oldest dropped first).

    A participant shutting down cleanly announces an empty node list last, which
    would erase who it was; `last_dict` keeps the last non-empty list for the
    participants that are gone.

    Not thread-safe by itself: the discovery tracker mutates it under its pass
    lock and readers copy it from `DiscoveryCaches.snapshot`.
    """

    def __init__(self, max_items: int = MAX_CACHED_PARTICIPANTS) -> None:
        self._items: OrderedDict[str, tuple[tuple[str, ...], tuple[str, ...]]] = OrderedDict()
        self._max = max_items

    def put(self, key: str, names: Iterable[str]) -> None:
        current = tuple(names)
        previous = self._items.pop(key, ((), ()))[1]
        self._items[key] = (current, current or previous)
        while len(self._items) > self._max:
            self._items.popitem(last=False)

    def remove(self, key: str) -> None:
        self._items.pop(key, None)

    def as_dict(self) -> dict[str, tuple[str, ...]]:
        """Latest names per prefix key (possibly empty)."""
        return {k: v[0] for k, v in self._items.items()}

    def last_dict(self) -> dict[str, tuple[str, ...]]:
        """Last non-empty names per prefix key."""
        return {k: v[1] for k, v in self._items.items() if v[1]}


def apply_info_samples(cache: NodeNameCache, raws: Iterable[bytes]) -> int:
    """Fold taken `ros_discovery_info` payloads into `cache`; returns how many decoded.

    Each message is the participant's complete node list, so the latest one
    replaces the earlier. Undecodable payloads are skipped.
    """
    decoded = 0
    for raw in raws:
        info = try_decode_participant_entities(raw)
        if info is None:
            continue
        cache.put(info.prefix_key, info.node_names)
        decoded += 1
    return decoded


def with_node_names(
    participants: Sequence[ParticipantInfo],
    names: Mapping[str, Sequence[str]],
    last: Mapping[str, Sequence[str]] | None = None,
) -> list[ParticipantInfo]:
    """Fill `node_names` / `node_names_source` for participants whose prefix is in `names`.

    A participant with a message but no nodes (a pure DDS participant that only
    hosts the ROS graph plumbing) keeps `none`. A participant that has `left`
    falls back to `last` (the nodes it hosted before its final empty message).
    """
    out = []
    for p in participants:
        key = prefix_key_of_guid(p.guid)
        found = names.get(key)
        if not found and p.status == "left" and last:
            found = last.get(key)
        if found:
            p = p.model_copy(
                update={"node_names": sorted(found), "node_names_source": "ros_discovery_info"}
            )
        out.append(p)
    return out


# ----- ghost participants of TopicForge's own ros2 CLI -----

CLI_GHOST_WINDOW_S = 30.0
"""How long a spawned CLI pid is remembered."""

CLI_CORRELATION_S = 5.0
"""A participant first seen this soon after a spawn is a probable ghost (note only)."""


def cli_pid_of_node(full_name: str) -> int | None:
    """Pid in a `/_ros2cli_<pid>` node name, `None` for any other name."""
    leaf = full_name.rsplit("/", 1)[-1]
    if not leaf.startswith(CLI_NODE_PREFIX):
        return None
    digits = leaf[len(CLI_NODE_PREFIX) :]
    return int(digits) if digits.isdigit() else None


def fast_pid_bits(guid: str) -> int | None:
    """Low 16 bits of the pid a Fast DDS GUID prefix carries (bytes 4-5), else `None`."""
    parts = guid.split(".")
    if len(parts) < 3:
        return None
    try:
        prefix = bytes.fromhex("".join(parts[:3]))
    except ValueError:
        return None
    if len(prefix) != 12 or prefix[:2] != b"\x01\x0f":
        return None
    return int.from_bytes(prefix[4:6], "little")


def _matches_pid(p: ParticipantInfo, pids: set[int], extra_pids: Mapping[str, int]) -> bool:
    for node in p.node_names:
        pid = cli_pid_of_node(node)
        if pid is not None and pid in pids:
            return True
    declared = extra_pids.get(p.guid)
    if declared is not None and declared in pids:
        return True
    if p.vendor == "fast":
        low = fast_pid_bits(p.guid)
        return low is not None and any(pid & 0xFFFF == low for pid in pids)
    return False


def flag_topicforge_cli(
    participants: Sequence[ParticipantInfo],
    spawns: Sequence[tuple[int, int]],
    *,
    now_ns: int,
    window_s: float = CLI_GHOST_WINDOW_S,
    extra_pids: Mapping[str, int] | None = None,
) -> list[ParticipantInfo]:
    """Set `is_topicforge_cli` on participants started by TopicForge's own `ros2` calls.

    `spawns` is the ring of `(pid, wall_ns)` of processes TopicForge started.
    A participant is flagged when its `_ros2cli_<pid>` node name, its Fast DDS
    GUID pid bits or a declared pid (`extra_pids`, guid -> pid, e.g. Cyclone's
    `__Pid` property) match a spawn younger than `window_s`. Never excludes.
    """
    horizon = now_ns - int(window_s * 1e9)
    pids = {pid for pid, ns in spawns if ns >= horizon}
    if not pids:
        return list(participants)
    declared = extra_pids or {}
    return [
        p.model_copy(update={"is_topicforge_cli": True})
        if not p.is_observer and _matches_pid(p, pids, declared)
        else p
        for p in participants
    ]


def probable_ghost_count(
    participants: Sequence[ParticipantInfo],
    spawns: Sequence[tuple[int, int]],
    *,
    now_ns: int,
    window_s: float = CLI_GHOST_WINDOW_S,
) -> int:
    """Unflagged participants first seen just after a spawn: candidates for the note only."""
    horizon = now_ns - int(window_s * 1e9)
    starts = [ns for _, ns in spawns if ns >= horizon]
    span = int(CLI_CORRELATION_S * 1e9)
    count = 0
    for p in participants:
        if p.is_topicforge_cli or p.is_observer or p.status != "active" or p.first_seen_ns is None:
            continue
        if any(0 <= p.first_seen_ns - s <= span for s in starts):
            count += 1
    return count


def cli_ghost_note(flagged: int, probable: int = 0) -> str | None:
    """Listing note explaining flagged (and probable) ghost participants, else `None`."""
    parts = []
    if flagged:
        parts.append(
            f"{flagged} participant(s) with `is_topicforge_cli` true are the `ros2` CLI "
            "processes TopicForge itself spawned (list_nodes, get_node_info, ...). "
            "They vanish within about 2 s; leave them out when counting the robot's participants."
        )
    if probable:
        parts.append(
            f"{probable} more participant(s) appeared within {int(CLI_CORRELATION_S)} s of a "
            "TopicForge CLI call and may be its ghosts (not identified)."
        )
    return " ".join(parts) or None
