"""Read a ROS 2 parameter file and attach its comparison to a `NodeInfo`.

The only file access of `get_node_info(params_file=...)`: a regular `.yaml` / `.yml`
file of at most 1 MiB, opened read-only on the machine the server runs on. The path is
never handed to the `ros2` CLI and nothing is written. An unreadable file is a note on
the result (`param_diff_note`), not an error: the node's interfaces and parameters are
still worth returning. The comparison itself is `adapters.common.param_diff`.
"""

from __future__ import annotations

from pathlib import Path

from topicforge.adapters.base import AdapterError
from topicforge.adapters.common.param_diff import MAX_PARAMS_FILE_BYTES, build_param_diff
from topicforge.models import NodeInfo

_SUFFIXES = (".yaml", ".yml")


def validate_params_file(path: object) -> str:
    """The stripped path, or an `AdapterError` for a value that cannot be a YAML file path."""
    if not isinstance(path, str) or not path.strip():
        raise AdapterError("params_file must be a non-empty string")
    clean = path.strip()
    if "\x00" in clean:
        raise AdapterError("params_file must not contain null bytes")
    if not clean.lower().endswith(_SUFFIXES):
        raise AdapterError("params_file must be a .yaml or .yml file")
    return clean


def read_params_file(path: str) -> tuple[str | None, str | None]:
    """`(text, None)` for a readable parameter file, else `(None, why)`."""
    target = Path(path)
    try:
        if not target.is_file():
            return None, f"params_file not compared: {path} is not a file on the server."
        with target.open("rb") as handle:
            raw = handle.read(MAX_PARAMS_FILE_BYTES + 1)
    except OSError as exc:
        return None, f"params_file not compared: it could not be read ({exc.strerror or 'error'})."
    if len(raw) > MAX_PARAMS_FILE_BYTES:
        return None, f"params_file not compared: it is larger than {MAX_PARAMS_FILE_BYTES} bytes."
    try:
        return raw.decode("utf-8-sig"), None
    except UnicodeDecodeError:
        return None, "params_file not compared: it is not UTF-8 text."


def with_param_diff(info: NodeInfo, params_file: str) -> NodeInfo:
    """`info` with `param_diff`, `param_diff_summary` and `param_diff_note` filled in."""
    text, note = read_params_file(params_file)
    if text is None:
        return info.model_copy(update={"param_diff_note": note})
    diff, summary, diff_note = build_param_diff(text, info.full_name, info.parameters)
    return info.model_copy(
        update={"param_diff": diff, "param_diff_summary": summary, "param_diff_note": diff_note}
    )
