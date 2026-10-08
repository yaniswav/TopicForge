"""Pure parser for the YAML that `ros2 topic echo` prints, one document per message.

Module-level functions over CLI text, tested without ROS 2. The process side
(streaming, deadlines, kill) lives in `echo_stream.py`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

import yaml

from topicforge.adapters.common.stamps import payload_stamp
from topicforge.constants import TRUNCATED_FIELDS_KEY

ECHO_DOCUMENT_SEPARATOR = "---"
# The CLI's marker for a cut: an extra `'...'` list element after the first
# N elements, or `...` appended to the first N characters of a string.
TRUNCATION_MARK = "..."
RAW_TEXT_KEY = "_raw_text"

StampSource = Literal["header", "payload", "none"]


@dataclass(frozen=True)
class EchoMessage:
    """One decoded echo document.

    `timestamp_ns` is the top-level `header.stamp` (`stamp_source` `header`),
    the time in the body of `Clock`, `TFMessage` or `Log` (`payload`), or 0
    for a message without one (`none`).
    """

    payload: dict[str, object]
    timestamp_ns: int
    stamp_source: StampSource


# libyaml parses a 1 MiB document about 10 times faster than the pure-Python loader.
_BaseLoader = yaml.CSafeLoader if yaml.__with_libyaml__ else yaml.SafeLoader


class _EchoLoader(_BaseLoader):  # type: ignore[misc, valid-type]
    """Safe loader that leaves timestamp-looking scalars (`2024-01-01`) as strings."""


_EchoLoader.yaml_implicit_resolvers = {
    ch: [(tag, rx) for tag, rx in resolvers if tag != "tag:yaml.org,2002:timestamp"]
    for ch, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def split_echo_documents(text: str) -> list[str]:
    """Split echo output into its complete documents.

    A document ends at a line that is exactly `---`. Text after the last
    separator is a message still being printed and is dropped.
    """
    docs: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.rstrip() == ECHO_DOCUMENT_SEPARATOR:
            docs.append(join_document_lines(current))
            current = []
        else:
            current.append(line)
    return docs


def join_document_lines(lines: list[str]) -> str:
    """A document's lines as text, newline-terminated (block scalars keep their last newline)."""
    return "\n".join(lines) + "\n" if lines else ""


def parse_echo_document(document: str, *, truncate_length: int | None = None) -> EchoMessage:
    """Decode one echo document into a nested payload and a `header.stamp` timestamp.

    `truncate_length` is the `--truncate-length` the CLI ran with (`None` for
    `--full-length`). Arrays cut there lose the CLI's trailing `'...'`
    element, and strings cut there keep their `...`; the dotted path of every
    cut field is listed under `_truncated_fields`. A string that really is
    `truncate_length` characters followed by `...` is indistinguishable from a
    cut one and is reported as cut (a rare false positive). Non-finite floats become
    the strings `nan`, `inf` and `-inf` (JSON has no such numbers).

    A document that is not a YAML mapping keeps its text under `_raw_text`,
    with timestamp 0.
    """
    try:
        data = yaml.load(document, Loader=_EchoLoader)
    except yaml.YAMLError:
        data = None
    if not isinstance(data, dict):
        return EchoMessage({RAW_TEXT_KEY: document}, 0, "none")

    cut: list[str] = []
    payload = _normalize_mapping(data, "", truncate_length, cut)
    if cut:
        payload[TRUNCATED_FIELDS_KEY] = cut
    found = payload_stamp(payload)
    if found is None:
        return EchoMessage(payload, 0, "none")
    return EchoMessage(payload, found[0], found[1])


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _normalize_mapping(
    data: dict[Any, Any], path: str, truncate_length: int | None, cut: list[str]
) -> dict[str, object]:
    return {
        str(key): _normalize(value, f"{path}{key}", truncate_length, cut)
        for key, value in data.items()
    }


def _normalize(value: object, path: str, truncate_length: int | None, cut: list[str]) -> object:
    if isinstance(value, dict):
        return _normalize_mapping(value, f"{path}.", truncate_length, cut)
    if isinstance(value, list):
        items = list(value)
        if _is_cut_list(items, truncate_length):
            items.pop()
            _record(cut, path)
        return [_normalize(v, path, truncate_length, cut) for v in items]
    if isinstance(value, float) and not math.isfinite(value):
        return "nan" if math.isnan(value) else ("inf" if value > 0 else "-inf")
    if isinstance(value, (bytes, bytearray)):
        return list(value)
    if isinstance(value, str) and _is_cut_string(value, truncate_length):
        _record(cut, path)
    return value


def _is_cut_list(items: list[object], truncate_length: int | None) -> bool:
    return (
        truncate_length is not None
        and len(items) == truncate_length + 1
        and items[-1] == TRUNCATION_MARK
    )


def _is_cut_string(value: str, truncate_length: int | None) -> bool:
    return (
        truncate_length is not None
        and len(value) == truncate_length + len(TRUNCATION_MARK)
        and value.endswith(TRUNCATION_MARK)
    )


def _record(cut: list[str], path: str) -> None:
    if path not in cut:
        cut.append(path)
