"""Fail when the version strings scattered across the repo drift apart.

One release touches the package, the registry manifest, the Claude plugin, the
Gemini extension, the MCP Bundle and the pins copied into the docs. Forgetting
one of them ships a client config that installs the wrong version.

Repo-only: the sdist does not carry server.json, the plugin or the bundle, so the
whole module is skipped there. Nothing reads a file at import time.
"""

from __future__ import annotations

import base64
import json
import re
import urllib.parse
from collections.abc import Callable
from pathlib import Path

import pytest

import topicforge

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(
    not (ROOT / "server.json").exists(), reason="repo-only test (not in the sdist)"
)

PIN_RE = re.compile(r"topicforge(?:\[[a-z-]+\])?==([0-9][^\s\"'\\)&%]*)")
BARE_PIN_RE = re.compile(r"==(\d+\.\d+\.\d+)")
VERSION_LINE_RE = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)
LINK_RES = {
    "cursor https": re.compile(r"\((https://cursor\.com/install-mcp\?[^)\s]+)\)"),
    "vscode https": re.compile(r"\((https://vscode\.dev/redirect/mcp/install\?[^)\s]+)\)"),
}


def _text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _json(rel: str) -> dict:
    return json.loads(_text(rel))


def _toml_version(rel: str) -> str:
    match = VERSION_LINE_RE.search(_text(rel))
    assert match, f"no version line in {rel}"
    return match.group(1)


def _pin_in_args(args: list[str]) -> str:
    match = PIN_RE.fullmatch(args[args.index("--from") + 1])
    assert match, f"no topicforge==X pin in {args}"
    return match.group(1)


def _pin_in_text(rel: str) -> str:
    match = PIN_RE.search(_text(rel))
    assert match, f"no topicforge==X pin in {rel}"
    return match.group(1)


def _mcpb_pin() -> str:
    match = PIN_RE.search(_text("mcpb/pyproject.toml"))
    assert match, "no topicforge[dds]==X pin in mcpb/pyproject.toml"
    return match.group(1)


def _server_args(rel: str) -> list[str]:
    return _json(rel)["mcpServers"]["topicforge"]["args"]


SOURCES: dict[str, Callable[[], str]] = {
    "pyproject.toml": lambda: _toml_version("pyproject.toml"),
    "server.json": lambda: _json("server.json")["version"],
    "server.json packages[0]": lambda: _json("server.json")["packages"][0]["version"],
    "plugin.json": lambda: _json("plugin/.claude-plugin/plugin.json")["version"],
    "plugin .mcp.json pin": lambda: _pin_in_args(_server_args("plugin/.mcp.json")),
    "gemini-extension.json": lambda: _json("gemini-extension.json")["version"],
    "gemini-extension.json pin": lambda: _pin_in_args(_server_args("gemini-extension.json")),
    "mcpb manifest": lambda: _json("mcpb/manifest.json")["version"],
    "plugin pyproject version": lambda: _toml_version("plugin/pyproject.toml"),
    "plugin pyproject pin": lambda: _pin_in_text("plugin/pyproject.toml"),
    "mcpb pyproject version": lambda: _toml_version("mcpb/pyproject.toml"),
    "mcpb pyproject pin": _mcpb_pin,
}


@pytest.mark.parametrize("name", sorted(SOURCES))
def test_version_matches_package(name: str) -> None:
    assert SOURCES[name]() == topicforge.__version__, f"{name} drifted from __version__"


def test_pyproject_matches_package_and_bundle() -> None:
    assert _toml_version("pyproject.toml") == topicforge.__version__
    assert _toml_version("pyproject.toml") == _toml_version("mcpb/pyproject.toml")


@pytest.mark.parametrize("rel", ["docs/CLIENTS.md", "README.md", "docs/TESTING.md"])
def test_doc_pins_match_package(rel: str) -> None:
    text = _text(rel)
    pins = set(PIN_RE.findall(text)) | set(BARE_PIN_RE.findall(text))
    assert pins <= {topicforge.__version__}, f"{rel} has stale pins: {sorted(pins)}"


def _links(text: str) -> dict[str, str]:
    found = {}
    for name, regex in LINK_RES.items():
        match = regex.search(text)
        assert match, f"{name} install link missing"
        found[name] = match.group(1)
    return found


def _decode_cursor(url: str) -> dict:
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert query["name"] == ["topicforge"]
    return json.loads(base64.b64decode(query["config"][0]))


def _decode_vscode_redirect(url: str) -> dict:
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert query["name"] == ["topicforge"]
    return json.loads(query["config"][0])


def test_install_links_decode_to_pinned_config() -> None:
    clients = _text("docs/CLIENTS.md")
    links = _links(clients)
    cursor_cfg = _decode_cursor(links["cursor https"])
    vscode_cfg = _decode_vscode_redirect(links["vscode https"])
    assert _pin_in_args(cursor_cfg["args"]) == topicforge.__version__
    assert vscode_cfg == cursor_cfg

    # Raw scheme fallbacks kept in CLIENTS.md decode to the same config.
    raw_cursor = re.search(r"`(cursor://[^`\s]+)`", clients)
    assert raw_cursor, "raw cursor:// fallback missing"
    assert _decode_cursor(raw_cursor.group(1)) == cursor_cfg
    raw_vscode = re.search(r"`vscode:mcp/install\?([^`\s]+)`", clients)
    assert raw_vscode, "raw vscode: fallback missing"
    assert json.loads(urllib.parse.unquote(raw_vscode.group(1))) == {
        "name": "topicforge",
        **cursor_cfg,
    }


@pytest.mark.parametrize("rel", ["README.md", "docs/TESTING.md"])
def test_other_docs_links_identical_to_clients_doc(rel: str) -> None:
    text = _text(rel)
    expected = _links(_text("docs/CLIENTS.md"))
    for name, regex in LINK_RES.items():
        found = {m for m in regex.findall(text)}
        if rel == "README.md":
            assert found == {expected[name]}, f"{rel}: {name} link differs from CLIENTS.md"
        else:
            assert found <= {expected[name]}, f"{rel}: {name} link differs from CLIENTS.md"


def test_plugin_launches_without_an_extra() -> None:
    """The plugin directory refuses extras: dependencies live in plugin/pyproject.toml."""
    spec = _server_args("plugin/.mcp.json")[_server_args("plugin/.mcp.json").index("--from") + 1]
    assert spec == f"topicforge=={topicforge.__version__}"


def test_plugin_lock_matches_package_when_present() -> None:
    """plugin/uv.lock is generated after the release reaches PyPI, so it may be absent."""
    lock = ROOT / "plugin" / "uv.lock"
    if not lock.exists():
        pytest.skip("plugin/uv.lock not generated yet (run `uv lock` in plugin/ after PyPI)")
    text = lock.read_text(encoding="utf-8")
    match = re.search(r'^name = "topicforge"\s+version = "([^"]+)"', text, re.MULTILINE)
    assert match, "topicforge is not locked in plugin/uv.lock"
    assert match.group(1) == topicforge.__version__, "plugin/uv.lock is stale: rerun `uv lock`"
