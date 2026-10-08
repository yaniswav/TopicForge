"""Golden snapshots of the tool contract, and the generated tool reference.

`tests/contract/<tool>.json` holds each tool exactly as `list_tools` serves it in
mock mode (name, title, description, input schema, output schema, annotations).
Any difference fails the test with a readable diff. An intended change to the
contract is recorded with:

    python scripts/contract/snapshot_tools.py --update --docs

and the resulting diff is reviewed like any other API change (docs/CONTRACT.md).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "contract" / "snapshot_tools.py"
_REGEN = "python scripts/contract/snapshot_tools.py --update --docs"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("topicforge_snapshot_tools", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def snap() -> ModuleType:
    return _load_script()


@pytest.fixture(scope="module")
def tools(snap: ModuleType) -> list[dict]:
    return snap.collect_tools()


def test_served_tools_match_the_golden_snapshots(snap: ModuleType, tools: list[dict]) -> None:
    problems = snap.compare(tools)
    assert not problems, (
        "The tool contract changed. If this is intended, regenerate with "
        f"`{_REGEN}` and review the diff.\n\n" + "\n".join(problems)
    )


def test_every_snapshot_is_ascii_and_names_its_tool(snap: ModuleType) -> None:
    files = sorted(snap.SNAPSHOT_DIR.glob("*.json"))
    assert files, "no snapshots found"
    for path in files:
        raw = path.read_bytes()
        assert all(b < 128 for b in raw), f"{path.name} is not ASCII"
        assert b"\r" not in raw, f"{path.name} has CRLF line endings"
        assert f'"name": "{path.stem}"' in raw.decode("ascii"), path.name


def test_tools_doc_is_up_to_date(snap: ModuleType, tools: list[dict]) -> None:
    expected = snap.render_tools_doc(tools)
    actual = snap.TOOLS_DOC.read_text(encoding="utf-8")
    assert actual == expected, f"docs/TOOLS.md is out of date. Regenerate with `{_REGEN}`."


def test_a_changed_description_is_reported_as_a_diff(snap: ModuleType, tools: list[dict]) -> None:
    tampered = [dict(t) for t in tools]
    tampered[0]["description"] = tampered[0]["description"] + " Extra sentence."
    problems = snap.compare(tampered)
    assert len(problems) == 1
    assert "Extra sentence." in problems[0]
    assert problems[0].count("\n") > 2
