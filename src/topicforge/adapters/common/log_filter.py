"""Filter for `rcl_interfaces/msg/Log` messages (`/rosout`), shared by live, bag and mock.

Pure functions over a decoded payload, tested without ROS 2. The level threshold is
`level >= value` on the ROS 2 constants (10 debug, 20 info, 30 warn, 40 error, 50 fatal).
A node matches the `name` field of the log message, which is the node's fully qualified
name with the namespace separated by dots (`/robot1/driver` logs as `robot1.driver`),
and also its child loggers (`driver.camera`). It never matches a free prefix: `drive`
does not match `driver`.
"""

from __future__ import annotations

from typing import Any, Final

from topicforge.adapters.base import AdapterError
from topicforge.models import MessageSample

LOG_MESSAGE_TYPES: Final[frozenset[str]] = frozenset(
    {"rcl_interfaces/msg/Log", "rcl_interfaces/Log"}
)

LOG_LEVELS: Final[dict[str, int]] = {"debug": 10, "info": 20, "warn": 30, "error": 40, "fatal": 50}

# Documents a filtered live read streams at most, and the depth asked of the /rosout
# reader (the writer keeps the last 1000 for 10 s, KEEP_LAST, TRANSIENT_LOCAL).
LOG_STREAM_CAP: Final = 2000
LOG_READER_DEPTH: Final = 2000


def is_log_type(message_type: str) -> bool:
    """True for the `/rosout` message type."""
    return message_type in LOG_MESSAGE_TYPES


def level_threshold(min_log_level: str | None) -> int | None:
    """Numeric threshold of a level name; `None` for no level filter.

    Case is ignored and `warning` means `warn`. Raises `AdapterError` for any other name
    outside `debug`, `info`, `warn`, `error`, `fatal`.
    """
    if min_log_level is None:
        return None
    try:
        name = min_log_level.strip().lower()
        return LOG_LEVELS["warn" if name == "warning" else name]
    except (KeyError, TypeError, AttributeError):
        raise AdapterError(
            f"min_log_level must be one of {', '.join(LOG_LEVELS)}, got {min_log_level!r}"
        ) from None


def normalize_logger_name(node: str) -> str:
    """The logger name a node name designates: `/ns/name`, `name` and `ns.name` give `ns.name`."""
    return node.strip().lstrip("/").replace("/", ".")


def normalize_node_filter(node: str | None) -> str | None:
    """Normalized `node` filter, `None` when unset; `AdapterError` when it names nothing."""
    if node is None:
        return None
    name = normalize_logger_name(node)
    if not name or any(not part for part in name.split(".")):
        raise AdapterError(f"node must be a node name such as `/ns/name`, got {node!r}")
    return name


def matches_log(payload: dict[str, Any], *, threshold: int | None, logger: str | None) -> bool:
    """Whether a decoded Log payload passes the level threshold and the node match.

    `logger` is a name already normalized with `normalize_node_filter`. A payload
    without a numeric `level` fails any level filter; without a string `name`, any
    node filter.
    """
    if threshold is not None:
        level = payload.get("level")
        if isinstance(level, bool) or not isinstance(level, int) or level < threshold:
            return False
    if logger is not None:
        name = payload.get("name")
        if not isinstance(name, str):
            return False
        if name != logger and not name.startswith(logger + "."):
            return False
    return True


def describe_filter(min_log_level: str | None, node: str | None) -> str:
    """The active filter for a note, e.g. `min_log_level=error, node=/robot1/driver`."""
    parts = []
    if min_log_level is not None:
        parts.append(f"min_log_level={min_log_level}")
    if node is not None:
        parts.append(f"node={node}")
    return ", ".join(parts)


def require_log_topic(topic: str, message_type: str) -> None:
    """Raise `AdapterError` when a log filter is used on a topic that is not a Log topic."""
    if not is_log_type(message_type):
        raise AdapterError(
            "min_log_level and node filter `rcl_interfaces/msg/Log` topics such as "
            f"/rosout; {topic} carries {message_type}."
        )


class LogFilter:
    """A validated `min_log_level` / `node` pair, ready to test payloads and to write notes."""

    def __init__(self, min_log_level: str | None, node: str | None) -> None:
        self.min_log_level = min_log_level
        self.node = node
        self._threshold = level_threshold(min_log_level)
        self._logger = normalize_node_filter(node)

    @classmethod
    def of(cls, min_log_level: str | None, node: str | None) -> LogFilter | None:
        """The filter, or `None` when neither option is set."""
        if min_log_level is None and node is None:
            return None
        return cls(min_log_level, node)

    def __str__(self) -> str:
        return describe_filter(self.min_log_level, self.node)

    def matches(self, payload: dict[str, Any]) -> bool:
        """Whether a decoded Log payload passes both conditions."""
        return matches_log(payload, threshold=self._threshold, logger=self._logger)


_NO_MATCH_HINT = (
    " Nothing matched: a log `name` is the node name with dots for the namespace "
    "(`/ns/node` logs as `ns.node`); lower `min_log_level` or check the node."
)


def newest_matches(
    samples: list[MessageSample], count: int, log_filter: LogFilter, *, cap_hit: bool = False
) -> tuple[list[MessageSample], str | None]:
    """Live rule: the newest `count` matching samples in stamp order, and the note.

    `samples` are every message read; the sort is stable, so equal stamps keep arrival order.
    """
    if count <= 0:
        return [], None
    matched = [s for s in samples if log_filter.matches(s.payload)]
    matched.sort(key=lambda s: s.timestamp_ns)
    kept = matched[-count:]
    note = f"{len(matched)} of {len(samples)} log messages read matched {log_filter}"
    if len(matched) > len(kept):
        note += f"; the newest {len(kept)} are returned in stamp order"
    note += "."
    if cap_hit:
        note += (
            f" The read stopped at the {LOG_STREAM_CAP}-message cap, so this is the newest of the "
            f"first {LOG_STREAM_CAP} read and newer logs may be unread: use a narrower `node` "
            "or a higher `min_log_level`."
        )
    if not matched:
        note += _NO_MATCH_HINT
    return kept, note


def first_matches(
    samples: list[MessageSample], count: int, log_filter: LogFilter
) -> tuple[list[MessageSample], str | None]:
    """Bag rule: the first `count` matching samples in bag order, and the note."""
    kept: list[MessageSample] = []
    for sample in samples:
        if len(kept) >= count:
            break
        if log_filter.matches(sample.payload):
            kept.append(sample)
    return kept, first_matches_note(len(kept), len(samples), log_filter)


def first_matches_note(kept: int, scanned: int, log_filter: LogFilter) -> str | None:
    """Note of a bag read that filtered `scanned` messages and kept the first `kept`.

    `None` when nothing was scanned (a zero `count` or an empty range): no hint then.
    """
    if kept == 0 and scanned == 0:
        return None
    note = f"{kept} messages matched {log_filter} among the {scanned} scanned (first matches, bag order)."
    return note + (_NO_MATCH_HINT if kept == 0 else "")
