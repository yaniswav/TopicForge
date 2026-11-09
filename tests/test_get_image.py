"""`get_image`: the decoding, the helper's raw frames, the refusals, and the MCP result."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import struct
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from rosbags.typesys import Stores, get_typestore

from topicforge.adapters.base import AdapterError
from topicforge.adapters.common.image_decode import (
    COMPRESSED_TYPE,
    RAW_TYPE,
    compressed_size,
    decode_image,
)
from topicforge.adapters.common.sampler_frames import ENCODING_CDR, KIND_MESSAGE, Frame
from topicforge.adapters.ros2_live import image_capture
from topicforge.adapters.ros2_live.echo_stream import EchoRun
from topicforge.adapters.ros2_live.helper_stream import (
    SamplingBackend,
    _add_frame,
    helper_command,
    stream_helper,
)
from topicforge.adapters.ros2_live.image_capture import _nothing_arrived, capture_image
from topicforge.adapters.ros2_mock import MockAdapter
from topicforge.config import Settings
from topicforge.models import TopicInfo
from topicforge.server import build_app
from topicforge.services.inspector import Inspector

_FAKE = Path(__file__).parent / "fixtures" / "fake_rclpy"
_STORE = get_typestore(Stores.ROS2_HUMBLE)
_PNG = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 7, 5) + b"rest"
_JPEG = (
    b"\xff\xd8\xff\xe0"
    + struct.pack(">H", 4)
    + b"\0\0"
    + b"\xff\xc0"
    + struct.pack(">HBHHB", 11, 8, 5, 7, 3)
    + b"rest"
)


def _header(frame_id: str = "cam") -> Any:
    types = _STORE.types
    stamp = types["builtin_interfaces/msg/Time"](sec=3, nanosec=4)
    return types["std_msgs/msg/Header"](stamp=stamp, frame_id=frame_id)


def _compressed_cdr(data: bytes, fmt: str = "png", frame_id: str = "cam") -> bytes:
    cls = _STORE.types[COMPRESSED_TYPE]
    msg = cls(header=_header(frame_id), format=fmt, data=np.frombuffer(data, dtype=np.uint8))
    return bytes(_STORE.serialize_cdr(msg, COMPRESSED_TYPE))


def _raw_cdr(
    width: int, height: int, encoding: str, data: bytes, step: int, bigendian: int = 0
) -> bytes:
    cls = _STORE.types[RAW_TYPE]
    msg = cls(
        header=_header(),
        height=height,
        width=width,
        encoding=encoding,
        is_bigendian=bigendian,
        step=step,
        data=np.frombuffer(data, dtype=np.uint8),
    )
    return bytes(_STORE.serialize_cdr(msg, RAW_TYPE))


def _decode(message_type: str, cdr: bytes, max_edge: int = 512, max_bytes: int = 10**7) -> Any:
    return decode_image(
        "/c",
        message_type,
        cdr,
        received_ns=9,
        mode_effective="live",
        max_edge=max_edge,
        max_bytes=max_bytes,
    )


def _pixels(data: bytes) -> np.ndarray:
    from PIL import Image

    return np.asarray(Image.open(io.BytesIO(data)))


# ----- compressed -----


@pytest.mark.parametrize(
    ("data", "mime", "fmt"),
    [(_PNG, "image/png", "png"), (_JPEG, "image/jpeg", "rgb8; jpeg compressed bgr8")],
)
def test_a_compressed_frame_passes_through_byte_for_byte(data: bytes, mime: str, fmt: str) -> None:
    capture = _decode(COMPRESSED_TYPE, _compressed_cdr(data, fmt))
    info = capture.info
    assert capture.data == data
    assert info.mime_type == mime and info.encoding == fmt
    assert (info.width, info.height) == (7, 5) == (info.returned_width, info.returned_height)
    assert info.sha256 == hashlib.sha256(data).hexdigest() and info.byte_count == len(data)
    assert info.stamp_ns == 3_000_000_004 and info.frame_id == "cam" and info.received_ns == 9


def test_a_compressed_frame_of_another_kind_is_refused() -> None:
    with pytest.raises(AdapterError, match="neither JPEG nor PNG"):
        _decode(COMPRESSED_TYPE, _compressed_cdr(b"\0" * 20, "16UC1; compressedDepth"))


def test_a_compressed_frame_over_the_cap_is_refused() -> None:
    with pytest.raises(AdapterError, match="over the 5 byte cap"):
        _decode(COMPRESSED_TYPE, _compressed_cdr(_PNG), max_bytes=5)


@pytest.mark.parametrize("data", [b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"\xff\xd8\xff\xc0\0\0"])
def test_a_cut_header_has_no_size(data: bytes) -> None:
    assert compressed_size(data) == (None, None)


def _png_of(width: int, height: int) -> bytes:
    return _PNG[:16] + struct.pack(">II", width, height) + b"rest"


def test_long_publisher_strings_are_cut_in_the_metadata() -> None:
    capture = _decode(COMPRESSED_TYPE, _compressed_cdr(_PNG, "f" * 100_000, "c" * 100_000))
    assert len(capture.info.frame_id) == 256 and capture.info.frame_id.endswith("...")
    assert len(capture.info.encoding) == 256
    assert len(capture.info.model_dump_json()) < 2_000


@pytest.mark.parametrize("kind", [COMPRESSED_TYPE, RAW_TYPE])
def test_long_publisher_strings_are_cut_in_the_errors(kind: str) -> None:
    long = "x" * 100_000
    compressed = kind == COMPRESSED_TYPE
    cdr = _compressed_cdr(b"\0" * 20, long) if compressed else _raw_cdr(1, 1, long, bytes(3), 3)
    with pytest.raises(AdapterError) as caught:
        _decode(kind, cdr)
    assert len(str(caught.value)) < 700


@pytest.mark.parametrize(
    "data",
    [
        b"\xff\xd8\xff" + b"garbage" * 3,
        b"\x89PNG\r\n\x1a\n" + b"not an IHDR chunk at all",
    ],
)
def test_a_signature_without_a_readable_size_is_refused(data: bytes) -> None:
    with pytest.raises(AdapterError, match="no readable size"):
        _decode(COMPRESSED_TYPE, _compressed_cdr(data))


@pytest.mark.parametrize(
    ("width", "height"), [(100_000, 100_000), (0, 5), (7, 0), (16385, 1), (16384, 16384)]
)
def test_an_implausible_size_is_refused(width: int, height: int) -> None:
    with pytest.raises(AdapterError, match=f"{width}x{height}"):
        _decode(COMPRESSED_TYPE, _compressed_cdr(_png_of(width, height)))


def test_the_largest_plausible_size_passes() -> None:
    assert _decode(COMPRESSED_TYPE, _compressed_cdr(_png_of(8192, 8192))).info.width == 8192


def test_a_payload_that_is_not_an_image_message_is_an_adapter_error() -> None:
    with pytest.raises(AdapterError, match="could not decode"):
        _decode(RAW_TYPE, b"\0\1\0\0garbage")


# ----- raw -----


def test_rgb8_comes_back_equal() -> None:
    pytest.importorskip("PIL")
    data = bytes(range(24))
    capture = _decode(RAW_TYPE, _raw_cdr(4, 2, "rgb8", data, 12))
    assert capture.info.mime_type == "image/png"
    assert capture.info.encoding == "rgb8"
    assert _pixels(capture.data).tobytes() == data


def test_row_padding_is_dropped() -> None:
    pytest.importorskip("PIL")
    data = bytes([1, 2, 3, 99, 4, 5, 6, 99])
    capture = _decode(RAW_TYPE, _raw_cdr(1, 2, "rgb8", data, 4))
    assert _pixels(capture.data).tobytes() == bytes([1, 2, 3, 4, 5, 6])


def test_bgr8_is_reordered_and_mono8_stays_gray() -> None:
    pytest.importorskip("PIL")
    bgr = _decode(RAW_TYPE, _raw_cdr(1, 1, "bgr8", bytes([1, 2, 3]), 3))
    assert _pixels(bgr.data).tobytes() == bytes([3, 2, 1])
    mono = _decode(RAW_TYPE, _raw_cdr(2, 1, "mono8", bytes([7, 8]), 2))
    assert _pixels(mono.data).tobytes() == bytes([7, 8])


@pytest.mark.parametrize(("fmt", "bigendian"), [("<u2", 0), (">u2", 1)])
def test_mono16_scales_from_its_own_range(fmt: str, bigendian: int) -> None:
    pytest.importorskip("PIL")
    data = np.array([1000, 2000, 3000], dtype=fmt).tobytes()
    capture = _decode(RAW_TYPE, _raw_cdr(3, 1, "mono16", data, 6, bigendian))
    assert list(_pixels(capture.data)[0]) == [0, 127, 255]


def test_a_flat_mono16_frame_is_black() -> None:
    pytest.importorskip("PIL")
    data = np.array([5, 5], dtype="<u2").tobytes()
    capture = _decode(RAW_TYPE, _raw_cdr(2, 1, "mono16", data, 4))
    assert list(_pixels(capture.data)[0]) == [0, 0]


def test_16uc1_scales_like_mono16() -> None:
    pytest.importorskip("PIL")
    data = np.array([1000, 3000], dtype="<u2").tobytes()
    capture = _decode(RAW_TYPE, _raw_cdr(2, 1, "16UC1", data, 4))
    assert list(_pixels(capture.data)[0]) == [0, 255]
    assert capture.info.encoding == "16UC1"


def test_rgba8_keeps_its_pixels_and_bgra8_is_reordered() -> None:
    pytest.importorskip("PIL")
    rgba = _decode(RAW_TYPE, _raw_cdr(1, 1, "rgba8", bytes([1, 2, 3, 4]), 4))
    assert _pixels(rgba.data).tobytes() == bytes([1, 2, 3, 4])
    bgra = _decode(RAW_TYPE, _raw_cdr(1, 1, "bgra8", bytes([1, 2, 3, 4]), 4))
    assert _pixels(bgra.data).tobytes() == bytes([3, 2, 1, 4])


def test_a_large_frame_is_resized_keeping_its_shape() -> None:
    pytest.importorskip("PIL")
    capture = _decode(RAW_TYPE, _raw_cdr(640, 480, "rgb8", bytes(640 * 480 * 3), 1920))
    info = capture.info
    assert (info.width, info.height) == (640, 480)
    assert (info.returned_width, info.returned_height) == (512, 384)
    assert _pixels(capture.data).shape == (384, 512, 3)


def test_a_returned_frame_over_the_cap_says_to_lower_max_edge() -> None:
    pytest.importorskip("PIL")
    with pytest.raises(AdapterError, match="lower `max_edge`"):
        _decode(RAW_TYPE, _raw_cdr(4, 2, "rgb8", bytes(24), 12), max_bytes=3)


def test_another_encoding_is_refused_naming_the_supported_ones() -> None:
    with pytest.raises(AdapterError, match="mono16"):
        _decode(RAW_TYPE, _raw_cdr(1, 1, "32FC1", bytes(4), 4))


@pytest.mark.parametrize(
    ("width", "height", "step", "size"), [(0, 1, 3, 3), (2, 1, 3, 6), (2, 2, 6, 6)]
)
def test_inconsistent_sizes_are_refused(width: int, height: int, step: int, size: int) -> None:
    with pytest.raises(AdapterError, match="inconsistent image"):
        _decode(RAW_TYPE, _raw_cdr(width, height, "rgb8", bytes(size), step))


def test_without_pillow_the_error_names_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "PIL", None)
    with pytest.raises(AdapterError, match=r"topicforge\[image\]") as caught:
        _decode(RAW_TYPE, _raw_cdr(1, 1, "rgb8", bytes(3), 3))
    assert "uvx --from" in str(caught.value) and "pip install" in str(caught.value)


# ----- the helper -----


def _capture(
    backend: SamplingBackend, topic_type: str = COMPRESSED_TYPE, publishers: int = 1
) -> Any:
    def info(topic: str, timeout: float) -> TopicInfo:
        return TopicInfo(
            name=topic,
            message_type=topic_type,
            publisher_count=publishers,
            subscriber_count=0,
            mode_effective="live",
        )

    return capture_image(
        "/c",
        topic_info=info,
        mode="live",
        backend=backend,
        max_edge=512,
        max_bytes=10**7,
        timeout_s=1.0,
    )


def test_cdr_frames_are_kept_apart_from_documents() -> None:
    run = EchoRun()
    _add_frame(run, Frame(KIND_MESSAGE, {"wall_ns": 5, "encoding": ENCODING_CDR}, b"\0\1"))
    assert run.cdr_frames == [(b"\0\1", 5)] and run.documents == []


def test_the_helper_sends_raw_frames_as_a_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHONPATH", str(_FAKE))
    monkeypatch.setenv("FAKE_RCLPY_MESSAGES", "3")
    cmd = helper_command(
        sys.executable, "/c", COMPRESSED_TYPE, ["--raw", "--count", "1", "--deadline", "4"]
    )
    run = stream_helper(cmd, count=1, deadline_s=4.0)
    assert run.exit_code is None, run.stderr_tail
    assert run.cdr_frames[0][0] == b"0" and run.cdr_frames[0][1] > 0


def test_the_helper_counts_an_oversized_raw_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHONPATH", str(_FAKE))
    monkeypatch.setenv("FAKE_RCLPY_MESSAGES", "1")
    cmd = helper_command(
        sys.executable,
        "/c",
        COMPRESSED_TYPE,
        ["--raw", "--count", "1", "--deadline", "1", "--max-chars", "0"],
    )
    run = stream_helper(cmd, count=1, deadline_s=1.0)
    assert run.cdr_frames == [] and run.oversized == 1


@pytest.mark.parametrize(
    "backend",
    [SamplingBackend("cli", note="TOPICFORGE_ROS_SAMPLER=cli"), SamplingBackend("unavailable")],
)
def test_without_the_helper_it_raises_naming_the_setting(backend: SamplingBackend) -> None:
    with pytest.raises(AdapterError, match="TOPICFORGE_ROS_SAMPLER"):
        _capture(backend)


def test_a_non_image_topic_is_refused() -> None:
    backend = SamplingBackend("rclpy", python=sys.executable)
    with pytest.raises(AdapterError, match="sample_messages"):
        _capture(backend, "sensor_msgs/msg/LaserScan")


@pytest.mark.parametrize(
    ("publishers", "reliability", "needle"),
    [
        (0, "reliable", "no publisher"),
        (1, "best_effort", "best-effort"),
        (1, "reliable", "idle or slower"),
    ],
)
def test_nothing_arriving_says_why(publishers: int, reliability: str, needle: str) -> None:
    qos = ["--qos-reliability", reliability]
    assert needle in _nothing_arrived("/c", publishers, 1.0, qos)


@pytest.mark.parametrize("publishers", [1, 0])
def test_the_lookup_comes_out_of_the_wait(monkeypatch: pytest.MonkeyPatch, publishers: int) -> None:
    seen: dict[str, float] = {}

    def stream(cmd: list[str], *, count: int, deadline_s: float) -> EchoRun:
        seen["deadline_s"] = deadline_s
        return EchoRun()

    def slow_info(topic: str, timeout: float) -> TopicInfo:
        time.sleep(0.2)
        return TopicInfo(
            name=topic,
            message_type=COMPRESSED_TYPE,
            publisher_count=publishers,
            subscriber_count=0,
            mode_effective="live",
        )

    monkeypatch.setattr(image_capture, "stream_helper", stream)
    with pytest.raises(AdapterError, match="No image arrived"):
        image_capture.capture_image(
            "/c",
            topic_info=slow_info,
            mode="live",
            backend=SamplingBackend("rclpy", python=sys.executable),
            max_edge=512,
            max_bytes=10**7,
            timeout_s=1.0,
        )
    assert 0.5 <= seen["deadline_s"] <= 0.85


def test_a_silent_topic_is_an_error_not_an_empty_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHONPATH", str(_FAKE))
    with pytest.raises(AdapterError, match="No image arrived"):
        _capture(SamplingBackend("rclpy", python=sys.executable))


def test_a_failed_helper_says_why(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHONPATH", str(_FAKE))
    monkeypatch.setenv("FAKE_RCLPY_FAIL", "boom")
    with pytest.raises(AdapterError, match="boom"):
        _capture(SamplingBackend("rclpy", python=sys.executable))


# ----- service, mock, MCP -----


@pytest.mark.parametrize("max_edge", [0, 15, 2049, True])
def test_the_service_refuses_a_bad_max_edge(max_edge: int) -> None:
    with pytest.raises(AdapterError, match="max_edge"):
        Inspector(MockAdapter()).get_image("/camera/image_raw", max_edge=max_edge)


def test_the_cap_passed_down_is_the_call_ceiling() -> None:
    seen: dict[str, Any] = {}

    class Recorder(MockAdapter):
        def get_image(self, topic: str, **kw: Any) -> Any:
            seen.update(kw)
            return super().get_image(topic, **kw)

    Inspector(Recorder(), max_sample_bytes=1000).get_image("/camera/image_raw")
    assert seen == {"max_edge": 512, "max_bytes": 4000, "timeout_s": 10.0}


def test_the_mock_is_deterministic_and_refuses_other_types() -> None:
    first = MockAdapter().get_image("/camera/image_raw", max_edge=512, max_bytes=1, timeout_s=1.0)
    again = MockAdapter().get_image("/camera/image_raw", max_edge=512, max_bytes=1, timeout_s=1.0)
    assert first == again
    assert first.data.startswith(b"\x89PNG")
    assert first.info.mode_effective == "mock"
    with pytest.raises(AdapterError, match="LaserScan"):
        MockAdapter().get_image("/scan", max_edge=512, max_bytes=1, timeout_s=1.0)


def _app() -> Any:
    return build_app(
        Settings(mode="mock", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False)
    )


def test_the_tool_returns_an_image_block_a_text_block_and_structured_content() -> None:
    result = asyncio.run(_app().call_tool("get_image", {"topic": "/camera/image_raw"}))
    image, text = result.content
    assert image.type == "image" and image.mime_type == "image/png"
    data = base64.b64decode(image.data)
    assert data.startswith(b"\x89PNG")
    assert json.loads(text.text) == result.structured_content
    assert result.structured_content["sha256"] == hashlib.sha256(data).hexdigest()
    assert result.structured_content["mode_effective"] == "mock"


def test_the_tool_declares_no_output_schema_on_purpose() -> None:
    tools = {t.name: t for t in asyncio.run(_app().list_tools())}
    assert tools["get_image"].output_schema is None
    assert set(tools["get_image"].input_schema["properties"]) == {"topic", "max_edge"}
