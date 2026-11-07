"""The frame codec of the sampler helper, and that the helper's own encoder agrees with it."""

from __future__ import annotations

import ast
import importlib.util
import io
import struct
from pathlib import Path
from types import ModuleType

import pytest

from topicforge.adapters.common.sampler_frames import (
    KIND_DROPPED,
    KIND_HELLO,
    KIND_MESSAGE,
    MAX_BODY_BYTES,
    Frame,
    FrameError,
    encode_frame,
    read_frame,
)
from topicforge.adapters.ros2_live.helper_stream import HELPER_PATH


def _helper_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_sampler_helper_under_test", HELPER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _frames(data: bytes) -> list[Frame]:
    stream = io.BytesIO(data)
    out = []
    while (frame := read_frame(stream)) is not None:
        out.append(frame)
    return out


def test_round_trip_keeps_kind_metadata_and_binary_body() -> None:
    body = bytes(range(256)) * 4
    meta = {"wall_ns": 7, "encoding": "cdr", "source_ns": 5, "received_ns": 6, "later": "kept"}
    frame = _frames(encode_frame(KIND_MESSAGE, meta, body))[0]
    assert (frame.kind, frame.body) == (KIND_MESSAGE, body)
    assert (frame.wall_ns, frame.encoding, frame.source_ns, frame.received_ns) == (7, "cdr", 5, 6)
    assert frame.meta["later"] == "kept"


def test_metadata_of_the_wrong_type_reads_as_absent() -> None:
    frame = _frames(encode_frame(KIND_MESSAGE, {"wall_ns": "x", "encoding": 3}))[0]
    assert (frame.wall_ns, frame.encoding, frame.source_ns, frame.received_ns) == (
        0,
        None,
        None,
        None,
    )


def test_the_helper_encoder_writes_what_the_codec_reads() -> None:
    helper = _helper_module()
    data = (
        helper.encode_frame(helper.KIND_HELLO, {"pid": 1, "type": "a/msg/B"})
        + helper.encode_frame(helper.KIND_MESSAGE, {"wall_ns": 2, "encoding": "yaml"}, b"x: 1\n")
        + helper.encode_frame(helper.KIND_DROPPED, {"wall_ns": 3})
    )
    assert [f.kind for f in _frames(data)] == [KIND_HELLO, KIND_MESSAGE, KIND_DROPPED]
    assert data == (
        encode_frame(KIND_HELLO, {"pid": 1, "type": "a/msg/B"})
        + encode_frame(KIND_MESSAGE, {"wall_ns": 2, "encoding": "yaml"}, b"x: 1\n")
        + encode_frame(KIND_DROPPED, {"wall_ns": 3})
    )


@pytest.mark.parametrize("keep", [0, 3, 12, 15])
def test_a_stream_cut_anywhere_ends_without_an_error(keep: int) -> None:
    whole = encode_frame(KIND_MESSAGE, {"wall_ns": 1}, b"body")
    assert keep < len(whole)
    assert _frames(whole[:keep]) == []


class _OneByteAtATime(io.BytesIO):
    """A pipe: `read` may return less than asked."""

    def read(self, size: int | None = -1) -> bytes:
        return super().read(1 if size else 0)


def test_short_reads_are_completed() -> None:
    frame = read_frame(_OneByteAtATime(encode_frame(KIND_MESSAGE, {"wall_ns": 1}, b"body")))
    assert frame is not None and frame.body == b"body"


@pytest.mark.parametrize(
    "raw",
    [
        b"WARNING: something printed!",
        struct.pack(">2sBBII", b"TF", 9, ord("m"), 0, 0),
        struct.pack(">2sBBII", b"TF", 1, ord("?"), 0, 0),
        struct.pack(">2sBBII", b"TF", 1, ord("m"), 1 << 30, 0),
        struct.pack(">2sBBII", b"TF", 1, ord("m"), 0, MAX_BODY_BYTES + 1),
        struct.pack(">2sBBII", b"TF", 1, ord("m"), 2, 0) + b"{x",
        struct.pack(">2sBBII", b"TF", 1, ord("m"), 2, 0) + b"[]",
    ],
)
def test_anything_else_is_a_frame_error(raw: bytes) -> None:
    with pytest.raises(FrameError):
        read_frame(io.BytesIO(raw))


# ---- static pins on the script: it must not grow a write path, nor import this package ----


def _helper_tree() -> ast.Module:
    return ast.parse(HELPER_PATH.read_text(encoding="utf-8"))


def test_the_helper_never_creates_a_write_path() -> None:
    source = HELPER_PATH.read_text(encoding="utf-8")
    for needle in (
        ".publish(",
        "create_publisher",
        "create_service",
        "create_client",
        "create_timer",
        "ActionClient",
        "ActionServer",
    ):
        assert needle not in source, needle


def test_the_helper_imports_only_stdlib_rclpy_and_rosidl_runtime() -> None:
    allowed = {
        "argparse",
        "copy",
        "json",
        "os",
        "rclpy",
        "rosidl_runtime_py",
        "struct",
        "sys",
        "threading",
        "time",
    }
    roots = set()
    for node in ast.walk(_helper_tree()):
        if isinstance(node, ast.Import):
            roots |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            roots.add((node.module or "").split(".")[0])
    assert roots <= allowed, roots - allowed


def test_no_file_next_to_the_helper_shadows_what_it_imports_before_dropping_the_path() -> None:
    siblings = {p.stem for p in Path(HELPER_PATH).parent.glob("*.py")}
    assert not siblings & {"argparse", "copy", "json", "os", "struct", "sys", "threading", "time"}


def test_the_node_has_the_sampler_name_and_no_rosout_nor_services() -> None:
    source = HELPER_PATH.read_text(encoding="utf-8")
    assert 'f"_topicforge_sampler_{os.getpid()}"' in source
    assert "enable_rosout=False" in source
    assert "start_parameter_services=False" in source
    assert "START_TYPE_DESCRIPTION_SERVICE_PARAM" in source
