"""Architecture pins: layer rules and schema-mode traps that no other test covers."""

from __future__ import annotations

import ast
from pathlib import Path

from topicforge.models import TopicListItem

_PACKAGE = Path(__file__).resolve().parents[1] / "src" / "topicforge"
_ADAPTERS = _PACKAGE / "adapters"
_FORBIDDEN = "topicforge.services"


def _module_name(path: Path) -> str:
    """Dotted module name of `path`; a package `__init__` names the package itself."""
    parts = list(path.relative_to(_PACKAGE.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _package_of(path: Path) -> list[str]:
    """The package a module's relative imports resolve against."""
    parts = _module_name(path).split(".")
    return parts if path.name == "__init__.py" else parts[:-1]


def _imported_modules(path: Path, source: str | None = None) -> list[tuple[int, str]]:
    """Every absolute module name imported anywhere in `path` (function-local too).

    `source` overrides the file content; `path` still drives relative resolution.
    """
    text = path.read_text(encoding="utf-8") if source is None else source
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(text, str(path))):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                package = _package_of(path)
                anchor = package[: len(package) - (node.level - 1)]
                base = ".".join([*anchor, *([base] if base else [])])
            found.append((node.lineno, base))
            # `from topicforge import services` imports the package through a name.
            found.extend((node.lineno, f"{base}.{alias.name}") for alias in node.names)
    return found


def _is_services(module: str) -> bool:
    return module == _FORBIDDEN or module.startswith(_FORBIDDEN + ".")


def test_adapters_never_import_services() -> None:
    """Adapters sit below services; any import of `topicforge.services` breaks the layering.

    Walks the AST of every adapter module, so absolute, relative and
    function-local imports are all seen. A violation adds a line to
    `offenders` and fails the assertion with its location.
    """
    files = sorted(_ADAPTERS.rglob("*.py"))
    assert files, "no adapter modules found: the test would pass vacuously"
    offenders = [
        f"{path.relative_to(_PACKAGE.parent)}:{line} imports {module}"
        for path in files
        for line, module in _imported_modules(path)
        if _is_services(module)
    ]
    assert not offenders, "\n".join(offenders)


def test_import_scanner_sees_absolute_relative_and_local_imports() -> None:
    """Prove the scanner would catch each import form, so the layer test cannot rot silent."""
    sample = _ADAPTERS / "ros2_live" / "x.py"
    source = "\n".join(
        [
            "import topicforge.services.a",
            "from ...services import c",
            "from topicforge import services",
            "def f():",
            "    from topicforge.services.d import e",
        ]
    )
    modules = {m for _, m in _imported_modules(sample, source) if _is_services(m)}
    assert modules >= {
        "topicforge.services.a",
        "topicforge.services",
        "topicforge.services.c",
        "topicforge.services.d",
    }


def test_topic_list_item_schema_trap() -> None:
    """`counts_unknown` is dropped from dumps when None but must stay in the validation schema.

    Serialization-mode JSON schema collapses a model with a custom serializer
    to a free object, so schema tools must use `mode="validation"`.
    """
    schema = TopicListItem.model_json_schema(mode="validation")
    assert "counts_unknown" in schema["properties"]

    base = {"name": "/a", "message_type": "t/msg/T", "publisher_count": 1, "subscriber_count": 1}
    assert "counts_unknown" not in TopicListItem(**base).model_dump()
    assert TopicListItem(**base, counts_unknown=True).model_dump()["counts_unknown"] is True
