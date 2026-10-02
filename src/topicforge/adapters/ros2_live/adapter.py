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
from topicforge.adapters.ros2_live.echo_parser import parse_echo_document
from topicforge.adapters.ros2_live.echo_stream import stream_echo
from topicforge.adapters.ros2_live.parsers import (
    parse_topic_endpoint_qos,
    parse_topic_list_verbose,
    summarize_publisher_qos,
)
from topicforge.constants import DEFAULT_MAX_ARRAY_LENGTH, DEFAULT_SAMPLE_TIMEOUT_S
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
# Wait for `sample_messages` on a topic with no announced publisher.
_NO_PUBLISHER_WAIT_SEC = 3.0

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
        timeout_s: float = DEFAULT_SAMPLE_TIMEOUT_S,
    ) -> SampleResult:
        """Stream `ros2 topic echo` and keep up to `count` messages within `timeout_s`.

        QoS is passed explicitly, derived from the publishers' QoS in
        `ros2 topic info --verbose`: the CLI's own choice runs once against a
        possibly cold daemon and can pick a profile that never matches a
        latched topic. A short result carries a `note` saying why.
        TODO(roadmap): rclpy-backed adapter: time-range windows and rmw
        receive timestamps.
        """
        if count <= 0:
            return self._sample_result(topic, [], None)

        info = self.get_topic_info(topic)
        cmd = self._echo_command(
            topic, info, max_array_length=max_array_length, arrays_summary_only=arrays_summary_only
        )
        has_publisher = info.publisher_count > 0
        deadline = timeout_s if has_publisher else min(timeout_s, _NO_PUBLISHER_WAIT_SEC)
        run = stream_echo(cmd, count=count, deadline_s=deadline)
        if not run.documents and run.exit_code not in (None, 0):
            raise AdapterError(
                f"`ros2 topic echo {topic}` failed (exit {run.exit_code}): "
                f"{run.stderr_tail or 'no stderr'}"
            )

        samples = []
        for doc in run.documents:
            message = parse_echo_document(doc.text, truncate_length=max_array_length)
            samples.append(
                MessageSample(
                    topic=topic,
                    message_type=info.message_type,
                    timestamp_ns=message.timestamp_ns,
                    stamp_source=message.stamp_source,
                    received_ns=doc.received_ns,
                    payload=message.payload,
                )
            )
        note = _short_result_note(len(samples), count, deadline, has_publisher)
        return self._sample_result(topic, samples, note)

    def _sample_result(
        self, topic: str, samples: list[MessageSample], note: str | None
    ) -> SampleResult:
        return SampleResult(
            topic=topic,
            count=len(samples),
            samples=samples,
            mode_effective=self.effective_mode,
            note=note,
        )

    def _echo_command(
        self,
        topic: str,
        info: TopicInfo,
        *,
        max_array_length: int | None,
        arrays_summary_only: bool,
    ) -> list[str]:
        exe = shutil.which(self._exe)
        if exe is None:
            raise AdapterError(f"`{self._exe}` not found on PATH. Source your ROS2 setup file.")
        return [
            exe,
            "topic",
            "echo",
            "--no-lost-messages",
            *_echo_qos_args(info),
            *_echo_array_args(max_array_length, arrays_summary_only),
            topic,
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


def _echo_qos_args(info: TopicInfo) -> list[str]:
    """Explicit `--qos-reliability` / `--qos-durability` that match every publisher.

    `reliable` and `transient_local` only when all publishers use them; the
    permissive `best_effort` / `volatile` otherwise (and when there is no
    publisher to read), which connects to any publisher.
    """
    reliability = "reliable" if info.qos_reliability == "reliable" else "best_effort"
    durability = "transient_local" if info.qos_durability == "transient_local" else "volatile"
    return ["--qos-reliability", reliability, "--qos-durability", durability]


def _short_result_note(got: int, wanted: int, deadline_s: float, has_publisher: bool) -> str | None:
    """Why fewer than `wanted` messages arrived; `None` when all did."""
    if got >= wanted:
        return None
    head = f"{got} of {wanted} messages within {deadline_s:g} s."
    if not has_publisher:
        return (
            f"{head} No publisher is announced on this topic, so nothing was "
            "waited for beyond a short grace period."
        )
    return (
        f"{head} A publisher exists but sent nothing in time: it may publish "
        "less often than the deadline, be idle, or the message may be too "
        "large to print (retry with a lower `max_array_length` or "
        "`arrays_summary_only` true). A larger `timeout_s` waits longer."
    )


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
