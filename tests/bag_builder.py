"""Build small rosbag2 bags with `rosbags` for the bag-reading tests (needs no ROS 2).

A `Series` is one topic with the record time of every message. The message body is made by
`kind`: a stamped `PointStamped`, a `Clock`, a `TFMessage` or an unstamped `String`. Record
times and header stamps are independent, so a test can inject a stamp jump on a clean rhythm.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

NS = 1_000_000_000
BASE = 1_700_000_000 * NS

_TYPES = {
    "stamped": "sensor_msgs/msg/Range",
    "event": "geometry_msgs/msg/PointStamped",
    "clock": "rosgraph_msgs/msg/Clock",
    "tf": "tf2_msgs/msg/TFMessage",
    "string": "std_msgs/msg/String",
    "scan": "sensor_msgs/msg/LaserScan",
}


@dataclass
class Series:
    """One topic: `times` are record times in ns; `stamps` (stamped) or `clocks` (clock) override."""

    topic: str
    kind: str
    times: Sequence[int]
    stamps: Sequence[int] | None = None
    # TF edges of every message, or a function of the record time for edges that come and go.
    edges: Sequence[tuple[str, str]] | Callable[[int], Sequence[tuple[str, str]]] = field(
        default_factory=lambda: [("odom", "base_link")]
    )
    payload_bytes: int = 0
    latched: bool = False
    # `frame_id` of the stamped header, cycled per message (a topic merged from several publishers).
    frames: Sequence[str] = ()
    # QoS profiles recorded for the topic: one per publisher in a real bag.
    publishers: int = 1


def periodic(
    start_ns: int, period_s: float, count: int, jitter_ns: Callable[[int], int] = lambda i: 0
) -> list[int]:
    """`count` record times from `start_ns`, `period_s` apart, each moved by `jitter_ns(i)`."""
    return [start_ns + round(i * period_s * NS) + jitter_ns(i) for i in range(count)]


def without(times: Sequence[int], start_ns: int, end_ns: int) -> list[int]:
    """`times` with every time inside `[start_ns, end_ns)` removed."""
    return [t for t in times if not start_ns <= t < end_ns]


def write_bag(
    directory: Path,
    series: Sequence[Series],
    *,
    storage: str = "mcap",
    compression: str | None = None,
) -> Path:
    """Write `series` into a rosbag2 bag at `directory` and return it; `storage` is `mcap` or `sqlite3`."""
    from rosbags.rosbag2 import StoragePlugin, Writer
    from rosbags.typesys import Stores, get_typestore

    store = get_typestore(Stores.ROS2_HUMBLE)
    plugin = StoragePlugin.MCAP if storage == "mcap" else StoragePlugin.SQLITE3
    events: list[tuple[int, Any, bytes]] = []
    bag_writer = Writer(directory, version=Writer.VERSION_LATEST, storage_plugin=plugin)
    if compression is not None:  # `file` or `message`, zstd
        from rosbags.rosbag2.enums import CompressionFormat, CompressionMode

        mode = CompressionMode.FILE if compression == "file" else CompressionMode.MESSAGE
        bag_writer.set_compression(mode, CompressionFormat.ZSTD)
    with bag_writer as writer:
        for s in series:
            msgtype = _TYPES[s.kind]
            conn = writer.add_connection(
                s.topic,
                msgtype,
                typestore=store,
                offered_qos_profiles=_qos(s.latched, s.publishers),
            )
            for i, t in enumerate(s.times):
                events.append((t, conn, store.serialize_cdr(_message(store, s, i, t), msgtype)))
        for t, conn, raw in sorted(events, key=lambda e: e[0]):
            writer.write(conn, t, raw)
    return directory


def _qos(latched: bool, publishers: int = 1) -> list[Any]:
    """One profile per publisher (transient_local for a latched topic); none for a plain topic."""
    if not latched and publishers <= 1:
        return []
    from rosbags.interfaces import (
        Qos,
        QosDurability,
        QosHistory,
        QosLiveliness,
        QosReliability,
        QosTime,
    )

    zero = QosTime(0, 0)
    durability = QosDurability.TRANSIENT_LOCAL if latched else QosDurability.VOLATILE
    return [
        Qos(
            QosHistory.KEEP_LAST,
            1,
            QosReliability.RELIABLE,
            durability,
            zero,
            zero,
            QosLiveliness.AUTOMATIC,
            zero,
            False,
        )
        for _ in range(max(publishers, 1))
    ]


def _time(store: Any, ns: int) -> Any:
    return store.types["builtin_interfaces/msg/Time"](sec=ns // NS, nanosec=ns % NS)


def _header(store: Any, ns: int, frame: str = "base_link") -> Any:
    return store.types["std_msgs/msg/Header"](stamp=_time(store, ns), frame_id=frame)


def _message(store: Any, s: Series, i: int, t: int) -> Any:
    types = store.types
    stamp = s.stamps[i] if s.stamps is not None else t
    frame = s.frames[i % len(s.frames)] if s.frames else "base_link"
    if s.kind == "stamped":
        return types["sensor_msgs/msg/Range"](
            header=_header(store, stamp, frame),
            radiation_type=0,
            field_of_view=0.1,
            min_range=0.0,
            max_range=10.0,
            range=1.0,
        )
    if s.kind == "event":
        point = types["geometry_msgs/msg/Point"](x=float(i), y=0.0, z=0.0)
        return types["geometry_msgs/msg/PointStamped"](header=_header(store, stamp), point=point)
    if s.kind == "clock":
        return types["rosgraph_msgs/msg/Clock"](clock=_time(store, stamp))
    if s.kind == "tf":
        edges = s.edges(t) if callable(s.edges) else s.edges
        return types["tf2_msgs/msg/TFMessage"](
            transforms=[_transform(store, p, c, t) for p, c in edges]
        )
    if s.kind == "scan":
        return _scan(store, stamp, s.payload_bytes)
    return types["std_msgs/msg/String"](data=f"msg-{i}")


def _transform(store: Any, parent: str, child: str, ns: int) -> Any:
    types = store.types
    vec = types["geometry_msgs/msg/Vector3"](x=0.0, y=0.0, z=0.0)
    rot = types["geometry_msgs/msg/Quaternion"](x=0.0, y=0.0, z=0.0, w=1.0)
    return types["geometry_msgs/msg/TransformStamped"](
        header=_header(store, ns, parent),
        child_frame_id=child,
        transform=types["geometry_msgs/msg/Transform"](translation=vec, rotation=rot),
    )


def _scan(store: Any, ns: int, payload_bytes: int) -> Any:
    import numpy as np

    beams = max(payload_bytes // 4, 1)
    return store.types["sensor_msgs/msg/LaserScan"](
        header=_header(store, ns, "lidar"),
        angle_min=-1.0,
        angle_max=1.0,
        angle_increment=2.0 / beams,
        time_increment=0.0,
        scan_time=0.1,
        range_min=0.1,
        range_max=20.0,
        ranges=np.full(beams, 5.0, dtype=np.float32),
        intensities=np.zeros(0, dtype=np.float32),
    )
