"""Decoding of live ROS 2 samples read from DDS (`TOPICFORGE_DDS_DECODE=on`): binding-free.

Decides whether a topic may be decoded at all, builds the IDL type tree from a `rosbags`
definition through constructors the binding module supplies, and turns raw CDR into the
payload shape `peek_bag_samples` already returns. The binding side is
`adapters/dds_cyclone/user_read.py`.

The rule at every step is fail-closed: a value is shown only when the definition is known to
be the writer's. `plan_decode` raises `DecodeRefused` with the reason, and `decode_cdr` falls
back to raw bytes. Three guards, in order:

1. the writer's `typehash=RIHS01_...` from USER_DATA (Iron and later) selects the `rosbags`
   store with that exact definition; a hash no store has is a refusal;
2. the CDR encapsulation must be plain CDR (`00 00` or `00 01`);
3. the message re-serialized from the decoded fields must be the bytes received, except at the
   type's own alignment gaps, which Fast DDS leaves uninitialized, and with at most the padding
   to the next 4-byte boundary at the end. This catches a cut or corrupt sample, a type of
   another size and, without a hash, most wrong definitions.

Without a hash (Humble) a definition of the same layout cannot be told from the real one: an
int32 reads as a float32, and a wider field reads as a narrow one when its other bytes fall in
a gap. `DecodePlan.store_note` says so, in the result's `note` and in each sample's
`_decode_note`.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from rosbags.interfaces import Nodetype  # type: ignore[import-not-found]

from topicforge.adapters.common.bag_read import _cap_arrays
from topicforge.adapters.common.cdr_decoder import decode_dynamic_sample, stringify_non_finite
from topicforge.adapters.common.dds_introspection import cyclone_extract_type_name
from topicforge.adapters.common.qos_normalize import cyclone_qos_to_profile
from topicforge.adapters.common.stamps import payload_stamp
from topicforge.adapters.common.summaries import needs_whole_arrays, summarize_message
from topicforge.adapters.common.xtypes import annotate_raw
from topicforge.models import MessageSample, TopicMetrics

ROS_TOPIC_PREFIX = "rt/"

HEAVY_TYPES = frozenset(
    {
        "sensor_msgs/msg/Image",
        "sensor_msgs/msg/CompressedImage",
        "sensor_msgs/msg/PointCloud",
        "sensor_msgs/msg/PointCloud2",
        "stereo_msgs/msg/DisparityImage",
        "nav_msgs/msg/OccupancyGrid",
        "map_msgs/msg/OccupancyGridUpdate",
        "nav_msgs/msg/GridCells",
        "nav_msgs/msg/Path",
        "shape_msgs/msg/Mesh",
        "visualization_msgs/msg/ImageMarker",
        "visualization_msgs/msg/InteractiveMarkerInit",
        "visualization_msgs/msg/InteractiveMarkerUpdate",
        "visualization_msgs/msg/Marker",
        "visualization_msgs/msg/MarkerArray",
    }
)
"""Refused: a 6.2 MB `Image` showed gaps, 239-265 MB of RSS and +40% writer CPU. A type not listed
is stopped at its first sample over `MAX_RAW_SAMPLE_BYTES`."""

PEEK_READ_S = 5.0
"""Longest read of one `peek_dds_samples`, discovery of the writer included."""

METRICS_READ_S = 10.0
"""Longest read of one `topic_metrics`; `window_s` shortens it."""

METRICS_MAX_SAMPLES = 1000
"""A `topic_metrics` read stops at this many samples."""

MAX_RAW_SAMPLE_BYTES = 1024 * 1024
"""A larger serialized sample ends the read and is counted, never decoded."""

MAX_DECODED_RESPONSE_BYTES = 256 * 1024
"""Decoded samples returned by one peek, as serialized JSON."""

_MAX_ARRAY_READ = 4096
_PLAIN_CDR = (b"\x00\x00", b"\x00\x01")
_DDS_TYPE = re.compile(r"^(\w+)::(\w+)::dds_::(\w+)_$")
_TYPEHASH = re.compile(rb"typehash=([^;]*)")
_PRIMITIVE_SIZES = {
    "bool": 1,
    "byte": 1,
    "char": 1,
    "int8": 1,
    "uint8": 1,
    "int16": 2,
    "uint16": 2,
    "int32": 4,
    "uint32": 4,
    "float32": 4,
    "int64": 8,
    "uint64": 8,
    "float64": 8,
}
_HASHED_STORES = ("ROS2_JAZZY", "ROS2_KILTED", "ROS2_LYRICAL", "ROS2_IRON")


class DecodeRefused(Exception):
    """This topic is not decoded; the message says why, for the result's `note`."""


