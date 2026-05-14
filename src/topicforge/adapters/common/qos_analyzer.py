"""Pure QoS-mismatch analyzer — no DDS dependency.

Compares a reader QoS profile against a writer QoS profile and surfaces
the policies that block (or risk degrading) communication. Testable
against synthesized `QosProfile` pairs without any DDS middleware
installed — same convention as the live-adapter pure parsers.

The MVP covers four policies (Reliability, Durability, History, Deadline)
that explain the bulk of "subscriber doesn't receive" diagnostics in
real-world stacks. Vendor extensions are deliberately out of scope ;
add them through this module, not through adapter-specific patches.
"""

from __future__ import annotations

from typing import Literal

from topicforge.models import QosProfile

# Durability is totally ordered. Higher index = stronger guarantee.
# A reader cannot demand a stronger guarantee than the writer provides.
_DURABILITY_ORDER: tuple[str, ...] = (
    "VOLATILE",
    "TRANSIENT_LOCAL",
    "TRANSIENT",
    "PERSISTENT",
)


def detect_mismatches(
    reader_qos: QosProfile, writer_qos: QosProfile
) -> tuple[list[str], Literal["incompatible", "risky"]] | None:
    """Compare reader and writer QoS profiles.

    Returns `None` if the two profiles are fully compatible.

    Otherwise returns `(incompatible_policies, severity)` where:
      * `incompatible_policies` is the list of QoS policy names that
        prevent or threaten communication. Names are spec-canonical
        (e.g. `"Reliability"`, `"Durability"`).
      * `severity` is `"incompatible"` if at least one policy strictly
        blocks communication per the DDS spec ; `"risky"` if the
        profiles can theoretically communicate but with degradation
        (e.g. a `KEEP_ALL` reader paired with a `KEEP_LAST` writer
        may drop history under load).

    Pure function: no I/O, no side effects, deterministic.
    """
    incompatible: list[str] = []
    risky: list[str] = []

    # Reliability — RELIABLE reader cannot match BEST_EFFORT writer.
    # The reverse is compatible (a BEST_EFFORT reader accepts what
    # arrives from any writer).
    if reader_qos.reliability == "RELIABLE" and writer_qos.reliability == "BEST_EFFORT":
        incompatible.append("Reliability")

    # Durability — reader cannot demand a stronger guarantee than the writer.
    reader_rank = _DURABILITY_ORDER.index(reader_qos.durability)
    writer_rank = _DURABILITY_ORDER.index(writer_qos.durability)
    if reader_rank > writer_rank:
        incompatible.append("Durability")

    # History — KEEP_ALL reader paired with KEEP_LAST writer is risky.
    # The writer may drop samples under load that the reader expects to
    # retain. Not strictly blocked by the spec, but worth flagging.
    if reader_qos.history == "KEEP_ALL" and writer_qos.history == "KEEP_LAST":
        risky.append("History")

    # Deadline — a reader deadline strictly tighter than the writer
    # deadline is incompatible: the writer cannot honor the reader's
    # promise. If either side has no deadline (None), no constraint
    # applies on that side.
    if (
        reader_qos.deadline_ns is not None
        and writer_qos.deadline_ns is not None
        and reader_qos.deadline_ns < writer_qos.deadline_ns
    ):
        incompatible.append("Deadline")

    if not incompatible and not risky:
        return None

    policies = incompatible + risky
    severity: Literal["incompatible", "risky"] = "incompatible" if incompatible else "risky"
    return policies, severity
