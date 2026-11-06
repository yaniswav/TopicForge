"""Result model of `diagnose`: ranked findings from one bounded sweep of the ROS 2 graph and DDS bus.

Every finding names the check that raised it, a severity from the table in docs/CONTRACT.md
section 9.21, the evidence behind it (tool, field, value) and, for QoS findings, the fixes
`detect_qos_mismatches` proposes. A check that could not run is listed in `checks_skipped`
with its reason: it never turns into a tool error and never into silence.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from topicforge.models.schemas import SuggestedFix

_CONFIG = ConfigDict(extra="forbid", frozen=True)

Severity = Literal["critical", "warning", "info"]
"""Finding severity; also the sort order of `Diagnosis.findings`."""

CheckName = Literal["environment", "nodes", "topics", "dds", "tf", "logs", "responsiveness"]
"""Checks in execution order."""

SEVERITIES: tuple[str, ...] = ("critical", "warning", "info")
CHECKS: tuple[str, ...] = (
    "environment",
    "nodes",
    "topics",
    "dds",
    "tf",
    "logs",
    "responsiveness",
)

EVIDENCE_VALUE_MAX = 200
"""Characters of an evidence value; longer text is cut."""

MAX_FINDINGS = 50
"""Findings returned; `findings_total` keeps the full count."""


class Evidence(BaseModel):
    """One fact behind a finding, readable again with the named tool."""

    model_config = _CONFIG

    tool: str = Field()
    field: str = Field(description="Field of that tool's result.")
    value: str = Field(max_length=EVIDENCE_VALUE_MAX)


class Finding(BaseModel):
    """One thing the sweep found."""

    model_config = _CONFIG

    severity: Severity = Field()
    check: CheckName = Field()
    subject: str | None = Field(default=None, description="Topic, node or frame.")
    text: str = Field()
    evidence: list[Evidence] = Field()
    suggested_fixes: list[SuggestedFix] = Field(
        description="QoS changes or read-only commands; text for a human, never applied."
    )


class SkippedCheck(BaseModel):
    """A check that did not run, or only in part, and why."""

    model_config = _CONFIG

    check: str = Field()
    reason: str = Field(description="`budget`, or the error or missing backend.")


class Diagnosis(BaseModel):
    """Envelope returned by `diagnose`."""

    model_config = _CONFIG

    findings: list[Finding] = Field(description="Critical first; at most 50.")
    findings_total: int = Field()
    critical_count: int = Field(description="Counts cover all findings, not the capped list.")
    warning_count: int = Field()
    info_count: int = Field()
    checks_run: list[str] = Field()
    checks_skipped: list[SkippedCheck] = Field(description="Not verified.")
    budget_s: float = Field()
    elapsed_s: float = Field()
    verdict: str = Field(description="One line; never an all-clear after a skipped check.")
    mode_effective: Literal["mock", "live"] = Field(description="`live` or `mock` (fixtures).")
    note: str | None = Field(default=None, description="One sentence of context, or `None`.")


__all__ = [
    "CHECKS",
    "EVIDENCE_VALUE_MAX",
    "MAX_FINDINGS",
    "SEVERITIES",
    "CheckName",
    "Diagnosis",
    "Evidence",
    "Finding",
    "Severity",
    "SkippedCheck",
]
