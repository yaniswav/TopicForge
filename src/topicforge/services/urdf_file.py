"""Read a local `.urdf` file for `inspect_tf(urdf_path=...)`.

The only file access of that input: a regular `.urdf` file of at most 1 MiB (the bound of
`params_file`), opened read-only on the machine the server runs on. The path is never
handed to the `ros2` CLI and nothing is written. xacro is never expanded or executed: a
`.xacro` path is refused, and a `.urdf` that still holds `xacro:` elements is reported as
unreadable, because its macros and properties would be compared unexpanded. Problems other
than a malformed path are a note (`urdf_note`): the TF tree is still the result.
"""

from __future__ import annotations

from pathlib import Path

from topicforge.adapters.base import AdapterError
from topicforge.adapters.common.param_diff import MAX_PARAMS_FILE_BYTES
from topicforge.adapters.common.urdf import UrdfError, UrdfModel, parse_urdf, xacro_reason

MAX_URDF_FILE_BYTES = MAX_PARAMS_FILE_BYTES


def validate_urdf_path(path: object) -> str:
    """The stripped path, or an `AdapterError` for a value that cannot be a `.urdf` file path."""
    if not isinstance(path, str) or not path.strip():
        raise AdapterError("urdf_path must be a non-empty string")
    clean = path.strip()
    if "\x00" in clean:
        raise AdapterError("urdf_path must not contain null bytes")
    if clean.lower().endswith(".xacro"):
        raise AdapterError(
            "urdf_path is a xacro file, which is not expanded here: run `xacro` yourself "
            "and pass the resulting .urdf"
        )
    if not clean.lower().endswith(".urdf"):
        raise AdapterError("urdf_path must be a .urdf file")
    return clean


def read_urdf_file(path: str) -> tuple[UrdfModel | None, str]:
    """`(model, note)` for a readable URDF file, else `(None, why)`; never raises."""
    target = Path(path)
    try:
        if not target.is_file():
            return None, f"urdf_path not compared: {path} is not a file on the server."
        with target.open("rb") as handle:
            raw = handle.read(MAX_URDF_FILE_BYTES + 1)
    except OSError as exc:
        return None, f"urdf_path not compared: it could not be read ({exc.strerror or 'error'})."
    if len(raw) > MAX_URDF_FILE_BYTES:
        return None, f"urdf_path not compared: it is larger than {MAX_URDF_FILE_BYTES} bytes."
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, "urdf_path not compared: it is not UTF-8 text."
    if (xacro := xacro_reason(text)) is not None:
        return None, f"urdf_path not compared: {xacro}, and xacro is not expanded."
    try:
        return parse_urdf(text), "URDF read from the urdf_path file, not from /robot_description."
    except UrdfError as exc:
        return None, f"urdf_path not compared: {exc}."
