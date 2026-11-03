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
import shutil
import time
from pathlib import Path

from topicforge import budget as call_budget
from topicforge.adapters.base import AdapterError, AdapterName, EffectiveMode
from topicforge.adapters.common import bag_read, bag_stats
from topicforge.adapters.common.log_filter import (
    LogFilter,
)
from topicforge.adapters.common.nodes import (
    build_node_listing,
    lifecycle_node_names,
)
from topicforge.adapters.ros2_live import node_reader
from topicforge.adapters.ros2_live.node_parsers import (
    parse_node_list,
)
from topicforge.adapters.ros2_live.parsers import (
    parse_bag_info,
    parse_pub_sub_counts,
    parse_service_list,
    parse_topic_info,
    parse_topic_list,
    parse_topic_list_verbose,
)
from topicforge.adapters.ros2_live.process_runner import ProcessResult, run_process
from topicforge.adapters.ros2_live.sampler import echo_qos_args, sample_messages
from topicforge.adapters.ros2_live.tf_capture import capture_tf
from topicforge.constants import (
    DEFAULT_MAX_ARRAY_LENGTH,
    DEFAULT_MAX_SAMPLE_BYTES,
    DEFAULT_SAMPLE_TIMEOUT_S,
    ROS2_CLI_TIMEOUT_S,
)
from topicforge.models import (
    BagAnalysis,
    EndpointListing,
    MismatchScan,
    NodeInfo,
    NodeListing,
    ParticipantEvent,
    ParticipantInfo,
    SampleResult,
    TfCapture,
    TopicInfo,
    TopicListItem,
    TopicMetrics,
)

log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SEC = ROS2_CLI_TIMEOUT_S
# A `/clock` publisher hint older than this is reported as unknown.
_CLOCK_HINT_TTL_SEC = 120.0

