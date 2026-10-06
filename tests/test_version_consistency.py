"""Fail when the version strings scattered across the repo drift apart.

One release touches the package, the registry manifest, the Claude plugin, the
Gemini extension, the MCP Bundle and the pins copied into the docs. Forgetting
one of them ships a client config that installs the wrong version.
"""

from __future__ import annotations

import base64
import json
import re
import urllib.parse
from pathlib import Path

import pytest

import topicforge

ROOT = Path(__file__).resolve().parent.parent
PIN_RE = re.compile(r"topicforge\[dds\]==([0-9][^\s\"'\\)&%]*)")


def _json(rel: str) -> dict:
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def _pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match, "no version in pyproject.toml"
    return match.group(1)


def _pin(args: list[str]) -> str:
    match = PIN_RE.fullmatch(args[args.index("--from") + 1])
    assert match, args
    return match.group(1)


def test_package_versions_agree() -> None:
    assert topicforge.__version__ == _pyproject_version()


def _versions() -> dict[str, str]:
    server = _json("server.json")
    found = {
        "server.json": server["version"],
        "server.json packages[0]": server["packages"][0]["version"],
        "plugin.json": _json("plugin/.claude-plugin/plugin.json")["version"],
        "plugin .mcp.json pin": _pin(_json("plugin/.mcp.json")["mcpServers"]["topicforge"]["args"]),
        "gemini-extension.json": _json("gemini-extension.json")["version"],
        "gemini-extension.json pin": _pin(
            _json("gemini-extension.json")["mcpServers"]["topicforge"]["args"]
        ),
        "mcpb manifest": _json("mcpb/manifest.json")["version"],
    }
    mcpb_pyproject = (ROOT / "mcpb" / "pyproject.toml").read_text(encoding="utf-8")
    found["mcpb pyproject version"] = re.search(
        r'^version\s*=\s*"([^"]+)"', mcpb_pyproject, re.MULTILINE
    ).group(1)
    found["mcpb pyproject pin"] = PIN_RE.search(mcpb_pyproject).group(1)
    return found


@pytest.mark.parametrize("name,value", sorted(_versions().items()))
def test_version_matches_package(name: str, value: str) -> None:
    assert value == topicforge.__version__, f"{name} drifted from the package version"


def test_clients_doc_pins_match_package() -> None:
    text = (ROOT / "docs" / "CLIENTS.md").read_text(encoding="utf-8")
    pins = set(PIN_RE.findall(text)) | set(re.findall(r"==(\d+\.\d+\.\d+)", text))
    assert pins == {topicforge.__version__}, pins


def test_clients_doc_deeplinks_decode_to_pinned_config() -> None:
    text = (ROOT / "docs" / "CLIENTS.md").read_text(encoding="utf-8")

    cursor = re.search(r"\((cursor://[^)\s]+)\)", text)
    assert cursor, "Cursor deeplink missing"
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(cursor.group(1)).query)
    cursor_cfg = json.loads(base64.b64decode(query["config"][0]))
    assert query["name"] == ["topicforge"]
    assert _pin(cursor_cfg["args"]) == topicforge.__version__

    vscode = re.search(r"\(vscode:mcp/install\?([^)\s]+)\)", text)
    assert vscode, "VS Code deeplink missing"
    vscode_cfg = json.loads(urllib.parse.unquote(vscode.group(1)))
    assert vscode_cfg["name"] == "topicforge"
    assert _pin(vscode_cfg["args"]) == topicforge.__version__
    assert vscode_cfg["env"] == cursor_cfg["env"]
