"""The advertised `MessageSample.summary` is an open object; real results still validate.

Section 9.9 of docs/CONTRACT.md: the five strict summary models are not listed in the tool
schemas any more (they cost about 26 KB of `tools/list`), and `summary` is advertised as an
object with a `summary_type`. These tests pin that loosening: no `*Summary` definition is
served, and mock results validate against the served `outputSchema` with `jsonschema`, the
check an MCP client makes on `structuredContent`.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import jsonschema
import pytest

from topicforge.config import Settings
from topicforge.models.summaries import LaserScanSummary
from topicforge.server import build_app

_SUMMARY_TYPES = {
    "/scan": "laser_scan",
    "/odom": "odometry",
    "/imu/data": "imu",
    "/camera/image_raw": "image",
}


_MESSAGE_SUMMARY_MODELS = {
    "LaserScanSummary",
    "OdometrySummary",
    "ImuSummary",
    "ImageSummary",
    "PointCloud2Summary",
    "ScanReturn",
    "ScanSector",
    "ScanSectors",
    "PointFieldSummary",
}


@pytest.fixture(scope="module")
def app() -> Any:
    return build_app(
        Settings(mode="mock", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False)
    )


@pytest.fixture(scope="module")
def wires(app: Any) -> dict[str, dict[str, Any]]:
    tools = asyncio.run(app.list_tools())
    return {t.name: t.model_dump(by_alias=True, exclude_none=True) for t in tools}


def _call(app: Any, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    result = asyncio.run(app.call_tool(tool, args))
    return json.loads(json.dumps(result.structured_content))


def test_no_summary_model_is_listed_in_any_defs(wires: dict[str, dict[str, Any]]) -> None:
    for tool, wire in wires.items():
        defs = wire["outputSchema"].get("$defs", {})
        leaked = [k for k in defs if k in _MESSAGE_SUMMARY_MODELS]
        assert not leaked, f"{tool} lists {leaked}"


def test_summary_is_an_open_object_with_a_summary_type_or_null(
    wires: dict[str, dict[str, Any]],
) -> None:
    sample = wires["sample_messages"]["outputSchema"]["$defs"]["MessageSample"]
    obj = sample["properties"]["summary"]
    assert obj["type"] == ["object", "null"]  # the nullable fold of CONTRACT 9.13
    assert obj["required"] == ["summary_type"]
    assert obj["properties"]["summary_type"]["enum"] == [
        "laser_scan",
        "odometry",
        "imu",
        "image",
        "point_cloud2",
    ]


def _has_key(node: Any, key: str) -> bool:
    if isinstance(node, dict):
        return key in node or any(_has_key(v, key) for v in node.values())
    if isinstance(node, list):
        return any(_has_key(v, key) for v in node)
    return False


def test_output_schemas_carry_no_default_but_input_schemas_do(
    wires: dict[str, dict[str, Any]],
) -> None:
    assert not any(_has_key(w["outputSchema"], "default") for w in wires.values())
    assert any(_has_key(w["inputSchema"], "default") for w in wires.values())


def test_a_laser_scan_summary_validates_and_a_wrong_type_fails(
    wires: dict[str, dict[str, Any]], app: Any
) -> None:
    result = _call(app, "sample_messages", {"topic": "/scan", "count": 1})
    sample = result["samples"][0]
    assert LaserScanSummary.model_validate(sample["summary"]).summary_type == "laser_scan"
    schema = wires["sample_messages"]["outputSchema"]
    jsonschema.validate(result, schema)
    sample["summary"] = {**sample["summary"], "summary_type": "not_a_type"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(result, schema)


@pytest.mark.parametrize("topic", sorted(_SUMMARY_TYPES))
@pytest.mark.parametrize("tool", ["sample_messages", "peek_bag_samples"])
def test_mock_sample_results_validate_against_the_served_schema(
    app: Any, wires: dict[str, dict[str, Any]], tool: str, topic: str
) -> None:
    args: dict[str, Any] = {"topic": topic, "count": 2}
    if tool == "peek_bag_samples":
        args["path"] = "/tmp/demo.mcap"
    result = _call(app, tool, args)
    jsonschema.validate(result, wires[tool]["outputSchema"])
    summaries = [s["summary"] for s in result["samples"] if s.get("summary")]
    if tool == "sample_messages":  # the mock bag peeks carry summaries on some topics only
        assert summaries, f"{topic}: no summary in the mock sample"
    assert {s["summary_type"] for s in summaries} <= {_SUMMARY_TYPES[topic]}
