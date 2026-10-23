"""Inspector: validates tool inputs and delegates to the adapter."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from pathlib import Path

from topicforge import budget as call_budget
from topicforge.adapters.base import AdapterError, AdapterName, MiddlewareAdapter
from topicforge.adapters.common.bag_range import validate_range
from topicforge.adapters.common.log_filter import LogFilter
from topicforge.adapters.common.ros_discovery import cli_ghost_note
from topicforge.adapters.common.tf_tree import build_tf_report
from topicforge.adapters.common.urdf import UrdfError, UrdfModel, parse_urdf
from topicforge.constants import (
    DEFAULT_MAX_ARRAY_LENGTH,
    DEFAULT_MAX_SAMPLE_BYTES,
    DEFAULT_NODE_TIMEOUT_S,
    DEFAULT_SAMPLE_TIMEOUT_S,
    DEFAULT_TF_STALE_AFTER_S,
    DEFAULT_TF_WINDOW_S,
    MAX_ARRAY_LENGTH,
    MAX_NODE_TIMEOUT_S,
    MAX_PARTICIPANT_EVENTS,
    MAX_SAMPLE_COUNT,
    MAX_SAMPLE_TIMEOUT_S,
    MAX_TF_STALE_AFTER_S,
    MAX_TF_WINDOW_S,
    MIN_NODE_TIMEOUT_S,
    MIN_SAMPLE_TIMEOUT_S,
    MIN_TF_STALE_AFTER_S,
    MIN_TF_WINDOW_S,
    TRUNCATED_FIELDS_KEY,
)
from topicforge.models import (
    BagAnalysis,
    EndpointListing,
    GraphDiff,
    MismatchScan,
    NodeInfo,
    NodeListing,
    ParticipantEventListing,
    ParticipantInfo,
    ParticipantListing,
    SampleResult,
    TfReport,
    TopicInfo,
    TopicListing,
    TopicMetrics,
)
from topicforge.services.dds_views import (
    compact_endpoints,
    domain_note,
    endpointless_note,
    join_notes,
    restarts_from_events,
    restarts_from_nodes,
    restarts_from_participants,
    with_event_ages,
)
from topicforge.services.graph_diff import GraphDiffService, parse_baseline
from topicforge.services.params_file import validate_params_file, with_param_diff
from topicforge.services.sample_budget import apply_sample_budget

DEFAULT_SAMPLE_COUNT = 5
DEFAULT_LOOKBACK_SECONDS = 300
DEFAULT_WINDOW_SECONDS = 60
# `inspect_tf` reads the URDF from this latched topic after the capture: wait at most
# `_URDF_READ_S`, keep `_URDF_RESERVE_S` of the call budget back, skip below `_URDF_MIN_READ_S`.
_URDF_TOPIC = "/robot_description"
_URDF_READ_S = 6.0
_URDF_RESERVE_S = 3.0
_URDF_MIN_READ_S = 2.0
_DDS_DOMAIN_MIN = 0
_DDS_DOMAIN_MAX = 232
_LOOKBACK_MIN = 1
_LOOKBACK_MAX = 86400
_WINDOW_MIN = 1
_WINDOW_MAX = 3600

__all__ = ["DEFAULT_SAMPLE_COUNT", "Inspector"]

# ROS2 topic names: a leading `/`, then segments separated by single `/`, each
# starting with a letter or underscore. Rejects `//`, a trailing `/`, dashes,
# dots and shell metacharacters before a value reaches the `ros2` CLI.
_TOPIC_NAME_RE = re.compile(r"^/[A-Za-z_][A-Za-z0-9_]*(?:/[A-Za-z_][A-Za-z0-9_]*)*$")

# DDS topic names may omit the leading `/` (the builtin `DCPS*` topics) and may
# use `::` separators. Whitespace and shell metacharacters are still rejected.
_DDS_TOPIC_NAME_RE = re.compile(r"^[A-Za-z_/][A-Za-z0-9_/:]*$")


class Inspector:
    """Validation layer between tool handlers and adapters.

    Topic name format, count clamping and path checks happen here, so
    adapters can assume well-formed input. Every tool goes through it, even
    when it only passes the call on.
    """

    def __init__(
        self,
        adapter: MiddlewareAdapter,
        *,
        max_sample_bytes: int = DEFAULT_MAX_SAMPLE_BYTES,
        clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        self._adapter = adapter
        self._max_sample_bytes = max_sample_bytes
        self._clock_ns = clock_ns
        self._graph = GraphDiffService(adapter, clock_ns)

    @property
    def backend_name(self) -> AdapterName:
        return self._adapter.name

    def list_topics(self) -> TopicListing:
        # No arguments, so nothing to validate here.
        topics = self._adapter.list_topics()
        return TopicListing(
            topics=topics,
            returned=len(topics),
            total=len(topics),
            truncated=False,
            mode_effective=self._adapter.effective_mode,
        )

    def list_nodes(self) -> NodeListing:
        # No arguments, so nothing to validate here.
        return self._adapter.list_nodes()

    def get_node_info(
        self,
        node: str,
        timeout_s: float = DEFAULT_NODE_TIMEOUT_S,
        params_file: str | None = None,
    ) -> NodeInfo:
        name = _validate_node_name(node)
        _validate_node_timeout_s(timeout_s)
        path = None if params_file is None else validate_params_file(params_file)
        info = self._adapter.get_node_info(name, timeout_s)
        return info if path is None else with_param_diff(info, path)

    def inspect_tf(
        self,
        window_s: float = DEFAULT_TF_WINDOW_S,
        stale_after_s: float = DEFAULT_TF_STALE_AFTER_S,
        source_frame: str | None = None,
        target_frame: str | None = None,
    ) -> TfReport:
        _validate_range("window_s", window_s, MIN_TF_WINDOW_S, MAX_TF_WINDOW_S)
        _validate_range("stale_after_s", stale_after_s, MIN_TF_STALE_AFTER_S, MAX_TF_STALE_AFTER_S)
        source, target = _validate_frame_pair(source_frame, target_frame)
        capture = self._adapter.capture_tf(float(window_s))
        urdf, urdf_note = self._read_urdf()
        return build_tf_report(
            capture,
            float(stale_after_s),
            source,
            target,
            window_s=float(window_s),
            mode_effective=self._adapter.effective_mode,
            urdf=urdf,
            urdf_note=urdf_note,
        )

    def _read_urdf(self) -> tuple[UrdfModel | None, str | None]:
        """The URDF on `/robot_description` as `(model, None)`, else `(None, why)`; never raises.

        Without the topic, the robot_state_publisher parameter is tried and a model then
        comes with a note naming that source.

        One latched message, read after the capture within what is left of the call budget.
        A missing topic, a silent publisher, a late answer or a document that is not a URDF
        all become a note: the TF tree is the result, the URDF only a comparison.
        """
        wait = call_budget.clamp(_URDF_READ_S, _URDF_RESERVE_S)
        if wait < _URDF_MIN_READ_S:
            return None, f"No time was left in this call's budget to read {_URDF_TOPIC}."
        try:
            result = self._adapter.sample_messages(
                _URDF_TOPIC, 1, max_array_length=None, timeout_s=wait
            )
        except AdapterError as exc:
            if "not found" in str(exc).lower() or "unknown topic" in str(exc).lower():
                return self._read_urdf_parameter()
            return None, f"{_URDF_TOPIC} could not be read ({exc})."
        text = result.samples[0].payload.get("data") if result.samples else None
        if not isinstance(text, str):
            why = result.note or "no message arrived"
            return None, f"{_URDF_TOPIC} gave no robot description ({why})."
        try:
            return parse_urdf(text), None
        except UrdfError as exc:
            return None, f"{_URDF_TOPIC} was not compared: {exc}."

    def _read_urdf_parameter(self) -> tuple[UrdfModel | None, str | None]:
        """The URDF held as robot_state_publisher's `robot_description` parameter, or why not.

        Only when no node publishes `/robot_description`; the note then names the source.
        """
        read = getattr(self._adapter, "robot_description_parameter", None)
        if not callable(read):
            return None, f"No {_URDF_TOPIC} topic: robot_state_publisher publishes it."
        wait = call_budget.clamp(_URDF_READ_S, _URDF_RESERVE_S)
        if wait < _URDF_MIN_READ_S:
            return None, f"No {_URDF_TOPIC} topic, and no time was left to read the parameter."
        text, source = read(wait)
        if text is None:
            return None, f"No {_URDF_TOPIC} topic, and the parameter was not read: {source}."
        try:
            return parse_urdf(text), f"Read from the robot_description parameter of {source}."
        except UrdfError as exc:
            return None, f"The robot_description parameter of {source} was not compared: {exc}."

    def graph_diff(self, baseline: str = "previous", bag_path: str | None = None) -> GraphDiff:
        """Snapshot the graph (or `bag_path`) and compare it with `baseline`."""
        if not isinstance(baseline, str):
            raise AdapterError("baseline must be a string")
        kind, value = parse_baseline(baseline)
        if kind == "bag":
            value = _validate_bag_path(value)
        return self._graph.diff(value, None if bag_path is None else _validate_bag_path(bag_path))

    def get_topic_info(self, topic: str) -> TopicInfo:
        _validate_topic_name(topic)
        return self._adapter.get_topic_info(topic)

    def sample_messages(
        self,
        topic: str,
        count: int | None = None,
        *,
        max_array_length: int | None = DEFAULT_MAX_ARRAY_LENGTH,
        arrays_summary_only: bool = False,
        timeout_s: float = DEFAULT_SAMPLE_TIMEOUT_S,
        min_log_level: str | None = None,
        node: str | None = None,
    ) -> SampleResult:
        _validate_topic_name(topic)
        n = DEFAULT_SAMPLE_COUNT if count is None else count
        if n < 0:
            raise AdapterError("count must be >= 0")
        _validate_max_array_length(max_array_length)
        _validate_timeout_s(timeout_s)
        LogFilter.of(min_log_level, node)  # raises on a bad level or node before any I/O
        capped = min(n, MAX_SAMPLE_COUNT)
        result = self._adapter.sample_messages(
            topic,
            capped,
            max_array_length=max_array_length,
            arrays_summary_only=arrays_summary_only,
            timeout_s=timeout_s,
            min_log_level=min_log_level,
            node=node,
        )
        samples, notes = apply_sample_budget(result.samples, self._max_sample_bytes)
        if n > capped:
            notes.insert(0, f"count capped to {capped} (requested {n}).")
        if result.note:
            notes.insert(0, result.note)
        if any(TRUNCATED_FIELDS_KEY in s.payload for s in samples):
            notes.append(
                f"Arrays, strings or bytes longer than {max_array_length} elements were cut; "
                f"the cut fields are listed in `{TRUNCATED_FIELDS_KEY}`. Pass "
                "`max_array_length` null for full values, or `arrays_summary_only` true to "
                "drop array contents."
            )
        return result.model_copy(
            update={"samples": samples, "count": len(samples), "note": " ".join(notes) or None}
        )

    def analyze_bag(self, path: str) -> BagAnalysis:
        return self._adapter.analyze_bag(_validate_bag_path(path))

    def _await_dds(self) -> None:
        """Wait, bounded, for a newly started discovery tracker to hear the bus; usually a no-op."""
        wait = getattr(self._adapter, "await_discovery_ready", None)
        if callable(wait):
            wait()

    def _joined_domain(self) -> int | None:
        """The domain the adapter joined at startup, `None` when it does not say."""
        joined = getattr(self._adapter, "observed_domain_id", None)
        return joined if isinstance(joined, int) else None

    def _observed_domain(self, requested: int) -> int:
        """The domain the adapter joined at startup; the requested one when it does not say."""
        joined = self._joined_domain()
        return requested if joined is None else joined

    def _domain_note(self, requested: int) -> str | None:
        return domain_note(requested, self._joined_domain())

    def list_participants(self, domain_id: int = 0) -> ParticipantListing:
        _validate_dds_domain(domain_id)
        self._await_dds()
        participants = self._adapter.list_participants(domain_id)
        restarts, restarts_note = restarts_from_participants(participants)
        return ParticipantListing(
            participants=participants,
            returned=len(participants),
            total=len(participants),
            truncated=False,
            domain_id=self._observed_domain(domain_id),
            mode_effective=self._adapter.effective_mode,
            note=join_notes(
                self._domain_note(domain_id),
                self._ghost_note(participants),
                endpointless_note(participants, self._clock_ns()),
            ),
            restarts_by_name=restarts,
            restarts_note=restarts_note,
            restarts_by_node=restarts_from_nodes(participants),
        )

    def _ghost_note(self, participants: list[ParticipantInfo]) -> str | None:
        """Note on the `ros2` CLI processes TopicForge spawned (flagged or probable), else `None`."""
        probable = getattr(self._adapter, "unflagged_ghost_count", None)
        return cli_ghost_note(
            sum(1 for p in participants if p.is_topicforge_cli),
            probable(participants) if callable(probable) else 0,
        )

    def detect_qos_mismatches(self, topic: str | None = None) -> MismatchScan:
        if topic is not None:
            _validate_topic_name_dds(topic)
        self._await_dds()
        return self._adapter.detect_qos_mismatches(topic)

    def peek_dds_samples(self, topic: str, count: int | None = None) -> SampleResult:
        _validate_topic_name_dds(topic)
        n = DEFAULT_SAMPLE_COUNT if count is None else count
        if n < 0:
            raise AdapterError("count must be >= 0")
        self._await_dds()
        return self._adapter.peek_dds_samples(topic, min(n, MAX_SAMPLE_COUNT))

    def participant_events(
        self, domain_id: int = 0, lookback_s: int | None = None
    ) -> ParticipantEventListing:
        _validate_dds_domain(domain_id)
        seconds = DEFAULT_LOOKBACK_SECONDS if lookback_s is None else lookback_s
        _validate_lookback_s(seconds)
        self._await_dds()
        events = self._adapter.participant_events(domain_id, seconds)
        restarts, restarts_note = restarts_from_events(events)
        if self._adapter.effective_mode == "live":
            events = with_event_ages(events, self._clock_ns())
        # The adapters keep a ring of MAX_PARTICIPANT_EVENTS: a full result may have lost older ones.
        full = len(events) >= MAX_PARTICIPANT_EVENTS
        return ParticipantEventListing(
            events=events,
            returned=len(events),
            total=len(events),
            truncated=full,
            domain_id=self._observed_domain(domain_id),
            mode_effective=self._adapter.effective_mode,
            note=join_notes(
                self._domain_note(domain_id),
                (
                    f"The event log holds the last {MAX_PARTICIPANT_EVENTS} events; older events "
                    "inside the window may have been dropped. Narrow `lookback_s`."
                    if full
                    else None
                ),
            ),
            restarts_by_name=restarts,
            restarts_note=restarts_note,
        )

    def topic_metrics(
        self,
        topic: str,
        window_s: int | None = None,
        domain_id: int = 0,
    ) -> TopicMetrics:
        _validate_topic_name_dds(topic)
        _validate_dds_domain(domain_id)
        seconds = DEFAULT_WINDOW_SECONDS if window_s is None else window_s
        _validate_window_s(seconds)
        self._await_dds()
        result = self._adapter.topic_metrics(topic, seconds, domain_id)
        note = join_notes(result.note, self._domain_note(domain_id))
        return result if note == result.note else result.model_copy(update={"note": note})

    def list_endpoints(
        self,
        topic: str | None = None,
        participant_guid: str | None = None,
        include_observer: bool = False,
        domain_id: int = 0,
        include_departed: bool = False,
        include_internal: bool = False,
        fields: str = "full",
    ) -> EndpointListing:
        _validate_dds_domain(domain_id)
        if fields not in ("full", "compact"):
            raise AdapterError(f"fields must be 'full' or 'compact', got {fields!r}")
        if topic is not None:
            _validate_topic_name_dds(topic)
        guid = participant_guid.strip() if participant_guid else None
        if participant_guid is not None and not guid:
            raise AdapterError("participant_guid must be a non-empty string when given")
        self._await_dds()
        listing = self._adapter.list_endpoints(
            topic, guid, include_observer, include_departed, include_internal
        )
        note = join_notes(listing.note, self._domain_note(domain_id))
        if note != listing.note:
            listing = listing.model_copy(update={"note": note})
        return compact_endpoints(listing) if fields == "compact" else listing

    def peek_bag_samples(
        self,
        path: str,
        topic: str,
        count: int | None = None,
        *,
        start_s: float | None = None,
        end_s: float | None = None,
        min_log_level: str | None = None,
        node: str | None = None,
    ) -> SampleResult:
        clean_path = _validate_bag_path(path)
        _validate_topic_name_dds(topic)
        n = DEFAULT_SAMPLE_COUNT if count is None else count
        if n < 0:
            raise AdapterError("count must be >= 0")
        validate_range(start_s, end_s)
        LogFilter.of(min_log_level, node)
        result = self._adapter.peek_bag_samples(
            clean_path,
            topic,
            min(n, MAX_SAMPLE_COUNT),
            start_s=start_s,
            end_s=end_s,
            min_log_level=min_log_level,
            node=node,
        )
        samples, notes = apply_sample_budget(result.samples, self._max_sample_bytes)
        if not notes:
            return result
        note = " ".join([n for n in [result.note, *notes] if n])
        return result.model_copy(update={"samples": samples, "count": len(samples), "note": note})


def _validate_timeout_s(value: float) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise AdapterError(f"timeout_s must be a number, got {type(value).__name__}")
    if not MIN_SAMPLE_TIMEOUT_S <= value <= MAX_SAMPLE_TIMEOUT_S:
        raise AdapterError(
            f"timeout_s must be in {MIN_SAMPLE_TIMEOUT_S:g}..{MAX_SAMPLE_TIMEOUT_S:g}, got {value}"
        )


def _validate_max_array_length(value: int | None) -> None:
    if value is None:
        return
    if not isinstance(value, int) or isinstance(value, bool):
        raise AdapterError(f"max_array_length must be an int or null, got {type(value).__name__}")
    if value < 1 or value > MAX_ARRAY_LENGTH:
        raise AdapterError(f"max_array_length must be in 1..{MAX_ARRAY_LENGTH}, got {value}")


def _validate_dds_domain(domain_id: int) -> None:
    if not isinstance(domain_id, int) or isinstance(domain_id, bool):
        raise AdapterError(f"domain_id must be an int, got {type(domain_id).__name__}")
    if domain_id < _DDS_DOMAIN_MIN or domain_id > _DDS_DOMAIN_MAX:
        raise AdapterError(
            f"domain_id must be in {_DDS_DOMAIN_MIN}..{_DDS_DOMAIN_MAX}, got {domain_id}"
        )


def _validate_lookback_s(seconds: int) -> None:
    if not isinstance(seconds, int) or isinstance(seconds, bool):
        raise AdapterError(f"lookback_s must be an int, got {type(seconds).__name__}")
    if seconds < _LOOKBACK_MIN or seconds > _LOOKBACK_MAX:
        raise AdapterError(f"lookback_s must be in {_LOOKBACK_MIN}..{_LOOKBACK_MAX}, got {seconds}")


def _validate_window_s(seconds: int) -> None:
    if not isinstance(seconds, int) or isinstance(seconds, bool):
        raise AdapterError(f"window_s must be an int, got {type(seconds).__name__}")
    if seconds < _WINDOW_MIN or seconds > _WINDOW_MAX:
        raise AdapterError(f"window_s must be in {_WINDOW_MIN}..{_WINDOW_MAX}, got {seconds}")


def _validate_topic_name(topic: str) -> None:
    if not topic or not topic.strip():
        raise AdapterError("topic must be a non-empty string")
    if not topic.startswith("/"):
        raise AdapterError(f"topic must start with '/' (got {topic!r})")
    if not _TOPIC_NAME_RE.match(topic):
        raise AdapterError(
            f"topic name is malformed (got {topic!r}); each `/`-separated "
            "segment must start with a letter or underscore and contain only "
            "letters, digits, and underscores (no `//`, no trailing `/`)"
        )


def _validate_node_name(node: str) -> str:
    """The fully qualified node name; a missing leading `/` is added."""
    if not isinstance(node, str) or not node.strip():
        raise AdapterError("node must be a non-empty string")
    name = node.strip()
    if not name.startswith("/"):
        name = "/" + name
    if not _TOPIC_NAME_RE.match(name):
        raise AdapterError(
            f"node name is malformed (got {node!r}); give the fully qualified name, e.g. "
            "`/lidar_driver` or `/robot1/lidar_driver`: each `/`-separated segment must "
            "start with a letter or underscore and contain only letters, digits and underscores"
        )
    return name


def _validate_range(name: str, value: float, low: float, high: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AdapterError(f"{name} must be a number, got {type(value).__name__}")
    if not low <= value <= high:
        raise AdapterError(f"{name} must be in {low:g}..{high:g}, got {value}")


def _validate_frame_pair(source: str | None, target: str | None) -> tuple[str | None, str | None]:
    """Both frames or neither; names are stripped and a leading `/` removed (tf2 has none)."""
    if (source is None) != (target is None):
        raise AdapterError("source_frame and target_frame go together: give both or neither")
    if source is None or target is None:
        return None, None
    names = []
    for label, value in (("source_frame", source), ("target_frame", target)):
        name = value.strip().lstrip("/") if isinstance(value, str) else ""
        if not name or len(name) > 256 or any(c.isspace() for c in name):
            raise AdapterError(
                f"{label} must be a frame name without spaces, e.g. `base_link` (got {value!r})"
            )
        names.append(name)
    return names[0], names[1]


def _validate_node_timeout_s(value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AdapterError(f"timeout_s must be a number, got {type(value).__name__}")
    if value < MIN_NODE_TIMEOUT_S or value > MAX_NODE_TIMEOUT_S:
        raise AdapterError(
            f"timeout_s must be in {MIN_NODE_TIMEOUT_S:g}..{MAX_NODE_TIMEOUT_S:g}, got {value}"
        )


def _validate_topic_name_dds(topic: str) -> None:
    """Validate a DDS topic name (see `_DDS_TOPIC_NAME_RE`)."""
    if not topic or not topic.strip():
        raise AdapterError("topic must be a non-empty string")
    if not _DDS_TOPIC_NAME_RE.match(topic):
        raise AdapterError(
            f"DDS topic name is malformed (got {topic!r}); allowed "
            "characters are letters, digits, `_`, `:`, and `/`. "
            "Whitespace, shell metacharacters, and dashes are rejected."
        )


def _validate_bag_path(path: str) -> str:
    """Return the stripped bag path, or raise `AdapterError` if it is empty, blank or malformed.

    Existence and extension are checked by the adapter.
    """
    if not isinstance(path, str):
        raise AdapterError("path must be a string")
    if not path or not path.strip():
        raise AdapterError("path must be a non-empty string")
    clean = path.strip()
    if "\x00" in clean:
        raise AdapterError("path must not contain null bytes")
    try:
        Path(clean)
    except (ValueError, OSError) as exc:
        raise AdapterError(f"path is not a valid filesystem path: {exc}") from exc
    return clean
