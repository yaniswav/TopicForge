"""Live adapter — defensive wrappers over the `ros2` CLI.

Why CLI and not `rclpy`?
  * `rclpy` is hard to depend on portably — distro-pinned, requires a sourced
    setup file, and ships with the ROS2 install rather than from PyPI.
  * The `ros2` CLI is stable, widely available wherever ROS2 is installed,
    and trivial to mock in tests by stubbing `subprocess.run`.
  * A richer `rclpy`-backed adapter can ship later behind the same protocol.
    See `# TODO(roadmap): rclpy-backed adapter` below.

Public methods never raise raw subprocess errors. They raise `AdapterError`
with a message that is safe to surface to an MCP client.

The pure parsers at module level are split out from the adapter class so
they can be unit-tested without a running ROS2 install.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path

from topicforge.adapters.base import AdapterError, AdapterName, EffectiveMode
from topicforge.models import (
    BagAnalysis,
    BagTopicStats,
    MessageSample,
    MismatchReport,
    ParticipantEvent,
    ParticipantInfo,
    SampleResult,
    TopicInfo,
    TopicMetrics,
)

log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SEC = 8.0
_SAMPLE_TIMEOUT_SEC = 3.0

_DDS_MODULE_INACTIVE_MSG = (
    "DDS module is not active in this configuration. The `ros2` CLI "
    "adapter can introspect the ROS2 graph but does not have direct "
    "DDS-layer access. Install one of the DDS extras and select a "
    "backend: `pip install topicforge[dds-cyclone]` + "
    "`TOPICFORGE_DDS_BACKEND=cyclone` (Eclipse CycloneDDS), or "
    "`pip install topicforge[dds-fast]` + `TOPICFORGE_DDS_BACKEND=fast` "
    "(eProsima Fast DDS), or `pip install topicforge[dds]` for both "
    "OSS backends. RTI Connext is v0.4.0+ Pro tier (BYO license)."
)


class Ros2CliAdapter:
    """Adapter that shells out to the `ros2` CLI."""

    name: AdapterName = "ros2_cli"

    def __init__(self, executable: str = "ros2") -> None:
        self._exe = executable

    @property
    def effective_mode(self) -> EffectiveMode:
        return "live"

    def is_available(self) -> bool:
        return shutil.which(self._exe) is not None

    # ---------------------------- DDS module ------------------------------
    # The ROS2 CLI cannot reach the DDS layer directly. The 3 DDS methods
    # below raise AdapterError with a clear remediation path. This is the
    # MVP D6 limitation, documented in CHANGELOG and README.

    def list_participants(self, domain_id: int = 0) -> list[ParticipantInfo]:
        raise AdapterError(_DDS_MODULE_INACTIVE_MSG)

    def detect_qos_mismatches(self, topic: str | None = None) -> list[MismatchReport]:
        raise AdapterError(_DDS_MODULE_INACTIVE_MSG)

    def peek_dds_samples(self, topic: str, count: int) -> SampleResult:
        raise AdapterError(_DDS_MODULE_INACTIVE_MSG)

    def participant_events(
        self, domain_id: int = 0, lookback_seconds: int = 300
    ) -> list[ParticipantEvent]:
        raise AdapterError(_DDS_MODULE_INACTIVE_MSG)

    def topic_metrics(
        self, topic: str, window_seconds: int = 60, domain_id: int = 0
    ) -> TopicMetrics:
        raise AdapterError(_DDS_MODULE_INACTIVE_MSG)

    # ----------------------------- topics --------------------------------

    def list_topics(self) -> list[TopicInfo]:
        out = self._run([self._exe, "topic", "list", "-t"])
        topics: list[TopicInfo] = []
        for name, msg_type in parse_topic_list(out):
            pub_count, sub_count = self._safe_counts(name)
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

    def sample_messages(self, topic: str, count: int) -> list[MessageSample]:
        # `ros2 topic echo` blocks indefinitely; --once + bounded timeout is the
        # safe MVP shape. Richer windowed sampling is a roadmap item.
        #
        # We invoke `--csv --once`: ros2cli's `message_to_csv` flattens the
        # message in declaration order, so for any `Header`-stamped message
        # the first two columns are `header.stamp.sec` and
        # `header.stamp.nanosec`. That gives a real publish-time timestamp
        # without depending on rclpy. Headerless messages (e.g.
        # `std_msgs/String`, `geometry_msgs/Twist`) have no embedded
        # timestamp and the parser returns 0 for those rows — documented in
        # the `MessageSample.timestamp_ns` schema.
        # TODO(roadmap): rclpy-backed adapter — windowed echo, time-range,
        # access to rmw receive timestamps (vs publish-time from Header),
        # better deserialization of complex message payloads.
        if count <= 0:
            return []

        info = self.get_topic_info(topic)
        try:
            out = self._run(
                [self._exe, "topic", "echo", "--csv", "--once", topic],
                timeout=_SAMPLE_TIMEOUT_SEC,
            )
        except AdapterError as exc:
            log.info("sample_messages on %s returned no data: %s", topic, exc)
            return []

        rows = parse_csv_echo(out)
        return [
            MessageSample(
                topic=topic,
                message_type=info.message_type,
                timestamp_ns=ts_ns,
                payload=payload,
            )
            for ts_ns, payload in rows
        ]

    # ------------------------------ bag ----------------------------------

    def analyze_bag(self, path: str) -> BagAnalysis:
        bag_path = Path(path)
        if not bag_path.exists():
            raise AdapterError(f"Bag path does not exist: {path}")

        out = self._run([self._exe, "bag", "info", str(bag_path)])
        return parse_bag_info(out, fallback_path=str(bag_path), mode_effective=self.effective_mode)

    # ---------------------------- internals ------------------------------

    def _safe_counts(self, topic: str) -> tuple[int, int]:
        """Return (pub_count, sub_count) for a topic, defaulting to (0, 0).

        Failing to fetch counts for a single topic must not break `list_topics`.
        """
        try:
            text = self._run([self._exe, "topic", "info", topic])
        except AdapterError:
            return (0, 0)
        return parse_pub_sub_counts(text)

    def _run(self, cmd: list[str], timeout: float = _DEFAULT_TIMEOUT_SEC) -> str:
        # Resolve the executable to a full path so Windows .cmd/.bat shims work
        # without shell=True.
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


# ---------------------------------------------------------------------------
# Pure parsers — unit-testable without ROS2 present.
# ---------------------------------------------------------------------------

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
    """Parse `ros2 topic info <topic> --verbose` output into a TopicInfo.

    Returns None if no message type was found, which the adapter treats as
    "topic not found".

    `mode_effective` is kwarg-only and injected by the adapter (not parsed
    from the CLI output) — same pattern as `fallback_name`.
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
    return TopicInfo(
        name=fallback_name,
        message_type=msg_type,
        publisher_count=pub,
        subscriber_count=sub,
        mode_effective=mode_effective,
    )