@dataclass(frozen=True)
class WriterFacts:
    """What decoding needs from one discovered writer."""

    type_name: str | None
    typehash: str | None = None
    reliable: bool = False
    transient_local: bool = False


@dataclass(frozen=True)
class DecodePlan:
    """A topic cleared for decoding: the exact DDS type and the definitions to read it with."""

    dds_topic: str
    dds_type: str
    ros_type: str
    store: Any
    store_label: str
    store_note: str
    latched: bool


@dataclass(frozen=True)
class RawRead:
    """One serialized sample: `raw` is empty when oversize or when only the rate is wanted.

    `arrival_ns` is the reader's monotonic clock at the slice that took it, `received_ns` the
    wall clock there, `source_ns` the writer's wall clock.
    """

    raw: bytes
    source_ns: int
    received_ns: int
    size: int
    arrival_ns: int = 0


@dataclass(frozen=True)
class ReadOutcome:
    """A bounded read: the samples and what the reader saw of the writers."""

    samples: list[RawRead] = field(default_factory=list)
    matched: int = 0
    incompatible: int = 0
    elapsed_s: float = 0.0


@dataclass(frozen=True)
class IdlBuilders:
    """The binding's type constructors, so the tree builder runs without the binding."""

    base: Mapping[str, Any]
    string: Any
    bounded_string: Callable[[int], Any]
    array: Callable[[Any, int], Any]
    sequence: Callable[[Any, int], Any]
    struct: Callable[[str, str, dict[str, Any]], Any]


def typehash_from_user_data(user_data: bytes | None) -> str | None:
    """The value of `typehash=` in a ROS 2 endpoint's USER_DATA, `None` when absent."""
    found = _TYPEHASH.search(user_data or b"")
    return found.group(1).decode("latin-1") if found else None


def _user_data_of(sample: Any) -> bytes | None:
    """USER_DATA bytes of a Cyclone discovery sample, `None` when it has none."""
    try:
        for policy in getattr(sample, "qos", None) or ():
            if type(policy).__name__.rsplit(".", 1)[-1] == "Userdata":
                return bytes(policy.data)
    except Exception:
        return None
    return None


def writer_facts(sample: Any) -> WriterFacts:
    """`WriterFacts` of a raw DCPSPublication sample; an unreadable QoS reads as volatile."""
    profile = cyclone_qos_to_profile(sample)
    return WriterFacts(
        type_name=cyclone_extract_type_name(sample),
        typehash=typehash_from_user_data(_user_data_of(sample)),
        reliable=profile is not None and profile.reliability == "RELIABLE",
        transient_local=profile is not None and profile.durability == "TRANSIENT_LOCAL",
    )


def ros_type_of(dds_type: str) -> str | None:
    """`pkg/msg/Name` of `pkg::msg::dds_::Name_`, `None` for any other shape."""
    found = _DDS_TYPE.match(dds_type)
    return f"{found[1]}/{found[2]}/{found[3]}" if found else None


def dds_type_of(ros_type: str) -> str:
    """`pkg::msg::dds_::Name_` of `pkg/msg/Name`."""
    package, kind, name = ros_type.split("/")
    return f"{package}::{kind}::dds_::{name}_"


@lru_cache(maxsize=8)
def _typestore(name: str) -> Any:
    from rosbags.typesys import Stores, get_typestore  # type: ignore[import-not-found]

    return get_typestore(Stores[name])


def _store_label(name: str) -> str:
    return "ROS 2 " + name.removeprefix("ROS2_").capitalize()


