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


_COMPRESSION_MODE = re.compile(r"^\s*compression_mode:\s*[\"']?([A-Za-z]*)[\"']?\s*$", re.M)
# `rosbags` unpacks a file-compressed bag into a temporary directory when it opens it, before
# any time budget can apply. Past this compressed size it is refused rather than unpacked.
FILE_COMPRESSED_MAX_BYTES = 64 * 1024 * 1024


def is_file_compressed(path: Path) -> bool:
    """True for a rosbag2 directory whose `metadata.yaml` says `compression_mode: FILE`.

    Message compression (`MESSAGE`) is not file compression: its cost is paid message by
    message while reading. Reads only `metadata.yaml`; `False` when it is absent or unreadable.
    """
    meta = (path if path.is_dir() else path.parent) / "metadata.yaml"
    try:
        found = _COMPRESSION_MODE.search(meta.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return False
    return bool(found and found.group(1).upper() == "FILE")


def compressed_size_bytes(path: Path) -> int:
    """Size on disk of a bag file, or of the files under a bag directory."""
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def refuse_big_file_compressed(path: Path) -> None:
    """Raise `AdapterError` for a file-compressed bag over `FILE_COMPRESSED_MAX_BYTES`."""
    if not is_file_compressed(path):
        return
    size = compressed_size_bytes(path)
    if size > FILE_COMPRESSED_MAX_BYTES:
        raise AdapterError(
            f"{path.name!r} uses file compression (`compression_mode: FILE`), {size // 2**20} MiB "
            f"compressed: reading it would unpack it to a temporary directory, which is refused "
            f"above {FILE_COMPRESSED_MAX_BYTES // 2**20} MiB. Decompress it first "
            "(`ros2 bag reindex` after `zstd -d`, or `ros2 bag convert`), or use `ros2 bag info`."
        )