# Status, post-v0.1.2: this parser is **no longer called by the live
# adapter** — `sample_messages` switched to `parse_csv_echo` against
# `ros2 topic echo --csv --once`, which exposes Header timestamps cleanly
# (see `parse_csv_echo` below). `parse_echo_yaml` is kept for two reasons:
#   1. Its test coverage in `tests/test_live_adapter_parse.py` documents
#      the YAML-ish shape ROS2's plain echo produces — useful reference if
#      we ever need to fall back from CSV.
#   2. An rclpy-backed adapter will eventually return native typed payloads
#      and obsolete both parsers (see `docs/product-plan.md` Phase 1).
# Removal is a v0.3+ candidate ; the audit's recommendation to "either
# remove it or document why it stays" is satisfied by this comment.
def parse_echo_yaml(stdout: str) -> dict[str, object]:
    """Best-effort parse of `ros2 topic echo --once` YAML-ish output.

    We deliberately avoid a hard YAML dependency for the MVP — instead we emit
    a flat `{key: value}` dict (top-level keys only) plus the raw text under
    a reserved `_raw_text` key. LLMs can still reason over the raw text, and
    downstream tools can upgrade this parser without changing the contract.

    `_raw_text` is intentionally not dunder-named (no leading/trailing `__`):
    a dunder key signals Python special-attribute semantics, which this is
    not. A single leading underscore is enough to flag it as parser metadata
    while keeping it a plain dict key.
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


# Plausible bounds for a ROS2 Header timestamp in `sec, nanosec` form.
# `sec` is seconds since the epoch; we accept anything from year 2000 to
# year 2100 as "clearly an epoch second". `nanosec` is the sub-second
# remainder and is strictly < 1e9. These bounds let the parser decide
# whether the leading two CSV columns look like a real timestamp or are
# just the first two scalar fields of a headerless message.
_TS_SEC_MIN = 946_684_800  # 2000-01-01 UTC
_TS_SEC_MAX = 4_102_444_800  # 2100-01-01 UTC
_TS_NSEC_MAX = 1_000_000_000


def parse_csv_echo(stdout: str) -> list[tuple[int, dict[str, object]]]:
    """Parse `ros2 topic echo --csv [--once]` output.

    Returns a list of `(timestamp_ns, payload)` tuples — one per CSV row.

    `ros2cli`'s `message_to_csv` flattens the message in declaration order,
    so for any message whose first field is a `std_msgs/Header` the first
    two columns are `header.stamp.sec` and `header.stamp.nanosec`. We
    detect that shape by checking whether the leading two columns parse as
    a plausible epoch-second / nanosec pair (`2000-01-01` ≤ sec <
    `2100-01-01`, `0` ≤ nanosec < `1e9`). When they do, `timestamp_ns` is
    `sec * 1_000_000_000 + nanosec` and those two columns are dropped from
    the payload. When they don't, the row is a headerless message and
    `timestamp_ns` is 0 (documented behavior in
    `MessageSample.timestamp_ns`).

    Tolerant to blank lines and comment lines starting with `#` — both are
    skipped. Rows with fewer than two columns are skipped silently (no
    raise), matching the convention of the other parsers in this module:
    a malformed CLI artifact must not break the whole sample call.

    Example input (`sensor_msgs/Imu`, single row):
        1715600000,123456789,base_link,0.0,0.0,0.0,1.0, ...
    -> `[(1715600000123456789, {"col_0": "base_link", "col_1": "0.0", ...,
                                "_raw_text": "..."})]`

    The post-strip payload re-indexes from `col_0` after the two timestamp
    columns are removed — see `tests/test_live_adapter_parse.py`.
    """
    rows: list[tuple[int, dict[str, object]]] = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            # Single-column rows can't carry a Header timestamp and aren't
            # useful payloads either — skip rather than emit a degenerate
            # sample.
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

        payload: dict[str, object] = {f"col_{i}": value for i, value in enumerate(value_parts)}
        payload["_raw_text"] = line
        rows.append((ts_ns, payload))
    return rows


def parse_bag_info(
    stdout: str, *, fallback_path: str, mode_effective: EffectiveMode
) -> BagAnalysis:
    """Parse `ros2 bag info <path>` text output into a BagAnalysis.

    `mode_effective` is kwarg-only and injected by the adapter (not parsed
    from the CLI output) — same pattern as `fallback_path`.
    """
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
