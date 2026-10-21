"""Trim the JSON schemas the server lists, without changing what they describe.

`tools/list` carries the input and output schema of every tool. Pydantic adds to
each of them a generated `title` on every property and model, and the whole
class docstring as the model `description`. Neither tells a client anything the
property name and the one-line summary do not, and together they were about a
tenth of the listing, which some clients truncate. This module removes the
generated titles and the docstring of every nested model, and keeps the first
paragraph of the docstring of the top-level one. Property
names, types, constraints and field descriptions are untouched; `default` keywords
are dropped from output schemas only (a result always carries the field), and a
nullable scalar `anyOf: [X, {type: null}]` is folded into `type: [X, "null"]` there
too. Input schemas keep the `anyOf` form clients already parse. An output description that
only says "Envelope returned by `<tool>`." is dropped.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

JsonDict = dict[str, Any]


def drop_defaults(node: Any, *, in_properties: bool = False) -> None:
    """Remove every `default` keyword from an output schema, in place.

    A default says what a client may omit on input; a result always carries the field,
    so the keyword only costs bytes. A field literally named `default` is kept.
    """
    if isinstance(node, dict):
        if not in_properties:
            node.pop("default", None)
        for key, value in node.items():
            drop_defaults(value, in_properties=(key == "properties" and not in_properties))
    elif isinstance(node, list):
        for item in node:
            drop_defaults(item)


_NO_FOLD_KEYS = frozenset({"$ref", "anyOf", "oneOf", "allOf", "not", "if"})


def fold_nullable(node: Any, *, in_properties: bool = False) -> None:
    """Fold `anyOf: [X, {"type": "null"}]` into `type: [X's type, "null"]`, in place.

    For output schemas only. Applies when `X` is a plain typed schema (a `$ref` or a nested
    combinator is left alone, as a `type` list cannot express it). An `enum` or `const` of `X`
    gains `null` so the folded schema still accepts a null. Sibling keywords of the `anyOf`
    (`description`) are kept.
    """
    if isinstance(node, dict):
        if not in_properties:
            _fold_node(node)
        for key, value in node.items():
            fold_nullable(value, in_properties=(key == "properties" and not in_properties))
    elif isinstance(node, list):
        for item in node:
            fold_nullable(item)


def _fold_node(node: JsonDict) -> None:
    branches = node.get("anyOf")
    if not isinstance(branches, list) or len(branches) != 2:
        return
    typed = [b for b in branches if isinstance(b, dict) and b != {"type": "null"}]
    if len(typed) != 1 or {"type": "null"} not in branches:
        return
    inner = typed[0]
    kind = inner.get("type")
    if not isinstance(kind, str) or _NO_FOLD_KEYS & inner.keys() or "type" in node:
        return
    folded = {k: v for k, v in inner.items() if k not in ("type", "enum", "const")}
    if "enum" in inner:
        folded["enum"] = [*inner["enum"], None]
    elif "const" in inner:
        folded["enum"] = [inner["const"], None]
    del node["anyOf"]
    node["type"] = [kind, "null"]
    for key, value in folded.items():
        node.setdefault(key, value)


def compact_schema(node: Any, *, in_properties: bool = False, keep_title: bool = False) -> None:
    """Strip generated titles and cut model docstrings to their first paragraph, in place.

    `in_properties` is true for the mapping under a `properties` key, whose keys are
    field names (a field called `title` is kept), not schema keywords. `keep_title` keeps
    the title of this node itself (the output type name on an output schema).
    """
    if isinstance(node, dict):
        if not in_properties:
            if not keep_title:
                node.pop("title", None)
            desc = node.get("description")
            if isinstance(desc, str) and "\n\n" in desc:
                node["description"] = desc.split("\n\n", 1)[0].strip()
        for key, value in node.items():
            if key == "$defs" and isinstance(value, dict) and not in_properties:
                for definition in value.values():
                    if isinstance(definition, dict):
                        definition.pop("description", None)
            compact_schema(value, in_properties=(key == "properties" and not in_properties))
    elif isinstance(node, list):
        for item in node:
            compact_schema(item)


def compact_registered_tools(mcp: MCPServer) -> None:
    """Apply `compact_schema` to the input and output schema of every registered tool.

    Reaches into the SDK's tool registry; if a future SDK moves it, the listing
    is simply left verbose rather than broken.
    """
    manager = getattr(mcp, "_tool_manager", None)
    if manager is None:
        return
    for tool in manager.list_tools():
        compact_schema(tool.parameters)
        output = tool.output_schema
        if output is not None:
            compact_schema(output, keep_title=True)
            drop_defaults(output)
            fold_nullable(output)
            if output.get("description") == f"Envelope returned by `{tool.name}`.":
                del output["description"]  # restates the tool name
