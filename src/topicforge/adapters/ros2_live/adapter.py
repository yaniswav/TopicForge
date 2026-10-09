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
import time
from pathlib import Path

from topicforge import budget as call_budget
from topicforge.adapters.base import AdapterError, AdapterName, EffectiveMode
from topicforge.adapters.common.bag_kind import classify_bag_topic
from topicforge.adapters.ros2_live.echo_stream import EchoRun, stream_echo
from topicforge.adapters.ros2_live.parsers import (
    parse_topic_endpoint_qos,
    parse_topic_list_verbose,
    side_nodes,
    summarize_side_qos,
)
from topicforge.adapters.ros2_live.process_runner import run_process
from topicforge.adapters.ros2_live.sample_extras import (
    WHOLE_ARRAY_STREAM_MAX_CHARS,
    decode_with_summary,
    rate_of_run,
    wants_whole_arrays,
)
from topicforge.constants import (
    DEFAULT_MAX_ARRAY_LENGTH,
    DEFAULT_MAX_SAMPLE_BYTES,
    DEFAULT_SAMPLE_TIMEOUT_S,
)
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
    TopicListItem,
    TopicMetrics,
    TopicRate,
)

log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SEC = 8.0
# Wait for `sample_messages` on a topic with no announced publisher.
_NO_PUBLISHER_WAIT_SEC = 3.0
# `sample_messages` returns within about `timeout_s` plus this: stopping the CLI
# takes up to ~2 s, and decoding may run until 1 s past the deadline.
_PARSE_GRACE_SEC = 1.0
_MIN_ECHO_SEC = 0.5
# Time `sample_messages` keeps back from the call budget for stopping the CLI
# (about 2 s) and decoding (until 1 s past the deadline).
_STOP_RESERVE_SEC = 3.0
# A `/clock` publisher hint older than this is reported as unknown.
_CLOCK_HINT_TTL_SEC = 120.0

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

    def __init__(
        self,
        executable: str = "ros2",
        *,
        dds_inactive_reason: str | None = None,
        max_message_chars: int = DEFAULT_MAX_SAMPLE_BYTES,
    ) -> None:
        self._exe = executable
        # Longest echo document (printed YAML) kept by `sample_messages`.
        self._max_message_chars = max_message_chars
        # Why no DDS backend serves next to this adapter; set by the factory.
        self.dds_inactive_reason = dds_inactive_reason
        # `/clock` has a publisher: (value, monotonic time), refreshed by the
        # calls that already read the graph so `health_check` never runs the CLI.
        self._clock_hint: tuple[bool, float] | None = None

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
        self, domain_id: int = 0, lookback_s: int = 300
    ) -> list[ParticipantEvent]:
        raise self._dds_inactive_error()

    def topic_metrics(self, topic: str, window_s: int = 60, domain_id: int = 0) -> TopicMetrics:
        raise self._dds_inactive_error()

    def list_endpoints(
        self,
        topic: str | None = None,
        participant_guid: str | None = None,
        include_observer: bool = False,
        include_departed: bool = False,
        include_internal: bool = False,
    ) -> EndpointListing:
        raise self._dds_inactive_error()

    def peek_bag_samples(self, path: str, topic: str, count: int) -> SampleResult:
        """Read decoded samples from a bag through `BagService` (the `rosbags` library).

        Raises `AdapterError` with an install hint when `rosbags` is missing;
        there is no fallback.
        """
        from topicforge.services.bag_service import BagService

        return BagService().peek_samples(path, topic, count)

    def list_topics(self) -> list[TopicListItem]:
        """Topics with types and publisher/subscriber counts; QoS is not read here.

        Counts come from one `ros2 topic list -v` call. If that output cannot
        be read, each topic is queried with `ros2 topic info` instead.
        """
        out = self._run([self._exe, "topic", "list", "-t"])
        graph_counts = self._graph_counts()
        listed = parse_topic_list(out)
        if graph_counts is not None:
            self._clock_hint = (graph_counts.get("/clock", (0, 0))[0] > 0, time.monotonic())
        topics: list[TopicListItem] = []
        for name, msg_type in listed:
            counts = graph_counts.get(name) if graph_counts is not None else None
            pub_count, sub_count = counts if counts is not None else self._safe_counts(name)
            topics.append(
                TopicListItem(
                    name=name,
                    message_type=msg_type,
                    publisher_count=pub_count,
                    subscriber_count=sub_count,
                )
            )
        return topics

    def get_topic_info(self, topic: str) -> TopicInfo:
        return self._topic_info(topic, _DEFAULT_TIMEOUT_SEC)

    def _topic_info(self, topic: str, timeout: float) -> TopicInfo:
        out = self._run([self._exe, "topic", "info", topic, "--verbose"], timeout)
        info = parse_topic_info(out, fallback_name=topic, mode_effective=self.effective_mode)
        if info is not None and topic == "/clock":
            self._clock_hint = (info.publisher_count > 0, time.monotonic())
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

        `timeout_s` bounds the whole call, topic lookup included; the call
        returns within about `timeout_s` plus two seconds (stopping the CLI,
        decoding what arrived). QoS is passed explicitly, derived from the
        publishers' QoS in `ros2 topic info --verbose`: the CLI's own choice
        runs once against a possibly cold daemon and can pick a profile that
        never matches a latched topic. A short result carries a `note` saying
        why. A topic that does not exist raises, also for `count` 0.
        TODO(roadmap): rclpy-backed adapter: time-range windows and rmw
        receive timestamps.
        """
        started = time.monotonic()
        # Whatever the call waited for the lock is already gone from the budget.
        timeout_s = call_budget.clamp(timeout_s, _STOP_RESERVE_SEC)
        if timeout_s <= 0:
            raise AdapterError("busy: no time left in this call's budget for sampling, retry")
        info = self._topic_info(topic, min(_DEFAULT_TIMEOUT_SEC, timeout_s))
        if count <= 0:
            return self._sample_result(topic, [], None)

        has_publisher = info.publisher_count > 0
        budget = timeout_s if has_publisher else min(timeout_s, _NO_PUBLISHER_WAIT_SEC)
        remaining = max(budget - (time.monotonic() - started), _MIN_ECHO_SEC)
        # A summary that reads an array (a scan's ranges) needs it whole: stream
        # uncut, summarize, then cut the returned payload as the CLI would have.
        whole = wants_whole_arrays(info.message_type, max_array_length, arrays_summary_only)
        cmd = self._echo_command(
            topic,
            info,
            max_array_length=None if whole else max_array_length,
            arrays_summary_only=arrays_summary_only,
        )
        run = stream_echo(
            cmd,
            count=count,
            deadline_s=remaining,
            max_document_chars=(
                max(self._max_message_chars, WHOLE_ARRAY_STREAM_MAX_CHARS)
                if whole
                else self._max_message_chars
            ),
        )
        if not run.documents and run.exit_code not in (None, 0):
            raise AdapterError(
                f"`ros2 topic echo {topic}` failed (exit {run.exit_code}): "
                f"{run.stderr_tail or 'no stderr'}"
            )

        parse_until = started + budget + _PARSE_GRACE_SEC
        samples, skipped = self._decode(run, topic, info, max_array_length, whole, parse_until)
        note = " ".join(
            part
            for part in (
                _short_result_note(
                    run,
                    len(samples) + skipped,
                    count,
                    budget,
                    has_publisher,
                    _publisher_durability(info),
                ),
                _dropped_note(run.oversized, skipped, self._max_message_chars),
            )
            if part
        )
        return self._sample_result(
            topic, samples, note or None, _rate(run, count, samples, skipped)
        )

    def _decode(
        self,
        run: EchoRun,
        topic: str,
        info: TopicInfo,
        max_array_length: int | None,
        whole_arrays: bool,
        parse_until: float,
    ) -> tuple[list[MessageSample], int]:
        """Decode the run's documents until `parse_until` (monotonic); returns the rest as a count."""
        samples: list[MessageSample] = []
        for index, doc in enumerate(run.documents):
            if time.monotonic() > parse_until:
                return samples, len(run.documents) - index
            message, summary = decode_with_summary(
                doc.text,
                info.message_type,
                max_array_length=max_array_length,
                whole_arrays=whole_arrays,
            )
            samples.append(
                MessageSample(
                    topic=topic,
                    message_type=info.message_type,
                    timestamp_ns=message.timestamp_ns,
                    stamp_source=message.stamp_source,
                    received_ns=doc.received_ns,
                    payload=message.payload,
                    summary=summary,
                )
            )
        return samples, 0

    def _sample_result(
        self,
        topic: str,
        samples: list[MessageSample],
        note: str | None,
        rate: TopicRate | None = None,
    ) -> SampleResult:
        return SampleResult(
            topic=topic,
            count=len(samples),
            samples=samples,
            mode_effective=self.effective_mode,
            note=note,
            rate=rate,
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

    def sim_clock_published(self) -> bool | None:
        """Whether `/clock` had a publisher at the last graph read; `None` when unknown.

        Never runs the CLI: `health_check` calls it and must answer at once
        while a slow `ros2` call holds the ROS lock. The value is refreshed by
        `list_topics` and by `get_topic_info` on `/clock`, and expires after
        two minutes.
        """
        hint = self._clock_hint
        if hint is None or time.monotonic() - hint[1] > _CLOCK_HINT_TTL_SEC:
            return None
        return hint[0]

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
        """Run a `ros2` command and return its stdout, within `timeout` or the call budget."""
        # Resolve to a full path so Windows .cmd/.bat shims work without shell=True.
        resolved = shutil.which(cmd[0]) if cmd[0] == self._exe else cmd[0]
        if resolved is None:
            raise AdapterError(f"`{self._exe}` not found on PATH. Source your ROS2 setup file.")
        full_cmd = [resolved, *cmd[1:]]

        # A call that waited for the lock has less time left: shrink to the budget.
        effective = max(call_budget.clamp(timeout), 0.0)
        log.debug("ros2 cmd: %s", " ".join(full_cmd))
        shown = timeout if effective >= timeout else round(effective, 1)
        if effective <= 0:
            raise AdapterError(f"`{' '.join(cmd)}` timed out after {shown}s")
        try:
            result = run_process(full_cmd, deadline_s=effective)
        except FileNotFoundError as exc:
            raise AdapterError(
                f"`{self._exe}` not found on PATH. Source your ROS2 setup file."
            ) from exc
        except OSError as exc:
            raise AdapterError(
                f"could not start `{self._exe}`: {exc.strerror or type(exc).__name__}"
            ) from exc
        if result.timed_out:
            raise AdapterError(f"`{' '.join(cmd)}` timed out after {shown}s")

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


def _rate(run: EchoRun, count: int, samples: list[MessageSample], skipped: int) -> TopicRate:
    """Rate block of the run; the sim rate needs every message decoded and stamped."""
    stamped = all(s.stamp_source in ("header", "payload") for s in samples)
    complete = not run.oversized and not skipped
    stamps = [s.timestamp_ns for s in samples] if samples and stamped and complete else None
    return rate_of_run(run, count, stamps)


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
    qos = info.publisher_qos
    reliability = "reliable" if qos is not None and qos.reliability == "reliable" else "best_effort"
    durability = (
        "transient_local" if qos is not None and qos.durability == "transient_local" else "volatile"
    )
    return ["--qos-reliability", reliability, "--qos-durability", durability]


def _publisher_durability(info: TopicInfo) -> str | None:
    """Durability of the topic's publishers, `None` when unknown."""
    return info.publisher_qos.durability if info.publisher_qos is not None else None


def _short_result_note(
    run: EchoRun,
    got: int,
    wanted: int,
    budget_s: float,
    has_publisher: bool,
    durability: str | None,
) -> str | None:
    """Why fewer than `wanted` messages arrived; `None` when all did."""
    if got >= wanted:
        return None
    if run.exit_code is not None:
        tail = f": {run.stderr_tail}" if run.stderr_tail else ""
        return (
            f"{got} of {wanted} messages: the `ros2` CLI exited early (exit {run.exit_code}){tail}."
        )
    head = f"{got} of {wanted} messages within {budget_s:g} s"
    if got > 0:
        head += " (fewer than requested)"
    if not has_publisher:
        return (
            f"{head}. No publisher is announced on this topic, so nothing was "
            "waited for beyond a short grace period."
        )
    if durability == "transient_local":
        return (
            f"{head}. The topic is transient_local (latched): it usually holds "
            "only its last message(s), so request `count` 1."
        )
    return (
        f"{head}. A publisher exists but sent no more in time: it may publish "
        "less often than the deadline, be idle, or the message may be too "
        "large to print (retry with a lower `max_array_length` or "
        "`arrays_summary_only` true). A larger `timeout_s` waits longer."
    )


def _dropped_note(oversized: int, skipped: int, max_chars: int) -> str | None:
    """Messages dropped for size, or left undecoded because the time budget ran out."""
    parts: list[str] = []
    if oversized:
        parts.append(
            f"{oversized} message(s) over {max_chars / (1024 * 1024):.1f} MiB were dropped; "
            "use `max_array_length` or `arrays_summary_only`."
        )
    if skipped:
        parts.append(
            f"{skipped} message(s) were received but not decoded: the time budget ran out."
        )
    return " ".join(parts) or None


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
    not from the output. `publisher_qos` and `subscription_qos` summarize the QoS block
    of each side (see `summarize_side_qos`); `publisher_nodes` and `subscriber_nodes` come
    from the `Node name:` / `Node namespace:` lines.
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
    endpoints = parse_topic_endpoint_qos(stdout)
    publisher_qos, publisher_note = summarize_side_qos(endpoints, "PUBLISHER", pub)
    subscription_qos, subscription_note = summarize_side_qos(endpoints, "SUBSCRIPTION", sub)
    return TopicInfo(
        name=fallback_name,
        message_type=msg_type,
        publisher_count=pub,
        subscriber_count=sub,
        publisher_qos=publisher_qos,
        publisher_qos_note=publisher_note,
        subscription_qos=subscription_qos,
        subscription_qos_note=subscription_note,
        publisher_nodes=side_nodes(endpoints, "PUBLISHER"),
        subscriber_nodes=side_nodes(endpoints, "SUBSCRIPTION"),
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
                    kind=classify_bag_topic(name),
                    frequency_hz=freq,
                    frequency_basis="bag_duration" if freq is not None else None,
                )
            )

    return BagAnalysis(
        path=fallback_path,
        storage_format=storage,
        duration_s=duration,
        message_count=msg_count,
        topics=topics,
        anomalies=[],
        mode_effective=mode_effective,
    )
