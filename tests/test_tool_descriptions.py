"""Tool and field descriptions state present behaviour only (docs/CONTRACT.md section 1.7).

Descriptions are read by LLM clients. History words ("v0.4", "Phase 1", "since 0.5",
"ceiling", "TODO") age badly and mislead, and a tool that can block must say how long
it can take.
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest
from mcp.types import Tool

from topicforge.config import Settings
from topicforge.server import build_app

FORBIDDEN = re.compile(r"v0\.\d|Phase|ceiling|TODO|since 0\.", re.IGNORECASE)

# Tools that take a lane lock and so can wait: they must state the 45 s bound.
LOCKED_TOOLS = {
    "list_topics",
    "get_topic_info",
    "sample_messages",
    "analyze_bag",
    "list_participants",
    "detect_qos_mismatches",
    "peek_dds_samples",
    "participant_events",
    "topic_metrics",
    "list_endpoints",
}
ROS_TOOLS = {"list_topics", "get_topic_info", "sample_messages", "analyze_bag"}


@pytest.fixture(scope="module")
def tools() -> dict[str, Tool]:
    app = build_app(
        Settings(mode="mock", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False)
    )
    return {t.name: t for t in asyncio.run(app.list_tools())}


def _descriptions(node: object, where: str) -> list[tuple[str, str]]:
    """Every `description` string in a JSON schema, with its location."""
    found: list[tuple[str, str]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "description" and isinstance(value, str):
                found.append((where, value))
            else:
                found.extend(_descriptions(value, f"{where}/{key}"))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found.extend(_descriptions(value, f"{where}[{i}]"))
    return found


def test_no_history_words_in_any_description(tools: dict[str, Tool]) -> None:
    offenders: list[str] = []
    for name, tool in tools.items():
        texts = [(name, tool.description or "")]
        texts.extend(_descriptions(tool.input_schema, f"{name}/input"))
        texts.extend(_descriptions(tool.output_schema, f"{name}/output"))
        for where, text in texts:
            match = FORBIDDEN.search(text)
            if match:
                offenders.append(f"{where}: {match.group(0)!r}")
    assert not offenders, "history words in descriptions:\n" + "\n".join(offenders)


def test_every_description_is_non_empty(tools: dict[str, Tool]) -> None:
    for name, tool in tools.items():
        assert (tool.description or "").strip(), name


@pytest.mark.parametrize("name", sorted(LOCKED_TOOLS))
def test_locked_tools_state_the_45_second_bound_and_the_lock(
    tools: dict[str, Tool], name: str
) -> None:
    text = tools[name].description or ""
    assert "45 s" in text, f"{name} does not state its worst-case duration"
    assert "`busy`" in text, f"{name} does not say what happens when the lock is taken"
    lock = "ROS lock" if name in ROS_TOOLS else "DDS lock"
    assert lock in text, f"{name} does not name its lock"


def test_sample_messages_ties_the_bound_to_timeout_s(tools: dict[str, Tool]) -> None:
    text = tools["sample_messages"].description or ""
    assert "`timeout_s`" in text and "at most 40" in text and "45 s" in text


def test_lock_free_tools_say_so(tools: dict[str, Tool]) -> None:
    assert "never waits for a lock" in (tools["health_check"].description or "")
    assert "takes no lock" in (tools["peek_bag_samples"].description or "")


def test_sample_count_description_is_coherent(tools: dict[str, Tool]) -> None:
    """E13: no duplicated word, and the maximum of 50 is stated once, as a cap."""
    schema = tools["sample_messages"].output_schema or {}
    assert not re.search(r"\bthe the\b", json.dumps(schema))
    count_desc = next(
        d for _, d in _descriptions(schema, "out") if d.startswith("Number of samples in")
    )
    assert "capped to 50" in count_desc and "without warning" not in count_desc
