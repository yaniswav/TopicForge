"""Live adapter: wrappers over the `ros2` CLI.

The CLI is used instead of `rclpy` because `rclpy` is pinned to the distro,
needs a sourced setup file and does not come from PyPI, while the CLI is
available wherever ROS2 is installed and easy to stub in tests. An
`rclpy` adapter can later sit behind the same protocol (see the
`TODO(roadmap)` below).

Failures surface as `AdapterError` with a message that is safe to show to
an MCP client. The parsers are module-level functions so they can be tested
without ROS2.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path

from topicforge.adapters.base import AdapterError, AdapterName, EffectiveMode
from topicforge.adapters.ros2_live.parsers import (
    parse_topic_endpoint_qos,
    parse_topic_list_verbose,
    summarize_publisher_qos,
)
from topicforge.constants import DEFAULT_MAX_ARRAY_LENGTH
from topicforge.models import (
    BagAnalysis,
    BagTopicStats,
    EndpointListing,
    MessageSample,
    MismatchScan,
    ParticipantEvent,
    ParticipantInfo,
    SampleResult,
    TopicInfo,
    TopicMetrics,
)

log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SEC = 8.0
_SAMPLE_TIMEOUT_SEC = 3.0

_DEFAULT_DDS_INACTIVE_REASON = (
    "install the Cyclone binding and select it: "
    '`pip install "topicforge[dds-cyclone]"` + '
    "`TOPICFORGE_DDS_BACKEND=cyclone`. Fast DDS also works "
    "(`TOPICFORGE_DDS_BACKEND=fast`) but its Python binding is not on "
    "PyPI and must be built from eProsima's sources."
)


class Ros2CliAdapter:
    """Adapter that shells out to the `ros2` CLI."""

    name: AdapterName = "ros2_cli"

    def __init__(self, executable: str = "ros2", *, dds_inactive_reason: str | None = None) -> None:
        self._exe = executable
        # Why no DDS backend serves next to this adapter; set by the factory.
        self.dds_inactive_reason = dds_inactive_reason

    @property
    def effective_mode(self) -> EffectiveMode:
        return "live"

    def is_available(self) -> bool:
        return shutil.which(self._exe) is not None

    # The CLI cannot reach the DDS layer: these methods raise with the reason
    # no DDS backend is active.

    def _dds_inactive_error(self) -> AdapterError:
        reason = self.dds_inactive_reason or _DEFAULT_DDS_INACTIVE_REASON
        return AdapterError(
            f"DDS module is not active: {reason} The `ros2` CLI adapter can "
            "introspect the ROS2 graph but has no direct DDS-layer access."
        )

    def list_participants(self, domain_id: int = 0) -> list[ParticipantInfo]:
        raise self._dds_inactive_error()

    def detect_qos_mismatches(self, topic: str | None = None) -> MismatchScan:
        raise self._dds_inactive_error()

    def peek_dds_samples(self, topic: str, count: int) -> SampleResult:
        raise self._dds_inactive_error()

    def participant_events(
        self, domain_id: int = 0, lookback_seconds: int = 300
    ) -> list[ParticipantEvent]:
        raise self._dds_inactive_error()

    def topic_metrics(
        self, topic: str, window_seconds: int = 60, domain_id: int = 0
    ) -> TopicMetrics:
        raise self._dds_inactive_error()

    def list_endpoints(
        self,
        topic: str | None = None,
        participant_guid: str | None = None,
        include_observer: bool = False,
        include_departed: bool = False,
    ) -> EndpointListing:
        raise self._dds_inactive_error()

    def peek_bag_samples(self, path: str, topic: str, count: int) -> SampleResult:
        """Read decoded samples from a bag through `BagService` (the `rosbags` library).

        Raises `AdapterError` with an install hint when `rosbags` is missing;
        there is no fallback.
        """
        from topicforge.services.bag_service import BagService

        return BagService().peek_samples(path, topic, count)

    def list_topics(self) -> list[TopicInfo]:
        """Topics with types and publisher/subscriber counts; QoS is not read here.

        Counts come from one `ros2 topic list -v` call. If that output cannot
        be read, each topic is queried with `ros2 topic info` instead.
        """
        out = self._run([self._exe, "topic", "list", "-t"])
        graph_counts = self._graph_counts()
        topics: list[TopicInfo] = []
        for name, msg_type in parse_topic_list(out):
            counts = graph_counts.get(name) if graph_counts is not None else None
            pub_count, sub_count = counts if counts is not None else self._safe_counts(name)
            topics.append(
                TopicInfo(
                    name=name,
                    message_type=msg_type,
                    publisher_count=pub_count,
                    subscriber_count=sub_count,
                    mode_effective=self.effective_mode,
                )
            )
        return topics

    def get_topic_info(self, topic: str) -> TopicInfo:
        out = self._run([self._exe, "topic", "info", topic, "--verbose"])
        info = parse_topic_info(out, fallback_name=topic, mode_effective=self.effective_mode)
        if info is None:
            raise AdapterError(f"Topic not found or empty info: {topic!r}")
        return info

    def sample_messages(
        self,
        topic: str,
        count: int,
        *,
        max_array_length: int | None = DEFAULT_MAX_ARRAY_LENGTH,
        arrays_summary_only: bool = False,
    ) -> list[MessageSample]:
        # `ros2 topic echo` blocks indefinitely, so use --once with a timeout.
        # `--csv` flattens the message in declaration order: for a message
        # starting with a `Header` the first two columns are the stamp, which
        # gives a publish time without rclpy. Headerless messages
        # (`std_msgs/String`, `geometry_msgs/Twist`) get timestamp 0.
        # TODO(roadmap): rclpy-backed adapter: windowed echo, time-range,
        # access to rmw receive timestamps (vs publish-time from Header),
        # better deserialization of complex message payloads.
        if count <= 0:
            return []

        info = self.get_topic_info(topic)
        echo_args = _echo_array_args(max_array_length, arrays_summary_only)
        try:
            out = self._run(
                [self._exe, "topic", "echo", "--csv", "--once", *echo_args, topic],
                timeout=_SAMPLE_TIMEOUT_SEC,
            )
        except AdapterError as exc:
            log.info("sample_messages on %s returned no data: %s", topic, exc)
            return []

        rows = parse_csv_echo(out, truncate_length=max_array_length)
        return [
            MessageSample(
                topic=topic,
                message_type=info.message_type,
                timestamp_ns=ts_ns,
                payload=payload,
            )
            for ts_ns, payload in rows
        ]

    def analyze_bag(self, path: str) -> BagAnalysis:
        bag_path = Path(path)
        if not bag_path.exists():
            raise AdapterError(f"Bag path does not exist: {path}")

        out = self._run([self._exe, "bag", "info", str(bag_path)])
        analysis = parse_bag_info(
            out, fallback_path=str(bag_path), mode_effective=self.effective_mode
        )
        # `ros2 bag info` has no per-topic times: add them when the bag is readable here.
        # Imported here because `services` imports this package.
        from topicforge.services.bag_service import scan_topic_spans
        from topicforge.services.bag_stats import overlay_spans

        spans, note = scan_topic_spans(bag_path)
        if spans:
            topics = overlay_spans(analysis.topics, spans)
            analysis = analysis.model_copy(update={"topics": topics})
        if note:
            analysis = analysis.model_copy(update={"note": note})
        return analysis

    def _graph_counts(self) -> dict[str, tuple[int, int]] | None:
        """`{topic: (pubs, subs)}` from `ros2 topic list -v`, or `None` when unavailable."""
        try:
            text = self._run([self._exe, "topic", "list", "-v"])
        except AdapterError:
            return None
        return parse_topic_list_verbose(text)

    def _safe_counts(self, topic: str) -> tuple[int, int]:
        """`(pub_count, sub_count)` for a topic, `(0, 0)` if the lookup fails."""
        try:
            text = self._run([self._exe, "topic", "info", topic])
        except AdapterError:
            return (0, 0)
        return parse_pub_sub_counts(text)

    def _run(self, cmd: list[str], timeout: float = _DEFAULT_TIMEOUT_SEC) -> str:
        # Resolve to a full path so Windows .cmd/.bat shims work without shell=True.
        resolved = shutil.which(cmd[0]) if cmd[0] == self._exe else cmd[0]
        if resolved is None:
            raise AdapterError(f"`{self._exe}` not found on PATH. Source your ROS2 setup file.")
        full_cmd = [resolved, *cmd[1:]]

        log.debug("ros2 cmd: %s", " ".join(full_cmd))
        try:
            result = subprocess.run(
                full_cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise AdapterError(
                f"`{self._exe}` not found on PATH. Source your ROS2 setup file."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise AdapterError(f"`{' '.join(cmd)}` timed out after {timeout}s") from exc

        if result.returncode != 0:
            stderr_tail = ""
            if result.stderr:
                lines = [line for line in result.stderr.strip().splitlines() if line]
                if lines:
                    stderr_tail = lines[-1]
            raise AdapterError(
                f"`{' '.join(cmd)}` failed (exit {result.returncode}): {stderr_tail or 'no stderr'}"
            )
        return result.stdout


def _echo_array_args(max_array_length: int | None, arrays_summary_only: bool) -> list[str]:
    """`ros2 topic echo` flags for array handling.

    `None` means no truncation (`--full-length`); the CLI default of
    `DEFAULT_MAX_ARRAY_LENGTH` is left implicit.
    """
    args: list[str] = []
    if max_array_length is None:
        args.append("--full-length")
    elif max_array_length != DEFAULT_MAX_ARRAY_LENGTH:
        args += ["--truncate-length", str(max_array_length)]
    if arrays_summary_only:
        args.append("--no-arr")
    return args


# Parsers

_LIST_LINE = re.compile(r"^(\S+)\s+\[(.+)\]\s*$")
_TYPE_LINE = re.compile(r"^\s*Type:\s*(.+)$")
_PUB_COUNT = re.compile(r"^\s*Publisher count:\s*(\d+)\s*$")
_SUB_COUNT = re.compile(r"^\s*Subscription count:\s*(\d+)\s*$")
_BAG_DUR = re.compile(r"Duration:\s*([\d.]+)\s*s")
_BAG_COUNT = re.compile(r"Messages:\s*(\d+)")
_BAG_STORAGE = re.compile(r"Storage id:\s*(\S+)")
_BAG_TOPIC = re.compile(
    r"Topic:\s*(\S+)\s*\|\s*Type:\s*(\S+)\s*\|\s*Count:\s*(\d+)\s*\|\s*Serialization Format:"
)


def parse_topic_list(stdout: str) -> list[tuple[str, str]]:
    """Parse `ros2 topic list -t` output. Returns (name, type) pairs."""
    pairs: list[tuple[str, str]] = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _LIST_LINE.match(line)
        if m:
            pairs.append((m.group(1), m.group(2)))
    return pairs


def parse_pub_sub_counts(stdout: str) -> tuple[int, int]:
    """Parse publisher / subscription counts from `ros2 topic info` output."""
    pub = sub = 0
    for line in stdout.splitlines():
        if m := _PUB_COUNT.match(line):
            pub = int(m.group(1))
        elif m := _SUB_COUNT.match(line):
            sub = int(m.group(1))
    return pub, sub


def parse_topic_info(
    stdout: str, *, fallback_name: str, mode_effective: EffectiveMode
) -> TopicInfo | None:
    """Parse `ros2 topic info <topic> --verbose` output.

    Returns None when no message type is found (the adapter reports the topic
    as not found). `fallback_name` and `mode_effective` come from the caller,
    not from the output. `qos_reliability` and `qos_durability` summarize the
    publishers' QoS blocks (see `summarize_publisher_qos`).
    """
    msg_type: str | None = None
    pub = sub = 0
    for line in stdout.splitlines():
        if m := _TYPE_LINE.match(line):
            msg_type = m.group(1).strip()
        elif m := _PUB_COUNT.match(line):
            pub = int(m.group(1))
        elif m := _SUB_COUNT.match(line):
            sub = int(m.group(1))
    if msg_type is None:
        return None
    reliability, durability = summarize_publisher_qos(parse_topic_endpoint_qos(stdout))
    return TopicInfo(
        name=fallback_name,
        message_type=msg_type,
        publisher_count=pub,
        subscriber_count=sub,
        qos_reliability=reliability,
        qos_durability=durability,
        mode_effective=mode_effective,
    )


# Not called by the adapter: `sample_messages` uses `parse_csv_echo`. Kept as
# a fallback for the plain YAML echo format until an rclpy adapter makes both
# parsers obsolete.
def parse_echo_yaml(stdout: str) -> dict[str, object]:
    """Parse `ros2 topic echo --once` output into a flat dict.

    Only top-level keys are kept, and the raw text goes under `_raw_text`.
    This avoids a YAML dependency.
    """
    flat: dict[str, object] = {}
    for raw in stdout.splitlines():
        line = raw.rstrip()
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        # Top-level keys only (no indentation).
        if line == stripped and ":" in line:
            key, _, value = line.partition(":")
            flat[key.strip()] = value.strip()
    flat["_raw_text"] = stdout
    return flat


# Bounds for reading the first two CSV columns as a Header stamp: `sec` between
# the years 2000 and 2100, `nanosec` below 1e9. Anything else is taken to be
# the first fields of a headerless message.
_TS_SEC_MIN = 946_684_800  # 2000-01-01 UTC
_TS_SEC_MAX = 4_102_444_800  # 2100-01-01 UTC
_TS_NSEC_MAX = 1_000_000_000
_CSV_TRUNCATION_MARK = "..."
# `--no-arr` prints a sequence as one cell whose text contains a comma.
_CSV_SUMMARY_PREFIX = "<sequence type:"


def parse_csv_echo(
    stdout: str, *, truncate_length: int | None = None
) -> list[tuple[int, dict[str, object]]]:
    """Parse `ros2 topic echo --csv [--once]` output into `(timestamp_ns, payload)` rows.

    `message_to_csv` flattens a message in declaration order, so a message
    that starts with a `Header` begins with `header.stamp.sec,nanosec`. When
    the first two columns fall within the bounds above they become
    `timestamp_ns` and are dropped from the payload, which is re-indexed from
    `col_0`. Otherwise `timestamp_ns` is 0 (see `MessageSample.timestamp_ns`).

    A `...` cell is the CLI's marker for an array cut at `--truncate-length`.
    It is not a data column: it is dropped and the index of the column before
    it is listed under `_truncated_after_columns`. With `truncate_length`, a
    string or bytes cell the CLI cut is `truncate_length` characters plus
    `...`: those cells are listed under `_truncated_columns` (a cell that
    happens to have that length and ends in `...` is indistinguishable).

    A `--no-arr` sequence summary (`<sequence type: float, length: 541>`)
    contains a comma and is rejoined into one cell.

    Blank lines, `#` comments and rows with fewer than two columns are
    skipped. Example (`sensor_msgs/Imu`):
        1715600000,123456789,base_link,0.0,...
    -> `[(1715600000123456789, {"col_0": "base_link", "col_1": "0.0", ...,
                                "_raw_text": "..."})]`
    """
    rows: list[tuple[int, dict[str, object]]] = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue

        ts_ns = 0
        value_parts = parts
        try:
            sec = int(parts[0])
            nsec = int(parts[1])
        except ValueError:
            sec = nsec = -1
        if _TS_SEC_MIN <= sec < _TS_SEC_MAX and 0 <= nsec < _TS_NSEC_MAX:
            ts_ns = sec * 1_000_000_000 + nsec
            value_parts = parts[2:]

        payload: dict[str, object] = {}
        truncated_after: list[int] = []
        truncated_cells: list[int] = []
        cells = _join_sequence_summaries(value_parts)
        for value in cells:
            if value == _CSV_TRUNCATION_MARK:
                if payload:
                    truncated_after.append(len(payload) - 1)
                continue
            if _is_cut_cell(value, truncate_length):
                truncated_cells.append(len(payload))
            payload[f"col_{len(payload)}"] = value
        if truncated_after:
            payload["_truncated_after_columns"] = truncated_after
        if truncated_cells:
            payload["_truncated_columns"] = truncated_cells
        payload["_raw_text"] = line
        rows.append((ts_ns, payload))
    return rows


def _join_sequence_summaries(cells: list[str]) -> list[str]:
    """Rejoin `<sequence type: T, length: N>` summaries that the comma split in two."""
    out: list[str] = []
    i = 0
    while i < len(cells):
        cell = cells[i]
        if cell.startswith(_CSV_SUMMARY_PREFIX) and not cell.endswith(">") and i + 1 < len(cells):
            cell = f"{cell}, {cells[i + 1]}"
            i += 1
        out.append(cell)
        i += 1
    return out


def _is_cut_cell(value: str, truncate_length: int | None) -> bool:
    """True for a string cell the CLI cut: `truncate_length` characters plus `...`."""
    return (
        truncate_length is not None
        and len(value) == truncate_length + len(_CSV_TRUNCATION_MARK)
        and value.endswith(_CSV_TRUNCATION_MARK)
    )


def parse_bag_info(
    stdout: str, *, fallback_path: str, mode_effective: EffectiveMode
) -> BagAnalysis:
    """Parse `ros2 bag info <path>` output. `fallback_path` and `mode_effective` come from the caller."""
    duration = 0.0
    msg_count = 0
    storage: str | None = None
    topics: list[BagTopicStats] = []

    for line in stdout.splitlines():
        if m := _BAG_DUR.search(line):
            duration = float(m.group(1))
        if m := _BAG_COUNT.search(line):
            msg_count = int(m.group(1))
        if m := _BAG_STORAGE.search(line):
            storage = m.group(1)
        if m := _BAG_TOPIC.search(line):
            name, msg_type, count = m.group(1), m.group(2), int(m.group(3))
            freq = (count / duration) if duration > 0 else None
            topics.append(
                BagTopicStats(
                    name=name,
                    message_type=msg_type,
                    message_count=count,
                    frequency_hz=freq,
                    frequency_basis="bag_duration" if freq is not None else None,
                )
            )

    return BagAnalysis(
        path=fallback_path,
        storage_format=storage,
        duration_seconds=duration,
        message_count=msg_count,
        topics=topics,
        anomalies=[],
        mode_effective=mode_effective,
    )
