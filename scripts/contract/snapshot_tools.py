"""Golden snapshots of the MCP tool contract, and the generated tool reference.

The server is built in mock mode and its tools are listed through the MCP
layer, the way a client sees them. Each tool becomes one JSON file under
`tests/contract/` holding its name, title, full description, `inputSchema`,
`outputSchema` and annotations. `docs/TOOLS.md` is generated from the same data.

Usage (from the repository root):

    python scripts/contract/snapshot_tools.py            # check, exit 1 on any diff
    python scripts/contract/snapshot_tools.py --update   # rewrite tests/contract/*.json
    python scripts/contract/snapshot_tools.py --docs     # rewrite docs/TOOLS.md

`--update` and `--docs` can be combined. A change to a snapshot is a change to
the public contract: review the diff, and see `docs/CONTRACT.md`.
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import logging
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_DIR = REPO_ROOT / "tests" / "contract"
TOOLS_DOC = REPO_ROOT / "docs" / "TOOLS.md"

sys.path.insert(0, str(REPO_ROOT / "src"))

JsonDict = dict[str, Any]


def collect_tools() -> list[JsonDict]:
    """List the tools served in mock mode, as plain JSON-ready dicts, sorted by name."""
    from topicforge.config import Settings
    from topicforge.server import build_app

    logging.disable(logging.CRITICAL)
    app = build_app(
        Settings(mode="mock", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False)
    )
    tools = asyncio.run(app.list_tools())
    out: list[JsonDict] = []
    for tool in tools:
        annotations = tool.annotations.model_dump(exclude_none=True) if tool.annotations else None
        out.append(
            {
                "name": tool.name,
                "title": tool.title or (annotations or {}).get("title"),
                "description": tool.description,
                "inputSchema": tool.inputSchema,
                "outputSchema": tool.outputSchema,
                "annotations": annotations,
            }
        )
    return sorted(out, key=lambda t: t["name"])


def render_snapshot(tool: JsonDict) -> str:
    """Serialize one tool as stable, ASCII-only, newline-terminated JSON."""
    return json.dumps(tool, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def snapshot_path(name: str) -> Path:
    """Path of the snapshot file of tool `name`."""
    return SNAPSHOT_DIR / f"{name}.json"


def diff_snapshot(name: str, expected: str, actual: str) -> str:
    """Unified diff between the stored snapshot (`expected`) and the served tool (`actual`)."""
    return "".join(
        difflib.unified_diff(
            expected.splitlines(keepends=True),
            actual.splitlines(keepends=True),
            fromfile=f"tests/contract/{name}.json (stored)",
            tofile=f"{name} (served by list_tools)",
            n=2,
        )
    )


def compare(tools: list[JsonDict]) -> list[str]:
    """Return one readable message per difference between stored snapshots and `tools`."""
    problems: list[str] = []
    served = {t["name"]: render_snapshot(t) for t in tools}
    stored = {p.stem: p for p in SNAPSHOT_DIR.glob("*.json")}
    for name in sorted(served.keys() - stored.keys()):
        problems.append(f"tool {name!r} is served but has no snapshot")
    for name in sorted(stored.keys() - served.keys()):
        problems.append(f"snapshot {name!r} exists but the tool is no longer served")
    for name in sorted(served.keys() & stored.keys()):
        expected = stored[name].read_text(encoding="utf-8")
        if expected != served[name]:
            diff = diff_snapshot(name, expected, served[name])
            problems.append(f"tool {name!r} differs:\n{diff}")
    return problems


def write_snapshots(tools: list[JsonDict]) -> list[Path]:
    """Rewrite every snapshot and delete the ones of tools no longer served."""
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    names = {t["name"] for t in tools}
    for stale in SNAPSHOT_DIR.glob("*.json"):
        if stale.stem not in names:
            stale.unlink()
    for tool in tools:
        path = snapshot_path(tool["name"])
        path.write_text(render_snapshot(tool), encoding="utf-8", newline="\n")
        written.append(path)
    return written


# --- docs/TOOLS.md ---------------------------------------------------------


def type_label(schema: JsonDict) -> str:
    """Short type label for a JSON schema node (`int`, `list[X]`, `A | B`, ...)."""
    if "$ref" in schema:
        return str(schema["$ref"]).rsplit("/", 1)[-1]
    if "const" in schema:
        return json.dumps(schema["const"])
    if "enum" in schema:
        return " | ".join(json.dumps(v) for v in schema["enum"])
    for key in ("anyOf", "oneOf"):
        if key in schema:
            return " | ".join(type_label(s) for s in schema[key])
    kind = schema.get("type")
    if kind == "array":
        return f"list[{type_label(schema.get('items', {}))}]"
    if kind == "object":
        extra = schema.get("additionalProperties")
        if isinstance(extra, dict):
            return f"dict[str, {type_label(extra)}]"
        return "object"
    if isinstance(kind, list):
        return " | ".join(str(k) for k in kind)
    return {"integer": "int", "number": "float", "string": "str", "boolean": "bool"}.get(
        str(kind), str(kind or "any")
    )


def _constraints(schema: JsonDict) -> str:
    parts: list[str] = []
    for key, label in (
        ("minimum", ">="),
        ("maximum", "<="),
        ("exclusiveMinimum", ">"),
        ("exclusiveMaximum", "<"),
        ("minLength", "min length"),
        ("maxLength", "max length"),
    ):
        if key in schema:
            parts.append(f"{label} {schema[key]}")
    for option in schema.get("anyOf", []):
        parts.extend(p for p in _constraints(option).split(", ") if p and p not in parts)
    return ", ".join(parts)


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def _field_rows(schema: JsonDict, *, with_default: bool) -> list[str]:
    required = set(schema.get("required", []))
    rows = []
    for name, prop in schema.get("properties", {}).items():
        cells = [f"`{name}`", f"`{_cell(type_label(prop))}`", "yes" if name in required else "no"]
        if with_default:
            cells.append(f"`{_cell(json.dumps(prop['default']))}`" if "default" in prop else "-")
            cells.append(_cell(_constraints(prop)) or "-")
        cells.append(_cell(prop.get("description", "")) or "-")
        rows.append("| " + " | ".join(cells) + " |")
    return rows


def _table(headers: list[str], rows: list[str]) -> list[str]:
    return [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
        *rows,
    ]


def render_tool_section(tool: JsonDict) -> str:
    """Markdown section of one tool."""
    ann = tool["annotations"] or {}
    flags = ", ".join(f"`{k}={str(ann[k]).lower()}`" for k in sorted(ann) if k != "title")
    lines = [
        f"## `{tool['name']}`",
        "",
        f"**{tool['title']}**",
        "",
        f"Annotations: {flags}",
        "",
        tool["description"].strip(),
        "",
        "### Input parameters",
        "",
    ]
    rows = _field_rows(tool["inputSchema"], with_default=True)
    if rows:
        lines += _table(["Name", "Type", "Required", "Default", "Constraints", "Description"], rows)
    else:
        lines.append("None.")
    lines += ["", "### Output (top-level fields)", ""]
    rows = _field_rows(tool["outputSchema"] or {}, with_default=False)
    if rows:
        lines += _table(["Field", "Type", "Required", "Description"], rows)
    else:
        lines.append("Not declared.")
    return "\n".join(lines) + "\n"


def render_tools_doc(tools: list[JsonDict]) -> str:
    """Full text of docs/TOOLS.md for `tools`."""
    head = [
        "# TopicForge tool reference",
        "",
        "Generated by `python scripts/contract/snapshot_tools.py --docs` from the tools",
        "served in mock mode. Do not edit by hand: `tests/test_contract_snapshots.py`",
        "fails when this file is out of date. The rules behind the shapes are in",
        "[CONTRACT.md](CONTRACT.md).",
        "",
        f"{len(tools)} tools, all read-only.",
        "",
        "| Tool | Title | World |",
        "| --- | --- | --- |",
    ]
    for t in tools:
        world = "open" if (t["annotations"] or {}).get("openWorldHint") else "closed"
        head.append(f"| [`{t['name']}`](#{t['name']}) | {t['title']} | {world} |")
    return "\n".join(head) + "\n\n" + "\n".join(render_tool_section(t) for t in tools)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--update", action="store_true", help="rewrite tests/contract/*.json")
    parser.add_argument("--docs", action="store_true", help="rewrite docs/TOOLS.md")
    args = parser.parse_args(argv)
    tools = collect_tools()
    if args.update:
        print(f"wrote {len(write_snapshots(tools))} snapshots to {SNAPSHOT_DIR}")
    if args.docs:
        TOOLS_DOC.write_text(render_tools_doc(tools), encoding="utf-8", newline="\n")
        print(f"wrote {TOOLS_DOC}")
    if args.update or args.docs:
        return 0
    problems = compare(tools)
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
