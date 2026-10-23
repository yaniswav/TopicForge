"""Compare a ROS 2 parameter file with the parameters a node is running with.

Pure functions, no `ros2` and no file access: the caller reads the file and hands the
text over. This is the declared-against-real check of `get_node_info(params_file=...)`.

The file is the YAML a launch file loads, `{<node>: {ros__parameters: {...}}}`. The
sections that apply to a node are, from the least to the most specific: `/**` (or `**`),
`/<namespace>/**`, the bare node name, the fully qualified name; a later section wins.
Any other key with a `*` is a pattern this module does not evaluate: it is named in the
note, never guessed. Names are flattened with `.` between levels, as on the live side.

Equality. An integer and a float are equal when their values are (`12` and `12.0`); a
boolean equals only a boolean; strings are compared exactly; lists element by element
with the same rules. A name that looks like a secret is never shown from the file: the
entry is `masked` whatever the node holds. A live value the node cut for its size is
compared with the file's value cut the same way.

The file is parsed with `yaml.safe_load` (no Python objects). Aliases can make a small
file expand into a huge tree, so a document with more than `MAX_DOCUMENT_NODES` expanded
values is refused.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import yaml
from pydantic import JsonValue

from topicforge.adapters.common.nodes import (
    MASKED_VALUE,
    MAX_PARAMETERS,
    cut_value,
    flatten_parameters,
    is_secret_name,
    json_safe,
    split_full_name,
)
from topicforge.models import NodeParameter, ParamDiff

# Largest parameter file read, in bytes.
MAX_PARAMS_FILE_BYTES = 1_048_576
# Entries of `param_diff` returned; more are counted in the note.
MAX_PARAM_DIFF = 200
# Values of the loaded document (aliases expanded) beyond which the file is refused.
MAX_DOCUMENT_NODES = 100_000

_ROS_PARAMETERS = "ros__parameters"
_GENERIC_KEYS = frozenset({"/**", "**"})


class ParamFileError(ValueError):
    """The file text is not a usable parameter file; the message is user-safe."""


@dataclass(frozen=True)
class ParamComparison:
    """What `compare_parameters` found: the listed differences and the two counts left out."""

    entries: list[ParamDiff]
    same: int
    live_only: int
    omitted: int = 0
    same_names: tuple[str, ...] = ()
    live_only_names: tuple[str, ...] = ()


def load_params_document(text: str) -> Mapping[str, object]:
    """The mapping a parameter file holds; raises `ParamFileError` when it cannot be read."""
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ParamFileError(_yaml_problem(exc)) from exc
    except RecursionError as exc:
        raise ParamFileError("the file is nested too deeply") from exc
    if not isinstance(document, Mapping):
        raise ParamFileError("the top level of the file is not a mapping of node names")
    if _exceeds(document, MAX_DOCUMENT_NODES):
        raise ParamFileError(f"the file expands to more than {MAX_DOCUMENT_NODES} values")
    return document


def _yaml_problem(exc: yaml.YAMLError) -> str:
    """The parser's complaint and line, without the text of the file."""
    mark = getattr(exc, "problem_mark", None)
    problem = getattr(exc, "problem", None) or "invalid YAML"
    where = f" at line {mark.line + 1}" if mark is not None else ""
    return f"the file is not valid YAML ({problem}{where})"


def _exceeds(root: object, limit: int) -> bool:
    """True when `root` holds more than `limit` values, an alias counted at each use."""
    seen = 0
    stack: list[object] = [root]
    while stack:
        seen += 1
        if seen > limit:
            return True
        item = stack.pop()
        if isinstance(item, Mapping):
            stack.extend(item.values())
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
    return False


def _rank(key: str, full_name: str) -> int | None:
    """How specifically `key` names the node: 0 generic .. 3 full name; `None` if it does not."""
    name, _ = split_full_name(full_name)
    if key in _GENERIC_KEYS:
        return 0
    if key.endswith("/**") and key.count("*") == 2 and full_name.startswith(key[:-2]):
        return 1
    if key == name:
        return 2
    if key in (full_name, full_name.lstrip("/")):
        return 3
    return None


def _unsupported(key: str) -> bool:
    """A key with a wildcard that `_rank` does not evaluate."""
    return (
        ("*" in key or "?" in key)
        and key not in _GENERIC_KEYS
        and not (key.endswith("/**") and key.count("*") == 2 and "?" not in key)
    )


def declared_parameters(
    document: Mapping[str, object], full_name: str
) -> tuple[dict[str, JsonValue] | None, str | None]:
    """`(flattened parameters declared for the node, note)`; `None` when no section applies."""
    sections: list[tuple[int, int, Mapping[str, object]]] = []
    ignored: list[str] = []
    notes: list[str] = []
    for index, (raw_key, body) in enumerate(document.items()):
        key = str(raw_key).strip()
        rank = _rank(key, full_name)
        if rank is None:
            if _unsupported(key):
                ignored.append(key)
            continue
        params = body.get(_ROS_PARAMETERS) if isinstance(body, Mapping) else None
        if not isinstance(params, Mapping):
            notes.append(f"the section `{key}` has no `{_ROS_PARAMETERS}` mapping")
            continue
        sections.append((rank, index, params))
    if ignored:
        shown = ", ".join(f"`{k}`" for k in ignored[:3])
        notes.append(f"pattern(s) not evaluated: {shown}")
    if not sections:
        keys = ", ".join(f"`{k}`" for k in list(map(str, document))[:5])
        notes.insert(0, f"the file has no section for {full_name} (keys: {keys or 'none'})")
        return None, "; ".join(notes)
    merged: dict[str, JsonValue] = {}
    for _, _, params in sorted(sections, key=lambda s: (s[0], s[1])):
        for name, value in flatten_parameters(params):
            merged[name] = json_safe(value)
    return merged, "; ".join(notes) or None


