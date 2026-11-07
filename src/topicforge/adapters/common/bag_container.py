"""Container and storage of a bag at a path, read from the file system only.

A rosbag2 bag is a directory with a `metadata.yaml` (storage `sqlite3` or `mcap`) or a
bare `.db3` / `.mcap` file; a ROS 1 bag is a `.bag` file. `ros2 bag info` prints the
storage as `sqlite3` or `mcap`, and `storage_format` keeps those names.
"""

from __future__ import annotations

import re
from pathlib import Path

from topicforge.adapters.base import AdapterError

_EXT_FORMAT: dict[str, tuple[str, str]] = {
    ".mcap": ("mcap", "mcap"),
    ".db3": ("db3", "sqlite3"),
    ".bag": ("bag", "bag"),
}
_STORAGE_FORMAT: dict[str, tuple[str, str]] = {
    "mcap": ("mcap", "mcap"),
    "sqlite3": ("db3", "sqlite3"),
}
_STORAGE_LINE = re.compile(r"^\s*storage_identifier:\s*[\"']?([A-Za-z0-9_]+)[\"']?\s*$", re.M)


def describe_container(path: Path) -> tuple[str, str | None]:
    """`(bag_format, storage_format)` of the bag at `path`: `("db3", "sqlite3")`, `("mcap", "mcap")`.

    A directory is read through its `metadata.yaml`, else through the bag files inside.
    Returns `("unknown", None)` when the format cannot be told. Raises `AdapterError` for a
    directory that is not a rosbag2 bag and for a bare `.zstd` file, which `rosbags` cannot open.
    """
    if path.is_dir():
        return _describe_directory(path)
    suffix = path.suffix.lower()
    if suffix == ".zstd":
        raise AdapterError(
            f"{path.name!r} is a bare zstd-compressed file, which cannot be read as a bag. "
            "Decompress it (`zstd -d`) or pass the rosbag2 directory that holds it with its "
            "`metadata.yaml`."
        )
    return _EXT_FORMAT.get(suffix, ("unknown", None))


def _describe_directory(path: Path) -> tuple[str, str | None]:
    meta = path / "metadata.yaml"
    if not meta.is_file():
        raise AdapterError(
            f"{str(path)!r} is a directory without a `metadata.yaml`, so it is not a rosbag2 "
            "bag. Pass the bag directory itself (the one that holds `metadata.yaml`), or a "
            "`.mcap` / `.db3` file."
        )
    try:
        found = _STORAGE_LINE.search(meta.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        found = None
    if found and found.group(1).lower() in _STORAGE_FORMAT:
        return _STORAGE_FORMAT[found.group(1).lower()]
    for suffix, fmt in _EXT_FORMAT.items():
        if any(path.glob(f"*{suffix}")) or any(path.glob(f"*{suffix}.zstd")):
            return fmt
    return "unknown", None
