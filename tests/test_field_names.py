"""No field name carries a unit or a kind the locked vocabulary does not allow.

docs/CONTRACT.md section 1.4: durations end in `_s` or `_ns`, rates in `_hz`, explanations in
`_note`. A name ending in `_seconds`, `_sec`, `_ms`, `_hertz`, `_reason` and the like is a
second spelling of the same thing and fails here. Reads the output and input schemas as served
by `list_tools`, nested models included.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator
from typing import Any

import pytest

from topicforge.config import Settings
from topicforge.server import build_app

_FORBIDDEN_SUFFIXES = (
    "_seconds",
    "_sec",
    "_secs",
    "_ms",
    "_us",
    "_nanos",
    "_nanoseconds",
    "_hertz",
    "_reason",
    "_num",
    "_nb",
)

# A name that reads as a unit-less count, `_hz` in the middle, or the old spellings.
_RENAMED_AWAY = (
    "frequency_hz_observed",
    "frequency_hz_declared",
    "duration_seconds",
    "window_seconds",
    "lookback_seconds",
)


def _tools() -> list[Any]:
    app = build_app(
        Settings(mode="mock", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False)
    )
    return asyncio.run(app.list_tools())


def _property_names(schema: Any) -> Iterator[str]:
    """Every property name anywhere in a JSON schema (including `$defs`)."""
    if isinstance(schema, dict):
        props = schema.get("properties")
        if isinstance(props, dict):
            yield from props
        for value in schema.values():
            yield from _property_names(value)
    elif isinstance(schema, list):
        for value in schema:
            yield from _property_names(value)


def _all_names() -> list[tuple[str, str, str]]:
    found = []
    for tool in _tools():
        for kind, schema in (("input", tool.input_schema), ("output", tool.output_schema)):
            found += [(tool.name, kind, name) for name in _property_names(schema)]
    return found


def test_the_schemas_have_names_to_check() -> None:
    names = _all_names()
    assert len(names) > 150


@pytest.mark.parametrize("suffix", _FORBIDDEN_SUFFIXES)
def test_no_field_name_ends_with_a_forbidden_suffix(suffix: str) -> None:
    bad = sorted({f"{t}.{k}.{n}" for t, k, n in _all_names() if n.endswith(suffix)})
    assert not bad, f"field names ending in {suffix!r}: {bad}"


def test_renamed_fields_do_not_come_back() -> None:
    bad = sorted({f"{t}.{k}.{n}" for t, k, n in _all_names() if n in _RENAMED_AWAY})
    assert not bad, bad


def test_field_names_are_lower_snake_case() -> None:
    pattern = re.compile(r"^_?[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
    bad = sorted({n for _, _, n in _all_names() if not pattern.match(n)})
    assert not bad, bad