def _store_with_hash(ros_type: str, typehash: str) -> tuple[Any, str]:
    """The store whose definition of `ros_type` has exactly this hash, or a refusal."""
    known = False
    for name in _HASHED_STORES:
        store = _typestore(name)
        if ros_type not in store.fielddefs:
            continue
        known = True
        if store.hash_rihs01(ros_type) == typehash:
            return store, _store_label(name)
    if known:
        raise DecodeRefused(
            f"the writer's type hash differs from every known definition of {ros_type}: "
            "a custom or changed type"
        )
    raise DecodeRefused(f"{ros_type} is not in the rosbags type store (custom type)")


def _store_without_hash(ros_type: str, distro: str | None) -> tuple[Any, str, str]:
    """The store for a writer that sends no hash: `distro` when it names one, else Humble."""
    name = f"ROS2_{distro.upper()}" if distro else "ROS2_HUMBLE"
    try:
        store = _typestore(name)
    except KeyError:
        name, store = "ROS2_HUMBLE", _typestore("ROS2_HUMBLE")
    if ros_type not in store.fielddefs:
        raise DecodeRefused(f"{ros_type} is not in the rosbags type store (custom type)")
    label = _store_label(name)
    return (
        store,
        label,
        (
            f"Definitions assumed from {label}: the writer sends no type hash, so another type of "
            "the same layout reads as this one (an int32 as a float32)."
        ),
    )


def plan_decode(
    dds_topic: str, writers: Iterable[WriterFacts], *, distro: str | None = None
) -> DecodePlan:
    """Clear `dds_topic` for decoding from its discovered writers, or raise `DecodeRefused`.

    A reader is only ever created from this plan, so never for a topic without a writer or for
    a type no writer announced. `distro` (default `ROS_DISTRO`) is only used without a hash.
    """
    if not dds_topic.startswith(ROS_TOPIC_PREFIX):
        raise DecodeRefused(f"{dds_topic} is not a ROS 2 topic (`{ROS_TOPIC_PREFIX}` prefix)")
    found = list(writers)
    if not found:
        raise DecodeRefused("no writer is discovered on this topic")
    types = {w.type_name for w in found}
    if len(types) != 1 or None in types:
        raise DecodeRefused(f"its writers disagree on the type: {sorted(map(str, types))}")
    dds_type = next(iter(types))
    ros_type = ros_type_of(dds_type) if dds_type else None
    if dds_type is None or ros_type is None:
        raise DecodeRefused(f"{dds_type} is not a ROS 2 type name")
    if ros_type in HEAVY_TYPES:
        raise DecodeRefused(f"{ros_type} is too heavy to read here; use `sample_messages`")
    hashes = {w.typehash for w in found}
    if len(hashes) != 1:
        raise DecodeRefused("its writers announce different type hashes")
    typehash = next(iter(hashes))
    if typehash is None:
        hint = os.environ.get("ROS_DISTRO") if distro is None else distro
        store, label, note = _store_without_hash(ros_type, hint)
    else:
        store, label = _store_with_hash(ros_type, typehash)
        note = ""
    return DecodePlan(
        dds_topic=dds_topic,
        dds_type=dds_type,
        ros_type=ros_type,
        store=store,
        store_label=label,
        store_note=note,
        latched=all(w.reliable and w.transient_local for w in found),
    )


def build_idl_type(store: Any, ros_type: str, builders: IdlBuilders, cache: dict[Any, Any]) -> Any:
    """IDL struct of `ros_type` from the store's definition, with the ROS 2 DDS type names.

    `cache` is keyed by (store, type). A field the builders have no constructor for is a refusal.
    """
    key = (id(store), ros_type)
    if key not in cache:
        _, fields = store.fielddefs[ros_type]
        members = {name: _idl_field(store, desc, builders, cache) for name, desc in fields}
        cache[key] = builders.struct(ros_type.split("/")[-1] + "_", dds_type_of(ros_type), members)
    return cache[key]


