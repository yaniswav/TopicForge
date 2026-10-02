"""Size budget for sampled messages: bounded tool output, with a note for what was dropped."""

from __future__ import annotations

from topicforge.constants import MAX_SAMPLE_CALL_FACTOR
from topicforge.models import MessageSample

_MIB = 1024 * 1024


def sample_size_bytes(sample: MessageSample) -> int:
    """Serialized (JSON, UTF-8) size of one sample."""
    return len(sample.model_dump_json().encode("utf-8"))


def _fmt(size: int) -> str:
    return f"{size / _MIB:.1f} MiB"


def apply_sample_budget(
    samples: list[MessageSample], per_message_bytes: int
) -> tuple[list[MessageSample], list[str]]:
    """Keep the samples that fit the budget, and notes on what was dropped.

    A sample over `per_message_bytes` is dropped; once the kept samples reach
    `per_message_bytes * MAX_SAMPLE_CALL_FACTOR` the rest are dropped too.
    """
    call_limit = per_message_bytes * MAX_SAMPLE_CALL_FACTOR
    kept: list[MessageSample] = []
    notes: list[str] = []
    total = 0
    too_big: list[int] = []
    over_call = 0
    for sample in samples:
        size = sample_size_bytes(sample)
        if size > per_message_bytes:
            too_big.append(size)
        elif total + size > call_limit:
            over_call += 1
        else:
            kept.append(sample)
            total += size
    if too_big:
        notes.append(
            f"{len(too_big)} message(s) dropped: the largest is {_fmt(max(too_big))}, over the "
            f"{_fmt(per_message_bytes)} per-message cap. Lower `max_array_length` or set "
            "`arrays_summary_only`."
        )
    if over_call:
        notes.append(
            f"{over_call} message(s) dropped: the {_fmt(call_limit)} per-call cap was reached."
        )
    return kept, notes
