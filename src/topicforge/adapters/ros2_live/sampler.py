"""`sample_messages` for the live adapter: stream `ros2 topic echo` and decode it.

The function is wired like `tf_capture`: the adapter hands in what it owns (the topic
lookup, the `ros2` resolver, the mode label and the message size cap), so this module
starts no process except through `stream_echo` and never imports the adapter.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from topicforge import budget as call_budget
from topicforge.adapters.base import AdapterError, EffectiveMode
from topicforge.adapters.common.log_filter import (
    LOG_READER_DEPTH,
    LOG_STREAM_CAP,
    LogFilter,
    is_log_type,
    newest_matches,
    require_log_topic,
)
from topicforge.adapters.ros2_live.echo_stream import EchoRun, stream_echo
from topicforge.adapters.ros2_live.sample_extras import (
    WHOLE_ARRAY_STREAM_MAX_CHARS,
    decode_with_summary,
    rate_of_run,
    wants_whole_arrays,
)
from topicforge.constants import (
    DEFAULT_MAX_ARRAY_LENGTH,
    DEFAULT_SAMPLE_TIMEOUT_S,
    ROS2_CLI_TIMEOUT_S,
)
from topicforge.models import MessageSample, SampleResult, TopicInfo, TopicRate

# Wait for `sample_messages` on a topic with no announced publisher.
_NO_PUBLISHER_WAIT_SEC = 3.0
# `sample_messages` returns within about `timeout_s` plus this: stopping the CLI
# takes up to ~2 s, and decoding may run until 1 s past the deadline.
_PARSE_GRACE_SEC = 1.0
_MIN_ECHO_SEC = 0.5
# Share of a filtered `/rosout` read kept back from streaming, for decoding up to 2000 documents.
_FILTER_DECODE_RESERVE_SEC = 0.5
# Time `sample_messages` keeps back from the call budget for stopping the CLI
# (about 2 s) and decoding (until 1 s past the deadline).
_STOP_RESERVE_SEC = 3.0

TopicInfoFn = Callable[[str, float], TopicInfo]
ResolveExeFn = Callable[[], str]


def sample_messages(
    topic: str,
    count: int,
    *,
    topic_info: TopicInfoFn,
    resolve_exe: ResolveExeFn,
    mode: EffectiveMode,
    max_message_chars: int,
    max_array_length: int | None = DEFAULT_MAX_ARRAY_LENGTH,
    arrays_summary_only: bool = False,
    timeout_s: float = DEFAULT_SAMPLE_TIMEOUT_S,
    min_log_level: str | None = None,
    node: str | None = None,
) -> SampleResult:
    """Stream `ros2 topic echo` and keep up to `count` messages within `timeout_s`.

    `topic_info(topic, timeout)` reads one topic (raising `AdapterError` when it does
    not exist), `resolve_exe()` gives the resolved `ros2` path (raising `AdapterError`
    when it is not on PATH), `mode` labels the result and `max_message_chars` is the
    longest echo document kept.

    On a `rcl_interfaces/msg/Log` topic the reader asks for depth 2000, to receive
    the transient_local buffer of `/rosout`. With `min_log_level` or `node` the
    stream runs until `timeout_s` (at most 2000 messages), filters after decoding and
    returns the NEWEST `count` matches in stamp order, with a note.

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
    info = topic_info(topic, min(ROS2_CLI_TIMEOUT_S, timeout_s))
    log_filter = LogFilter.of(min_log_level, node)
    if log_filter is not None:
        require_log_topic(topic, info.message_type)
    if count <= 0:
        return _sample_result(mode, topic, [], None)

    has_publisher = info.publisher_count > 0
    budget = timeout_s if has_publisher else min(timeout_s, _NO_PUBLISHER_WAIT_SEC)
    remaining = max(budget - (time.monotonic() - started), _MIN_ECHO_SEC)
    # A summary that reads an array (a scan's ranges) needs it whole: stream
    # uncut, summarize, then cut the returned payload as the CLI would have.
    whole = wants_whole_arrays(info.message_type, max_array_length, arrays_summary_only)
    cmd = _echo_command(
        resolve_exe(),
        topic,
        info,
        max_array_length=None if whole else max_array_length,
        arrays_summary_only=arrays_summary_only,
        deep_history=log_filter is not None,
    )
    if log_filter is not None:
        # Stream until the deadline, leaving the decode its share of the budget.
        remaining = max(remaining - _FILTER_DECODE_RESERVE_SEC, _MIN_ECHO_SEC)
    run = stream_echo(
        cmd,
        count=count if log_filter is None else LOG_STREAM_CAP,
        deadline_s=remaining,
        max_document_chars=(
            max(max_message_chars, WHOLE_ARRAY_STREAM_MAX_CHARS) if whole else max_message_chars
        ),
    )
    if not run.documents and run.exit_code not in (None, 0):
        raise AdapterError(
            f"`ros2 topic echo {topic}` failed (exit {run.exit_code}): "
            f"{run.stderr_tail or 'no stderr'}"
        )

    parse_until = started + budget + _PARSE_GRACE_SEC
    samples, skipped = _decode(run, topic, info, max_array_length, whole, parse_until)
    if log_filter is not None:
        return _filtered_log_result(
            mode,
            topic,
            run,
            samples,
            skipped,
            count,
            log_filter,
            budget,
            has_publisher,
            max_message_chars,
        )
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
            _dropped_note(run.oversized, skipped, max_message_chars),
        )
        if part
    )
    return _sample_result(mode, topic, samples, note or None, _rate(run, count, samples, skipped))