def _idl_field(store: Any, desc: Any, builders: IdlBuilders, cache: dict[Any, Any]) -> Any:
    kind, rest = desc
    if kind == Nodetype.BASE:
        name, bound = rest
        if name == "string":
            return builders.bounded_string(bound) if bound else builders.string
        if name not in builders.base:
            raise DecodeRefused(f"field type {name} is not supported")
        return builders.base[name]
    if kind == Nodetype.NAME:
        return build_idl_type(store, rest, builders, cache)
    inner, size = rest
    element = _idl_field(store, inner, builders, cache)
    if kind == Nodetype.ARRAY:
        return builders.array(element, size)
    return builders.sequence(element, size)


def alignment_gaps(store: Any, ros_type: str, message: Any) -> tuple[list[tuple[int, int]], int]:
    """CDR alignment gaps of `message` as `(start, end)` after the header, and where it ends."""
    gaps: list[tuple[int, int]] = []

    def align(pos: int, size: int) -> int:
        end = (pos + size - 1) & -size
        if end > pos:
            gaps.append((pos, end))
        return end

    def walk_many(desc: Any, values: Any, count: int, pos: int) -> int:
        kind, rest = desc
        if kind == Nodetype.BASE and rest[0] != "string":
            size = _PRIMITIVE_SIZES[rest[0]]
            return align(pos, size) + count * size if count else pos
        for value in values:
            pos = walk(desc, value, pos)
        return pos

    def walk(desc: Any, value: Any, pos: int) -> int:
        kind, rest = desc
        if kind == Nodetype.BASE:
            if rest[0] == "string":
                return align(pos, 4) + 4 + len(value.encode("utf-8")) + 1
            size = _PRIMITIVE_SIZES[rest[0]]
            return align(pos, size) + size
        if kind == Nodetype.NAME:
            for name, field_desc in store.fielddefs[rest][1]:
                pos = walk(field_desc, getattr(value, name), pos)
            return pos
        inner, _ = rest
        if kind == Nodetype.ARRAY:
            return walk_many(inner, value, len(value), pos)
        return walk_many(inner, value, len(value), align(pos, 4) + 4)

    end = walk((Nodetype.NAME, ros_type), message, 0)
    return gaps, end


def _same_message(plan: DecodePlan, message: Any, raw: bytes, again: bytes) -> bool:
    """Whether `again`, re-serialized from `raw`, says what `raw` says (guard 3 above)."""
    body, expected = raw[4:], again[4:]
    size = len(expected)
    if not size <= len(body) <= (size + 3) & -4:
        return False
    gaps, end = alignment_gaps(plan.store, plan.ros_type, message)
    if end != size:
        return False
    start = 0
    for gap_start, gap_end in gaps:
        if body[start:gap_start] != expected[start:gap_start]:
            return False
        start = gap_end
    return body[start:size] == expected[start:size]


def decode_cdr(plan: DecodePlan, raw: bytes, *, whole_arrays: bool = False) -> dict[str, Any]:
    """Payload of one serialized sample; raw bytes and a `_decode_note` at any doubt."""
    if raw[:2] not in _PLAIN_CDR:
        return annotate_raw(raw, note=f"unsupported CDR encapsulation {raw[:2].hex()}")
    try:
        message = plan.store.deserialize_cdr(raw, plan.ros_type)
        again = plan.store.serialize_cdr(message, plan.ros_type, little_endian=raw[1] == 1)
        same = _same_message(plan, message, raw, bytes(again))
    except Exception as exc:
        return annotate_raw(raw, note=f"does not decode as {plan.ros_type}: {exc}")
    if not same:
        return annotate_raw(raw, note=f"{len(raw)} bytes do not read back as {plan.ros_type}")
    limit = None if whole_arrays else _MAX_ARRAY_READ
    return decode_dynamic_sample(message, max_array_elements=limit)


