"""MCP prompts and server instructions (snapshotted in tests/contract/_prompts.json)."""

from __future__ import annotations

import asyncio
import re
from typing import Any

import pytest
from mcp import Client

from topicforge.config import Settings
from topicforge.server import build_app
from topicforge.services.health import CONTRACT_VERSION

PROMPTS = {"diagnose-dds-bus", "inspect-ros2-robot"}


def _app() -> Any:
    return build_app(
        Settings(mode="mock", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False)
    )


def test_list_prompts_returns_both_with_optional_arguments() -> None:
    async def run() -> Any:
        async with Client(_app()) as client:
            return (await client.list_prompts()).prompts

    prompts = {p.name: p for p in asyncio.run(run())}
    assert set(prompts) == PROMPTS
    expected = {
        "diagnose-dds-bus": {"topic", "symptom"},
        "inspect-ros2-robot": {"topic", "bag_path"},
    }
    for name, prompt in prompts.items():
        assert prompt.description
        args = {a.name: a for a in prompt.arguments or []}
        assert set(args) == expected[name]
        assert not any(a.required for a in args.values())


@pytest.mark.parametrize("name", sorted(PROMPTS))
def test_get_prompt_renders_without_and_with_arguments(name: str) -> None:
    async def run() -> tuple[str, str]:
        async with Client(_app()) as client:
            bare = await client.get_prompt(name, {})
            focused = await client.get_prompt(name, {"topic": "zz_focus_topic"})
            return bare.messages[0].content.text, focused.messages[0].content.text

    bare, focused = asyncio.run(run())
    assert "health_check" in bare and "{focus}" not in bare
    assert "zz_focus_topic" in focused and "zz_focus_topic" not in bare
    assert len(focused) > len(bare)


def test_prompts_name_only_real_tools_and_stay_read_only() -> None:
    app = _app()
    tools = {t.name for t in asyncio.run(app.list_tools())}

    async def run() -> list[str]:
        return [(await app.get_prompt(n, {})).messages[0].content.text for n in sorted(PROMPTS)]

    for text in asyncio.run(run()):
        named = {w for w in re.findall(r"`([a-z_]+)`", text) if w in tools or w.endswith("_bag")}
        assert named <= tools
        assert "nothing here publishes" in text or "they only read" in text.lower()
        assert text.isascii()


def test_instructions_are_in_the_initialize_result() -> None:
    async def run() -> str | None:
        async with Client(_app()) as client:
            return client.instructions

    instructions = asyncio.run(run())
    assert instructions is not None
    assert "read-only" in instructions
    assert "health_check" in instructions
    assert "contract_version" in instructions and str(CONTRACT_VERSION) in instructions
    assert len(instructions) < 1200