def _filtered_log_result(
    mode: EffectiveMode,
    topic: str,
    run: EchoRun,
    samples: list[MessageSample],
    skipped: int,
    count: int,
    log_filter: LogFilter,
    budget: float,
    has_publisher: bool,
    max_message_chars: int,
) -> SampleResult:
    """Result of a filtered `/rosout` read: the newest `count` matches and a note."""
    if not run.documents:
        return _sample_result(
            mode, topic, [], _empty_log_note(run, budget, has_publisher, log_filter), None
        )
    kept, note = newest_matches(
        samples, count, log_filter, cap_hit=len(run.documents) >= LOG_STREAM_CAP
    )
    extras = [_dropped_note(run.oversized, skipped, max_message_chars)]
    return _sample_result(
        mode, topic, kept, " ".join(p for p in (note, *extras) if p) or None, None
    )


def _decode(
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
    mode: EffectiveMode,
    topic: str,
    samples: list[MessageSample],
    note: str | None,
    rate: TopicRate | None = None,
) -> SampleResult:
    return SampleResult(
        topic=topic,
        count=len(samples),
        samples=samples,
        mode_effective=mode,
        note=note,
        rate=rate,
    )


def _echo_command(
    exe: str,
    topic: str,
    info: TopicInfo,
    *,
    max_array_length: int | None,
    arrays_summary_only: bool,
    deep_history: bool = False,
) -> list[str]:
    return [
        exe,
        "topic",
        "echo",
        "--no-lost-messages",
        *echo_qos_args(info),
        *_echo_depth_args(info, deep_history),
        *_echo_array_args(max_array_length, arrays_summary_only),
        topic,
    ]


def _rate(run: EchoRun, count: int, samples: list[MessageSample], skipped: int) -> TopicRate:
    """Rate block of the run; the sim rate needs every message decoded and stamped."""
    stamped = all(s.stamp_source in ("header", "payload") for s in samples)
    complete = not run.oversized and not skipped
    stamps = [s.timestamp_ns for s in samples] if samples and stamped and complete else None
    clock = [(s.received_ns, s.timestamp_ns, s.stamp_source) for s in samples if s.received_ns]
    return rate_of_run(run, count, stamps, clock)


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


def echo_qos_args(info: TopicInfo) -> list[str]:
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


def _echo_depth_args(info: TopicInfo, deep_history: bool = False) -> list[str]:
    """Deep `keep_last` history on a filtered Log read, to receive the `/rosout` buffer on connect.

    Only with a log filter: an unfiltered read of `/rosout` keeps the CLI default depth.
    """
    if not deep_history or not is_log_type(info.message_type):
        return []
    return ["--qos-history", "keep_last", "--qos-depth", str(LOG_READER_DEPTH)]


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


def _empty_log_note(
    run: EchoRun, budget_s: float, has_publisher: bool, log_filter: LogFilter
) -> str:
    """Why a filtered `/rosout` read returned nothing when no log arrived at all.

    The filter never ran, so the no-match advice (logger names, a lower level) and the
    latched-topic advice (`count` 1) would both mislead here.
    """
    if run.exit_code is not None:
        tail = f": {run.stderr_tail}" if run.stderr_tail else ""
        return f"No log read: the `ros2` CLI exited early (exit {run.exit_code}){tail}."
    if not has_publisher:
        return "No log read: no node publishes on this topic."
    return (
        f"No log message arrived within {budget_s:g} s, so {log_filter} had nothing to test: "
        "no node logged in the last 10 s (the `/rosout` buffer) or during the wait. A larger "
        "`timeout_s` waits for new logs."
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
