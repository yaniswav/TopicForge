"""Every mock tool result validates against the `outputSchema` the server serves.

`tools/list` is compacted (generated titles, defaults and `anyOf: [X, null]` folds), and a
compaction that loosened or broke a schema would only show on a client that validates
`structuredContent`. This is the net under those levers: each tool is called in mock mode
through the MCP layer and its structured result is checked against the served schema with
`jsonschema`. The fold unit tests pin its rules (docs/CONTRACT.md section 9.13).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import jsonschema
import pytest

from topicforge.config import Settings
from topicforge.server import build_app
from topicforge.tools.schema_compact import fold_nullable

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
    "inspect_tf": {},
    "get_node_info": {"node": "/lidar_driver"},
    "graph_diff": {},
}


@pytest.fixture(scope="module")
def app() -> Any:
    return build_app(
        Settings(mode="mock", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False)
    )


@pytest.fixture(scope="module")
def schemas(app: Any) -> dict[str, dict[str, Any]]:
    return {t.name: t.output_schema for t in asyncio.run(app.list_tools())}


def test_every_tool_is_covered(schemas: dict[str, dict[str, Any]]) -> None:
    assert set(schemas) == set(_CALLS)


@pytest.mark.parametrize("tool", sorted(_CALLS))
def test_the_mock_result_validates_against_the_served_output_schema(
    app: Any, schemas: dict[str, dict[str, Any]], tool: str
) -> None:
    schema = schemas[tool]
    jsonschema.Draft202012Validator.check_schema(schema)
    result = asyncio.run(app.call_tool(tool, _CALLS[tool])).structured_content
    # What a client receives is JSON text: validate that, not the in-process object.
    wire = json.loads(json.dumps(result))
    jsonschema.Draft202012Validator(schema).validate(wire)


def test_nullable_scalars_are_folded_in_output_schemas_only(app: Any) -> None:
    tools = {t.name: t for t in asyncio.run(app.list_tools())}
    note = tools["list_topics"].output_schema["properties"]["note"]
    assert note["type"] == ["string", "null"] and "anyOf" not in note
    bag_path = tools["graph_diff"].input_schema["properties"]["bag_path"]
    assert bag_path["anyOf"] == [{"type": "string"}, {"type": "null"}]


def test_a_folded_enum_still_accepts_null() -> None:
    schema = {
        "properties": {
            "level": {"anyOf": [{"enum": ["a", "b"], "type": "string"}, {"type": "null"}]}
        }
    }
    fold_nullable(schema)
    level = schema["properties"]["level"]
    assert level == {"type": ["string", "null"], "enum": ["a", "b", None]}
    validator = jsonschema.Draft202012Validator(level)
    assert validator.is_valid(None) and validator.is_valid("a") and not validator.is_valid("c")


def test_a_ref_or_a_nested_combinator_is_not_folded() -> None:
    ref = {"properties": {"x": {"anyOf": [{"$ref": "#/$defs/X"}, {"type": "null"}]}}}
    fold_nullable(ref)
    assert "anyOf" in ref["properties"]["x"]
    three = {
        "properties": {"x": {"anyOf": [{"type": "string"}, {"type": "integer"}, {"type": "null"}]}}
    }
    fold_nullable(three)
    assert "anyOf" in three["properties"]["x"]


def test_a_property_named_any_of_is_left_alone() -> None:
    schema = {"properties": {"anyOf": {"type": "string"}}}
    fold_nullable(schema)
    assert schema == {"properties": {"anyOf": {"type": "string"}}}


def test_siblings_of_the_any_of_are_kept() -> None:
    schema = {
        "description": "d",
        "anyOf": [{"type": "integer", "minimum": 0}, {"type": "null"}],
    }
    fold_nullable(schema)
    assert schema == {"description": "d", "type": ["integer", "null"], "minimum": 0}
