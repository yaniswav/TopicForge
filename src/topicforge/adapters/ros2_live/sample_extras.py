"""Summary and rate for `sample_messages`, built from what the echo stream collected.

Kept apart from the adapter so the adapter stays short. `topicforge.services`
is imported inside the functions: loading it imports the adapters.
"""

from __future__ import annotations

from topicforge.adapters.ros2_live.echo_parser import EchoMessage, cut_payload, parse_echo_document
from topicforge.adapters.ros2_live.echo_stream import EchoRun
from topicforge.constants import TRUNCATED_FIELDS_KEY
from topicforge.models.summaries import MessageSummary, TopicRate

# Character cap of one message while it streams in whole-array mode. A scan of
# 100,000 beams prints about 3 MiB; the returned payload is cut afterwards, so
# the size cap on what is returned (`apply_sample_budget`) still applies.
WHOLE_ARRAY_STREAM_MAX_CHARS = 4 * 1024 * 1024


def wants_whole_arrays(
    message_type: str, max_array_length: int | None, arrays_summary_only: bool
) -> bool:
    """Whether to stream `message_type` uncut and cut the payload ourselves.

    True when the summary of the type reads an array, the caller asked for a cut
    and for array contents. With `max_array_length` null the stream is uncut
    anyway; with `arrays_summary_only` there is no array to read.
    """
    from topicforge.services.summaries import needs_whole_arrays

    return (
        needs_whole_arrays(message_type)
        and max_array_length is not None
        and not arrays_summary_only
    )


def decode_with_summary(
    text: str, message_type: str, *, max_array_length: int | None, whole_arrays: bool
) -> tuple[EchoMessage, MessageSummary | None]:
    """Decode one echo document; the summary is computed before any cut of the payload.

    With `whole_arrays` the document is uncut: the summary reads it whole, then the
    returned payload is cut to `max_array_length` as the CLI would have done.
    """
    from topicforge.services.summaries import summarize_message

    if whole_arrays:
        whole = parse_echo_document(text, truncate_length=None)
        summary = summarize_message(message_type, whole.payload)
        cut = cut_payload(whole.payload, max_array_length)
        return EchoMessage(cut, whole.timestamp_ns, whole.stamp_source), summary
    message = parse_echo_document(text, truncate_length=max_array_length)
    cut_fields = message.payload.get(TRUNCATED_FIELDS_KEY)
    summary = summarize_message(
        message_type, message.payload, cut_fields if isinstance(cut_fields, list) else ()
    )
    return message, summary


def rate_of_run(run: EchoRun, count: int, stamps_ns: list[int] | None) -> TopicRate:
    """Rate block of a streaming run, on the wall time each message was printed.

    Messages dropped for size count: they arrived. The trailing gap is measured
    only when collection ended on the deadline and not on `count`.
    """
    from topicforge.services.summaries import compute_rate

    arrivals = [d.received_ns for d in run.documents] + run.oversized_received_ns
    return compute_rate(
        arrivals,
        basis="received_ns",
        stamps_ns=stamps_ns,
        window_start_ns=run.started_ns or None,
        window_end_ns=run.ended_ns or None,
        stopped_on_deadline=len(run.documents) < count and run.exit_code is None,
    )
