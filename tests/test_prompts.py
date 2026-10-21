"""MCP prompts and server instructions (snapshotted in tests/contract/_prompts.json)."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from topicforge.config import Settings
from topicforge.server import build_app
from topicforge.services.health import CONTRACT_VERSION

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = {"diagnose-dds-bus", "inspect-ros2-robot", "diagnose-nav2"}
# The argument each prompt turns into a focus line.
FOCUS_ARGUMENT = {
    "diagnose-dds-bus": "topic",
    "inspect-ros2-robot": "topic",
    "diagnose-nav2": "symptom",
}
# Verbs that write to a robot: no prompt may suggest one, not even to forbid it.
FORBIDDEN_VERBS = ("service call", "lifecycle set", "action send_goal", "topic pub")


def _app() -> Any:
    return build_app(
        Settings(mode="mock", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False)
    )


def test_list_prompts_returns_all_with_optional_arguments() -> None:
    async def run() -> Any:
        async with Client(_app()) as client:
            return (await client.list_prompts()).prompts

    prompts = {p.name: p for p in asyncio.run(run())}
    assert set(prompts) == PROMPTS
    expected = {
        "diagnose-dds-bus": {"topic", "symptom"},
        "inspect-ros2-robot": {"topic", "bag_path"},
        "diagnose-nav2": {"symptom", "namespace"},
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
            focused = await client.get_prompt(name, {FOCUS_ARGUMENT[name]: "zz_focus_topic"})
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


def test_no_prompt_suggests_a_write_action() -> None:
    app = _app()

    async def run() -> list[str]:
        return [(await app.get_prompt(n, {})).messages[0].content.text for n in sorted(PROMPTS)]

    for text in asyncio.run(run()):
        lowered = text.lower()
        for verb in FORBIDDEN_VERBS:
            assert verb not in lowered


def test_nav2_prompt_follows_the_planned_order_and_names_its_blind_spots() -> None:
    text = asyncio.run(_app().get_prompt("diagnose-nav2", {})).messages[0].content.text
    steps = [
        "`health_check`",
        "`list_nodes`",
        "`lifecycle_manager_*`",
        "`/bond`",
        "_action/status",
        "`inspect_tf`",
        "`/rosout`",
        "`/cmd_vel`",
    ]
    positions = [text.index(step) for step in steps]
    assert positions == sorted(positions)
    assert "What TopicForge cannot see:" in text
    for blind_spot in ("lifecycle state", "Costmaps", "behavior tree"):
        assert blind_spot.lower() in text.lower()
    # The hidden `_action` topics are not in `list_topics` but `sample_messages` reads them.
    assert "`list_topics` hides the `_action` topics" in text


def test_nav2_skill_mirrors_the_prompt_steps() -> None:
    text = asyncio.run(_app().get_prompt("diagnose-nav2", {})).messages[0].content.text
    skill = (ROOT / "plugin" / "skills" / "diagnose-nav2" / "SKILL.md").read_text(encoding="utf-8")
    assert skill.startswith("---\nname: diagnose-nav2\n")
    steps = [line for line in text.splitlines() if re.match(r"\d\. ", line)]
    assert len(steps) == 8
    for line in [*steps, *(x for x in text.splitlines() if x.startswith("- "))]:
        assert line in skill
    for verb in FORBIDDEN_VERBS:
        assert verb not in skill.lower()


def test_instructions_are_in_the_initialize_result() -> None:
    async def run() -> str | None:
        async with Client(_app()) as client:
            return client.instructions

    instructions = asyncio.run(run())
    assert instructions is not None
    assert "read-only" in instructions
    assert "health_check" in instructions
    assert "contract_version" in instructions and str(CONTRACT_VERSION) in instructions
    for name in PROMPTS:
        assert f"`{name}`" in instructions
    assert len(instructions) < 1200