_DEFAULT_DDS_INACTIVE_REASON = (
    "install the Cyclone binding and select it: "
    '`pip install "topicforge[dds-cyclone]"` + '
    "`TOPICFORGE_DDS_BACKEND=cyclone`. Cyclone also observes Fast DDS "
    "and other vendors' participants on the same domain."
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

    def peek_bag_samples(
        self,
        path: str,
        topic: str,
        count: int,
        *,
        start_s: float | None = None,
        end_s: float | None = None,
        min_log_level: str | None = None,
        node: str | None = None,
    ) -> SampleResult:
        """Read decoded samples from a bag through `BagService` (the `rosbags` library).

        Raises `AdapterError` with an install hint when `rosbags` is missing;
        there is no fallback.
        """
        return bag_read.peek_bag_samples(
            path,
            topic,
            count,
            start_s=start_s,
            end_s=end_s,
            log_filter=LogFilter.of(min_log_level, node),
        )

    def list_nodes(self) -> NodeListing:
        """Nodes on the graph: names, namespaces, duplicates and which are lifecycle nodes.

        Two CLI calls: `ros2 node list`, then `ros2 service list -t` for the lifecycle
        flag. A failed service list does not fail the listing: it sets `lifecycle_note`.
        """
        names = self._node_names()
        try:
            services = parse_service_list(self._run([self._exe, "service", "list", "-t"]))
        except AdapterError as exc:
            return build_node_listing(
                names,
                self.effective_mode,
                lifecycle_note=f"Lifecycle unknown, `ros2 service list` failed: {exc}",
            )
        note = None
        if names and not services:
            note = "Lifecycle unknown: `ros2 service list -t` printed no service."
        return build_node_listing(
            names,
            self.effective_mode,
            lifecycle=lifecycle_node_names(names, services),
            lifecycle_note=note,
        )

    def get_node_info(self, node: str, timeout_s: float = _DEFAULT_TIMEOUT_SEC) -> NodeInfo:
        """Interfaces and parameters of one node (`node list`, `node info`, `param dump`).

        Bounded by `timeout_s` and the call budget; a node that does not answer its
        parameter read yields `parameters` `None` with a note, not an error. See
        `ros2_live.node_reader.get_node_info`.
        """
        return node_reader.get_node_info(
            node,
            timeout_s,
            exe=self._exe,
            node_names=self._node_names,
            run=self._run,
            exec_process=self._exec,
            mode=self.effective_mode,
        )

    def robot_description_parameter(self, timeout_s: float) -> tuple[str | None, str]:
        """The `robot_description` parameter of the robot_state_publisher node, read-only.

        Outside the adapter protocol: `inspect_tf` falls back to it when no node publishes
        `/robot_description`. Returns `(urdf_text, node)` or `(None, why)`; never raises
        for a missing node. See `ros2_live.node_reader.robot_description_parameter`.
        """
        return node_reader.robot_description_parameter(
            timeout_s,
            exe=self._exe,
            node_names=self._node_names,
            exec_process=self._exec,
        )

    def capture_tf(self, window_s: float) -> TfCapture:
        """Raw `/tf_static` and `/tf` transforms: a latched read, then `window_s` of `/tf`.

        Both passes stream `ros2 topic echo` within the call budget; see
        `ros2_live.tf_capture`. A stream that cannot be read becomes a capture note.
        """
        return capture_tf(
            window_s,
            exe=self._resolve_exe(),
            topic_info=self._topic_info,
            qos_args=echo_qos_args,
            max_message_chars=self._max_message_chars,
        )

    def _node_names(self) -> list[str]:
        """Every node name `ros2 node list` prints, duplicates kept."""
        return parse_node_list(self._run([self._exe, "node", "list"]))

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
            unknown = False
            if counts is None:
                counts = self._safe_counts(name)
                unknown = counts is None
            pub_count, sub_count = counts or (0, 0)
            topics.append(
                TopicListItem(
                    name=name,
                    message_type=msg_type,
                    publisher_count=pub_count,
                    subscriber_count=sub_count,
                    counts_unknown=True if unknown else None,
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
        min_log_level: str | None = None,
        node: str | None = None,
    ) -> SampleResult:
        """Stream `ros2 topic echo` and keep up to `count` messages within `timeout_s`.

        Filtered `/rosout` reads, the call budget and the short-result notes are described
        on `ros2_live.sampler.sample_messages`. A topic that does not exist raises, also
        for `count` 0.
        """
        return sample_messages(
            topic,
            count,
            topic_info=self._topic_info,
            resolve_exe=self._resolve_exe,
            mode=self.effective_mode,
            max_message_chars=self._max_message_chars,
            max_array_length=max_array_length,
            arrays_summary_only=arrays_summary_only,
            timeout_s=timeout_s,
            min_log_level=min_log_level,
            node=node,
        )

    def _resolve_exe(self) -> str:
        """Full path of the `ros2` executable, or `AdapterError` when it is not on PATH."""
        exe = shutil.which(self._exe)
        if exe is None:
            raise AdapterError(f"`{self._exe}` not found on PATH. Source your ROS2 setup file.")
        return exe

    def analyze_bag(self, path: str) -> BagAnalysis:
        bag_path = Path(path)
        if not bag_path.exists():
            raise AdapterError(f"Bag path does not exist: {path}")

        out = self._run([self._exe, "bag", "info", str(bag_path)])
        analysis = parse_bag_info(
            out, fallback_path=str(bag_path), mode_effective=self.effective_mode
        )
        # `ros2 bag info` has no per-topic times: add them when the bag is readable here.
        analysis = analysis.model_copy(update={"start_ns": bag_read.read_start_ns(bag_path)})
        spans, note = bag_read.scan_topic_spans(bag_path)
        if spans:
            topics = bag_stats.overlay_spans(analysis.topics, spans)
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

    def _safe_counts(self, topic: str) -> tuple[int, int] | None:
        """`(pub_count, sub_count)` for a topic, `None` if the lookup fails."""
        try:
            text = self._run([self._exe, "topic", "info", topic])
        except AdapterError:
            return None
        return parse_pub_sub_counts(text)

    def _exec(
        self,
        cmd: list[str],
        timeout: float,
        *,
        max_output_bytes: int | None = None,
    ) -> tuple[ProcessResult, float]:
        """Run `cmd` within `timeout` or the call budget; `(result, seconds allowed)`.

        Raises only when the process cannot start. A deadline or a non-zero exit is
        left in the result for the caller to read.
        """
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
            return ProcessResult(None, "", "", timed_out=True, truncated=False), shown
        options = {} if max_output_bytes is None else {"max_output_bytes": max_output_bytes}
        try:
            result = run_process(full_cmd, deadline_s=effective, **options)
        except FileNotFoundError as exc:
            raise AdapterError(
                f"`{self._exe}` not found on PATH. Source your ROS2 setup file."
            ) from exc
        except OSError as exc:
            raise AdapterError(
                f"could not start `{self._exe}`: {exc.strerror or type(exc).__name__}"
            ) from exc
        return result, shown

    def _run(self, cmd: list[str], timeout: float = _DEFAULT_TIMEOUT_SEC) -> str:
        """Run a `ros2` command and return its stdout, within `timeout` or the call budget."""
        result, shown = self._exec(cmd, timeout)
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
