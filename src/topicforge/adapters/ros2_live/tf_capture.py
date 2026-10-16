"""Capture `/tf_static` and `/tf` through `ros2 topic echo` for `inspect_tf`.

Two streaming passes, each bounded by the call budget (`topicforge.budget`):

1. `/tf_static`: latched (transient_local, reliable), so one message per publisher is
   waiting on arrival. The pass asks for `publisher_count` messages and stops after
   `_STATIC_WAIT_S`.
2. `/tf`: listened to for the window, uncut (`--full-length`), at most `MAX_TF_DOCS`
   messages. `echo` can drop messages at high rates; the structure of the tree survives
   that, the frequencies and the exact count do not.

A topic that is absent or has no publisher becomes a note on the capture, not an
error: an empty tree is a result. When both streams fail for any other reason (CLI
failure, timeout, no budget) the capture raises `AdapterError` rather than present
an empty tree as success.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from topicforge import budget as call_budget
from topicforge.adapters.base import AdapterError
from topicforge.adapters.ros2_live.echo_stream import EchoRun, stream_echo
from topicforge.adapters.ros2_live.tf_parsers import parse_tf_message
from topicforge.models import TfCapture, TfSample, TopicInfo

# Most `/tf` messages kept in one capture.
MAX_TF_DOCS = 3000
_STATIC_WAIT_S = 3.0
# Wait on `/tf` when it has no announced publisher.
_NO_PUBLISHER_WAIT_S = 2.0
# Time kept back from the call budget for stopping the CLI (about 2 s) and decoding.
_STOP_RESERVE_S = 3.0
_PARSE_GRACE_S = 1.0
_GRAPH_QUERY_S = 8.0
_MIN_STREAM_S = 0.5

TopicInfoFn = Callable[[str, float], TopicInfo]
QosArgsFn = Callable[[TopicInfo], list[str]]


def capture_tf(
    window_s: float,
    *,
    exe: str,
    topic_info: TopicInfoFn,
    qos_args: QosArgsFn,
    max_message_chars: int,
) -> TfCapture:
    """Read `/tf_static` then `/tf` for `window_s` seconds; see the module docstring.

    `exe` is the resolved `ros2` path, `topic_info(topic, timeout)` reads one topic
    (raising `AdapterError` when it does not exist) and `qos_args(info)` gives the
    `--qos-*` flags that match its publishers.
    """
    notes: list[str] = []
    failures: list[str] = []
    static, static_dropped = _static_pass(exe, topic_info, max_message_chars, notes, failures)
    dynamic, dropped, capped, actual_s = _dynamic_pass(
        window_s, exe, topic_info, qos_args, max_message_chars, notes, failures
    )
    if len(failures) >= 2:
        raise AdapterError("Could not read the TF topics: " + " ".join(failures))
    return TfCapture(
        static=static,
        dynamic=dynamic,
        window_actual_s=actual_s,
        dropped_count=static_dropped + dropped,
        capped=capped,
        notes=notes,
    )


def _echo_cmd(exe: str, topic: str, qos: list[str]) -> list[str]:
    return [exe, "topic", "echo", "--no-lost-messages", "--full-length", *qos, topic]


def _info_or_note(
    topic: str, topic_info: TopicInfoFn, notes: list[str], failures: list[str]
) -> TopicInfo | None:
    """Topic info, or `None` with a note; a failure other than "absent" is also recorded."""
    wait = call_budget.clamp(_GRAPH_QUERY_S, _STOP_RESERVE_S)
    if wait <= 0:
        msg = f"No time was left in this call's budget to read {topic}: retry."
        notes.append(msg)
        failures.append(msg)
        return None
    try:
        return topic_info(topic, wait)
    except AdapterError as exc:
        msg = f"{topic} could not be read ({exc})."
        notes.append(msg)
        if "not found" not in str(exc).lower():
            failures.append(msg)
        return None


def _decode(run: EchoRun, parse_until: float) -> tuple[list[TfSample], int]:
    """Samples of the run's documents; the second value counts documents not decoded.

    Documents are parsed newest first, so when the grace runs out the ones dropped are
    the oldest (the newest define the reference time). Samples keep arrival order.
    """
    chunks: list[list[TfSample]] = []
    skipped = 0
    docs = run.documents
    for index in range(len(docs) - 1, -1, -1):
        if time.monotonic() > parse_until:
            skipped += index + 1
            break
        parsed = parse_tf_message(docs[index].text, docs[index].received_ns)
        if parsed is None:
            skipped += 1
        else:
            chunks.append(parsed)
    samples = [s for chunk in reversed(chunks) for s in chunk]
    return samples, skipped + run.oversized


def _static_pass(
    exe: str, topic_info: TopicInfoFn, max_chars: int, notes: list[str], failures: list[str]
) -> tuple[list[TfSample], int]:
    info = _info_or_note("/tf_static", topic_info, notes, failures)
    if info is None:
        return [], 0
    if info.publisher_count == 0:
        notes.append("/tf_static has no publisher: no static transform is known.")
        return [], 0
    wait = call_budget.clamp(_STATIC_WAIT_S, _STOP_RESERVE_S)
    if wait < _MIN_STREAM_S:
        msg = "No time was left in this call's budget to read /tf_static: retry."
        notes.append(msg)
        failures.append(msg)
        return [], 0
    qos = ["--qos-reliability", "reliable", "--qos-durability", "transient_local"]
    run = stream_echo(
        _echo_cmd(exe, "/tf_static", qos),
        count=info.publisher_count,
        deadline_s=wait,
        max_document_chars=max_chars,
    )
    samples, dropped = _decode(run, time.monotonic() + _PARSE_GRACE_S)
    got = len(run.documents) + run.oversized
    if got < info.publisher_count:
        notes.append(
            f"{got} of {info.publisher_count} /tf_static publishers delivered their latched "
            "message: some static frames may be missing."
        )
    return samples, dropped


def _dynamic_pass(
    window_s: float,
    exe: str,
    topic_info: TopicInfoFn,
    qos_args: QosArgsFn,
    max_chars: int,
    notes: list[str],
    failures: list[str],
) -> tuple[list[TfSample], int, bool, float]:
    info = _info_or_note("/tf", topic_info, notes, failures)
    if info is None:
        return [], 0, False, 0.0
    window = window_s if info.publisher_count else min(window_s, _NO_PUBLISHER_WAIT_S)
    if not info.publisher_count:
        notes.append("/tf has no publisher: no dynamic transform is being sent.")
    wait = call_budget.clamp(window, _STOP_RESERVE_S)
    if wait < _MIN_STREAM_S:
        msg = "No time was left in this call's budget to listen to /tf: retry."
        notes.append(msg)
        failures.append(msg)
        return [], 0, False, 0.0
    if wait < window - 0.05:
        notes.append(
            f"The call's time budget shortened the /tf window to {wait:.1f} s "
            f"(asked for {window:.1f} s): frequencies and staleness rest on less data."
        )
    run = stream_echo(
        _echo_cmd(exe, "/tf", qos_args(info)),
        count=MAX_TF_DOCS,
        deadline_s=wait,
        max_document_chars=max_chars,
    )
    if not run.documents and run.exit_code not in (None, 0):
        notes.append(
            f"`ros2 topic echo /tf` exited early (exit {run.exit_code}): "
            f"{run.stderr_tail or 'no stderr'}."
        )
    samples, dropped = _decode(run, time.monotonic() + _PARSE_GRACE_S)
    actual = max(run.ended_ns - run.started_ns, 0) / 1_000_000_000
    return samples, dropped, len(run.documents) >= MAX_TF_DOCS, round(actual, 3)


__all__ = ["MAX_TF_DOCS", "capture_tf"]