def values_equal(declared: JsonValue, live: JsonValue) -> bool:
    """The equality rules of the module docstring."""
    if isinstance(declared, bool) or isinstance(live, bool):
        return isinstance(declared, bool) and isinstance(live, bool) and declared == live
    if isinstance(declared, (int, float)) and isinstance(live, (int, float)):
        return declared == live
    if isinstance(declared, list) and isinstance(live, list):
        return len(declared) == len(live) and all(
            values_equal(a, b) for a, b in zip(declared, live, strict=True)
        )
    return type(declared) is type(live) and declared == live


def compare_parameters(
    declared: Mapping[str, JsonValue], live: Sequence[NodeParameter]
) -> ParamComparison:
    """Differences between the declared parameters and the node's, sorted by name."""
    by_name = {p.name: p for p in live}
    entries: list[ParamDiff] = []
    same: list[str] = []
    for name in sorted(declared):
        entry = _compare_one(name, declared[name], by_name.get(name))
        if entry is None:
            same.append(name)
        else:
            entries.append(entry)
    live_only = sorted(name for name in by_name if name not in declared)
    omitted = max(len(entries) - MAX_PARAM_DIFF, 0)
    return ParamComparison(
        entries[:MAX_PARAM_DIFF],
        len(same),
        len(live_only),
        omitted,
        same_names=tuple(same),
        live_only_names=tuple(live_only),
    )


def _compare_one(name: str, declared: JsonValue, live: NodeParameter | None) -> ParamDiff | None:
    """`None` when the two agree, else the entry to report."""
    if is_secret_name(name):
        return ParamDiff(
            name=name,
            status="masked",
            declared=MASKED_VALUE,
            live=None if live is None else MASKED_VALUE,
        )
    shown, _ = cut_value(declared)
    if live is None:
        return ParamDiff(name=name, status="declared_only", declared=shown, live=None)
    if live.masked:
        return ParamDiff(name=name, status="masked", declared=MASKED_VALUE, live=MASKED_VALUE)
    expected = shown if live.truncated else declared
    if values_equal(expected, live.value):
        return None
    return ParamDiff(name=name, status="different", declared=shown, live=live.value)


# Names listed per count in the summary; the rest are counted.
_SUMMARY_NAMES = 10


def _named(count: int, label: str, names: Sequence[str]) -> str:
    if not names:
        return f"{count} {label}"
    shown = ", ".join(names[:_SUMMARY_NAMES])
    more = len(names) - _SUMMARY_NAMES
    return f"{count} {label} ({shown}{f' and {more} more' if more > 0 else ''})"


def summarize(comparison: ParamComparison) -> str:
    """`"1 same (rate), 2 live_only (a, b)"`: what `param_diff` does not list, with names.

    `live_only` parameters are on the node and absent from the file (the ROS 2 defaults
    such as `use_sim_time` among them); `same` agree with the file.
    """
    return (
        f"{_named(comparison.same, 'same', comparison.same_names)}, "
        f"{_named(comparison.live_only, 'live_only', comparison.live_only_names)}"
    )


def build_param_diff(
    text: str, full_name: str, live: Sequence[NodeParameter] | None
) -> tuple[list[ParamDiff] | None, str | None, str | None]:
    """`(param_diff, summary, note)` for a parameter file's `text` against a node.

    `live` is the node's parameters, `None` when they were not read. `param_diff` is
    `None` when no comparison could be made, and the note says why.
    """
    try:
        document = load_params_document(text)
    except ParamFileError as exc:
        return None, None, f"params_file not compared: {exc}."
    declared, note = declared_parameters(document, full_name)
    if declared is None:
        return None, None, f"params_file not compared: {note}."
    if live is None:
        return None, None, "params_file not compared: the node's parameters were not read."
    comparison = compare_parameters(declared, live)
    extra = [note] if note else []
    if comparison.omitted:
        extra.append(f"{comparison.omitted} more difference(s) not listed")
    if len(live) >= MAX_PARAMETERS:
        extra.append("the node reports more parameters than are listed, so declared_only may lie")
    return comparison.entries, summarize(comparison), "; ".join(extra) or None


__all__ = [
    "MAX_DOCUMENT_NODES",
    "MAX_PARAMS_FILE_BYTES",
    "MAX_PARAM_DIFF",
    "ParamComparison",
    "ParamFileError",
    "build_param_diff",
    "compare_parameters",
    "declared_parameters",
    "load_params_document",
    "summarize",
    "values_equal",
]
