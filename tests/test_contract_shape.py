"""Every tool returns exactly one object (docs/CONTRACT.md section 1.1).

Calls all fourteen tools in mock mode through the MCP layer and checks the wire shape: one
`structured_content` object (never the `{"result": [...]}` wrapper of a bare list) and one
text block carrying the same JSON.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from topicforge.config import Settings
from topicforge.server import build_app

_CALLS: dict[str, dict[str, Any]] = {
    "health_check": {},
    "list_topics": {},
    "get_topic_info": {"topic": "/scan"},
    "sample_messages": {"topic": "/scan", "count": 2},
    "analyze_bag": {"path": "/tmp/demo.mcap"},
    "list_participants": {},
    "detect_qos_mismatches": {},
    "peek_dds_samples": {"topic": "/dds/well_matched", "count": 2},
    "participant_events": {},
    "topic_metrics": {"topic": "/dds/heartbeat_10hz"},
    "peek_bag_samples": {"path": "/tmp/demo.mcap", "topic": "/odom", "count": 2},
    "list_endpoints": {},
    "list_nodes": {},
    "get_node_info": {"node": "/lidar_driver"},
}


def _app() -> Any:
    return build_app(
        Settings(mode="mock", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False)
    )


def test_every_tool_is_called_here() -> None:
    names = {t.name for t in asyncio.run(_app().list_tools())}
    assert names == set(_CALLS)


@pytest.mark.parametrize("tool", sorted(_CALLS))
def test_tool_returns_one_object_and_one_text_block(tool: str) -> None:
    result = asyncio.run(_app().call_tool(tool, _CALLS[tool]))
    structured = result.structured_content
    assert isinstance(structured, dict), f"{tool}: structured_content must be one object"
    assert "result" not in structured, f"{tool}: a bare list/scalar is wrapped as `result`"
    assert len(result.content) == 1, f"{tool}: exactly one content block"
    assert json.loads(result.content[0].text) == structured  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("tool", "list_field"),
    [
        ("list_topics", "topics"),
        ("list_participants", "participants"),
        ("participant_events", "events"),
        ("list_endpoints", "endpoints"),
    ],
)
def test_listings_carry_their_counts(tool: str, list_field: str) -> None:
    structured = asyncio.run(_app().call_tool(tool, _CALLS[tool])).structured_content
    assert structured is not None
    assert structured["returned"] == len(structured[list_field])
    assert structured["total"] >= structured["returned"]
    assert isinstance(structured["truncated"], bool)
    assert structured["mode_effective"] == "mock"