def message_samples(topic: str, plan: DecodePlan, reads: Iterable[RawRead]) -> list[MessageSample]:
    """`MessageSample`s of the reads, in the shape `peek_bag_samples` returns."""
    out: list[MessageSample] = []
    whole = needs_whole_arrays(plan.ros_type)
    for read in reads:
        decoded = decode_cdr(plan, read.raw, whole_arrays=whole)
        cut: set[str] = set()
        payload = _cap_arrays(decoded, cut)
        if cut:
            payload["_truncated_fields"] = sorted(cut)
        found = payload_stamp(decoded)
        full = decoded.get("_decode_status") == "full"
        if full and plan.store_note:
            payload["_decode_note"] = plan.store_note
        summary = summarize_message(plan.ros_type, decoded, set() if whole else cut)
        out.append(
            MessageSample(
                topic=topic,
                message_type=plan.ros_type,
                timestamp_ns=read.source_ns if found is None else found[0],
                stamp_source="dds_source" if found is None else found[1],
                received_ns=read.received_ns,
                payload=stringify_non_finite(payload),
                summary=summary if full else None,
            )
        )
    return out


def cap_decoded(
    samples: list[MessageSample], max_bytes: int = MAX_DECODED_RESPONSE_BYTES
) -> tuple[list[MessageSample], str | None]:
    """The samples that fit `max_bytes` of JSON, and a note when some did not."""
    kept: list[MessageSample] = []
    total = 0
    for sample in samples:
        total += len(sample.model_dump_json().encode("utf-8"))
        if total > max_bytes:
            break
        kept.append(sample)
    dropped = len(samples) - len(kept)
    if not dropped:
        return kept, None
    return kept, f"{dropped} decoded sample(s) dropped: the {max_bytes} byte response cap."


def fitting_reads(outcome: ReadOutcome) -> tuple[list[RawRead], str | None]:
    """Reads under `MAX_RAW_SAMPLE_BYTES`, and a note for the larger ones."""
    kept = [r for r in outcome.samples if r.size <= MAX_RAW_SAMPLE_BYTES]
    dropped = len(outcome.samples) - len(kept)
    if not dropped:
        return kept, None
    return kept, f"{dropped} sample(s) over {MAX_RAW_SAMPLE_BYTES} bytes not decoded."


def read_note(outcome: ReadOutcome, wanted: int) -> str | None:
    """Why a read returned fewer samples than `wanted`, `None` when it did not."""
    got = len(outcome.samples)
    if got >= wanted:
        return None
    took = f"{outcome.elapsed_s:.1f} s"
    if outcome.incompatible and not outcome.matched:
        return f"0 of {wanted} samples: the reader's QoS is incompatible with the writer's."
    if not outcome.matched and not got:
        return f"0 of {wanted} samples: no writer matched the reader in {took}."
    return f"{got} of {wanted} samples in {took}" + (": the topic is silent." if not got else ".")


def rate_metrics(
    topic: str,
    window_s: int,
    outcome: ReadOutcome,
    *,
    declared_hz: float | None,
) -> TopicMetrics:
    """`TopicMetrics` of one bounded read: the rate the samples arrived at this reader.

    `take()` gives no reception time, so an arrival is the reader's own monotonic clock at the
    polling slice that took the sample. It cannot jump, and needs no clock shared with a
    writer on another host, unlike source timestamps: a 10 Hz writer read as 5 Hz on WSL when
    the wall clock jumped. Merged across writers, and a lower bound where the best-effort
    reader lost samples. No latency and no sequence numbers: DDS gives neither here.
    """
    arrivals = sorted(r.arrival_ns for r in outcome.samples)
    count = len(arrivals)
    span_s = (arrivals[-1] - arrivals[0]) / 1e9 if arrivals else 0.0
    rated = count >= 2 and span_s > 0
    if rated:
        note = (
            f"Bounded read of {outcome.elapsed_s:.1f} s; samples lost by a best-effort reader "
            "lower the rate."
        )
    else:
        note = read_note(outcome, 2) or f"No rate: {count} samples arrived together."
    oversize = fitting_reads(outcome)[1]
    return TopicMetrics(
        topic=topic,
        window_s=window_s,
        window_actual_s=span_s,
        samples_observed=count,
        observed_frequency_hz=(count - 1) / span_s if rated else None,
        declared_frequency_hz=declared_hz,
        status="ok" if rated else "no_samples_yet",
        frequency_basis="reader_arrivals",
        mode_effective="live",
        note=f"{note} {oversize}" if oversize else note,
    )
