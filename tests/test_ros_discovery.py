"""`ros_discovery_info` decoding, node-name cache and ghost-CLI identification (no binding)."""

from __future__ import annotations

import random
import struct
import sys
from pathlib import Path

import pytest

from topicforge.adapters.common import spawned_pids
from topicforge.adapters.common.discovery_tracker import (
    BufferedTake,
    DiscoveryCaches,
    DiscoveryTracker,
    apply_builtin_samples,
)
from topicforge.adapters.common.ros_discovery import (
    NodeNameCache,
    RosDiscoveryError,
    apply_info_samples,
    cli_ghost_note,
    cli_pid_of_node,
    decode_participant_entities,
    fast_pid_bits,
    flag_topicforge_cli,
    gid_width_order,
    node_full_name,
    prefix_key_of_guid,
    probable_ghost_count,
    try_decode_participant_entities,
    with_node_names,
)
from topicforge.models import ParticipantInfo

FIXTURE = Path(__file__).parent / "fixtures" / "ros_discovery" / "humble_two_nodes.hex"

# ----- a reference encoder, independent of the decoder under test -----


class _Enc:
    def __init__(self, little: bool) -> None:
        self.buf = bytearray()
        self.fmt = "<I" if little else ">I"

    def align(self, n: int) -> None:
        self.buf += b"\x00" * ((-len(self.buf)) % n)

    def u32(self, v: int) -> None:
        self.align(4)
        self.buf += struct.pack(self.fmt, v)

    def string(self, s: str) -> None:
        raw = s.encode() + b"\x00"
        self.u32(len(raw))
        self.buf += raw


def encode(
    nodes: list[tuple[str, str, int, int]],
    *,
    width: int = 24,
    little: bool = True,
    gid: bytes | None = None,
    pad: int = 0,
) -> bytes:
    """XCDR1 `ParticipantEntitiesInfo`; each node is (namespace, name, n_readers, n_writers)."""
    enc = _Enc(little)
    gid = gid or bytes(range(1, 17)).ljust(width, b"\x00")
    enc.buf += gid[:width].ljust(width, b"\x00")
    enc.u32(len(nodes))
    for ns, name, readers, writers in nodes:
        enc.string(ns)
        enc.string(name)
        for count in (readers, writers):
            enc.u32(count)
            enc.buf += bytes(range(count * width)) if count else b""
    header = bytes([0, 1 if little else 0, 0, 0])
    return header + bytes(enc.buf) + b"\x00" * pad


# ----- decoder -----


@pytest.mark.parametrize("width", [24, 16])
@pytest.mark.parametrize("little", [True, False])
def test_both_gid_widths_and_endiannesses_decode(width: int, little: bool) -> None:
    data = encode([("/", "a", 2, 1), ("/ns/sub", "talker", 0, 3)], width=width, little=little)
    info = decode_participant_entities(data, distro="humble" if width == 24 else "jazzy")
    assert info.gid_width == width
    assert info.node_names == ["/a", "/ns/sub/talker"]
    assert [len(n.reader_gids) for n in info.nodes] == [2, 0]
    assert [len(n.writer_gids) for n in info.nodes] == [1, 3]
    assert all(len(g) == width for n in info.nodes for g in n.reader_gids)


@pytest.mark.parametrize("hint", ["humble", "jazzy", "rolling", None, "nonsense"])
@pytest.mark.parametrize("width", [24, 16])
def test_the_wrong_distro_hint_still_finds_the_width(width: int, hint: str | None) -> None:
    data = encode([("/robot", "lidar", 1, 2), ("/robot", "camera", 1, 1)], width=width)
    info = decode_participant_entities(data, distro=hint)
    assert info.gid_width == width
    assert info.node_names == ["/robot/lidar", "/robot/camera"]


def test_empty_participant_decodes_to_no_nodes() -> None:
    for width in (24, 16):
        info = decode_participant_entities(encode([], width=width))
        assert info.nodes == () and info.gid_width == width


def test_trailing_padding_is_accepted() -> None:
    assert decode_participant_entities(encode([("/", "a", 1, 1)], pad=3)).node_names == ["/a"]


def test_the_real_humble_capture_decodes() -> None:
    data = bytes.fromhex(FIXTURE.read_text().strip())
    info = decode_participant_entities(data)
    assert info.gid_width == 24
    assert info.node_names == ["/demo/talker", "/demo/sub/listener"]
    assert info.gid[:2] == b"\x01\x0f"
    assert info.prefix_key == "010fe8e0.f2017f1f.00000000"
    assert [(len(n.reader_gids), len(n.writer_gids)) for n in info.nodes] == [(6, 9), (7, 8)]
    # The hint is only an order: the same bytes decode with the Jazzy hint.
    assert decode_participant_entities(data, distro="jazzy").gid_width == 24


def test_the_real_jazzy_capture_decodes_with_a_16_byte_gid() -> None:
    path = FIXTURE.with_name("jazzy_two_nodes.hex")
    data = bytes.fromhex(path.read_text().strip())
    for hint in ("jazzy", "humble", None):
        info = decode_participant_entities(data, distro=hint)
        assert info.gid_width == 16
        assert info.node_names == ["/demo/talker", "/demo/sub/listener"]
        assert info.prefix_key.startswith("010fe8e0.")


def test_every_truncation_of_a_valid_message_fails_cleanly() -> None:
    data = encode([("/ns", "a", 2, 2), ("/", "b", 1, 0)])
    for cut in range(len(data) - 3):
        info = try_decode_participant_entities(data[:cut])
        if info is None:
            with pytest.raises(RosDiscoveryError):
                decode_participant_entities(data[:cut])
        else:
            # Cut inside the zero padding of a 24-byte gid: an empty 16-byte message.
            assert info.nodes == () and info.gid_width == 16 and cut <= 27


def test_garbage_never_crashes_the_decoder() -> None:
    rng = random.Random(7)
    for _ in range(300):
        blob = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 200)))
        blob = bytes([0, rng.choice([0, 1])]) + blob  # plausible header, junk body
        try_decode_participant_entities(blob)


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\x00\x01",
        bytes([0, 3, 0, 0]) + bytes(40),  # PL_CDR encapsulation: not what ROS sends
        bytes([0, 1, 0, 0]) + bytes(40),  # zero string length
    ],
)
def test_rejected_inputs_raise_a_clear_error(data: bytes) -> None:
    with pytest.raises(RosDiscoveryError):
        decode_participant_entities(data)


def test_a_namespace_without_a_leading_slash_is_rejected() -> None:
    with pytest.raises(RosDiscoveryError, match="implausible node"):
        decode_participant_entities(encode([("ns", "a", 0, 0)]))


def test_gid_width_order_follows_the_distro(monkeypatch: pytest.MonkeyPatch) -> None:
    assert gid_width_order("humble") == (24, 16)
    assert gid_width_order("Jazzy") == (16, 24)
    monkeypatch.setenv("ROS_DISTRO", "rolling")
    assert gid_width_order() == (16, 24)
    monkeypatch.delenv("ROS_DISTRO")
    assert gid_width_order() == (24, 16)


def test_node_full_name() -> None:
    assert node_full_name("/", "a") == "/a"
    assert node_full_name("/ns", "a") == "/ns/a"
    assert node_full_name("/ns/", "a") == "/ns/a"


# ----- cache and listing -----


def _participant(guid: str, **kw: object) -> ParticipantInfo:
    return ParticipantInfo(guid=guid, vendor=str(kw.pop("vendor", "cyclone")), domain_id=0, **kw)  # type: ignore[arg-type]


def test_cache_keeps_the_latest_message_per_participant() -> None:
    cache = NodeNameCache()
    first = encode([("/", "a", 0, 0)], gid=bytes(range(16)))
    second = encode([("/", "a", 0, 0), ("/", "b", 0, 0)], gid=bytes(range(16)))
    assert apply_info_samples(cache, [first, b"junk", second]) == 2
    assert cache.as_dict() == {"00010203.04050607.08090a0b": ("/a", "/b")}


def test_cache_is_bounded() -> None:
    cache = NodeNameCache(max_items=2)
    for i in range(3):
        cache.put(str(i), ["/n"])
    assert list(cache.as_dict()) == ["1", "2"]


def test_with_node_names_fills_only_matching_participants() -> None:
    a = _participant("00010203.04050607.08090a0b.000001c1")
    b = _participant("ffffffff.00000000.00000000.000001c1")
    out = with_node_names([a, b], {"00010203.04050607.08090a0b": ("/z", "/a")})
    assert out[0].node_names == ["/a", "/z"]
    assert out[0].node_names_source == "ros_discovery_info"
    assert out[1] == b and out[1].node_names_source == "none"


def test_prefix_key_of_guid() -> None:
    assert prefix_key_of_guid("AA000000.bb000000.cc000000.000001c1") == (
        "aa000000.bb000000.cc000000"
    )


# ----- tracker integration -----


def _tracker_pass(caches: DiscoveryCaches, infos: list[bytes], parts: list[object] | None = None):
    apply_builtin_samples(caches, parts or [], [], [], 1, domain_id=0, info_samples=infos)


def test_apply_builtin_samples_caches_node_names() -> None:
    caches = DiscoveryCaches()
    _tracker_pass(caches, [encode([("/", "a", 0, 0)], gid=bytes(range(16)))])
    assert caches.snapshot(0).node_names == {"00010203.04050607.08090a0b": ("/a",)}


def test_a_departed_participant_keeps_its_node_names() -> None:
    caches = DiscoveryCaches()
    key = "00010203.04050607.08090a0b"
    _tracker_pass(caches, [encode([("/", "a", 0, 0)], gid=bytes(range(16)))])
    caches.departed_participants[f"{key}.000001c1"] = (1, None)
    _tracker_pass(caches, [])
    assert caches.snapshot(0).node_names[key] == ("/a",)


def test_tracker_accepts_three_or_four_lists() -> None:
    caches = DiscoveryCaches()
    three = DiscoveryTracker(lambda: ([], [], []), caches, domain_id=0)
    three.run_pass()
    four = DiscoveryTracker(
        lambda: ([], [], [], [encode([("/", "x", 0, 0)], gid=bytes(range(16)))]),
        caches,
        domain_id=0,
    )
    four.run_pass()
    assert caches.snapshot(0).node_names == {"00010203.04050607.08090a0b": ("/x",)}


def test_buffered_take_keeps_a_fourth_list_across_a_failure() -> None:
    calls = {"n": 0}

    def info(sink: list) -> None:
        sink.append(b"x")
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")

    take = BufferedTake(lambda s: None, lambda s: None, lambda s: None, info)
    with pytest.raises(RuntimeError):
        take()
    assert take()[3] == [b"x", b"x"]


# ----- ghost CLI participants -----

NOW = 1_000_000_000_000_000_000
SEC = 1_000_000_000


def test_cli_pid_of_node() -> None:
    assert cli_pid_of_node("/_ros2cli_4242") == 4242
    assert cli_pid_of_node("/ns/_ros2cli_7") == 7
    assert cli_pid_of_node("/_ros2cli_daemon_0") is None
    assert cli_pid_of_node("/talker") is None


def test_fast_pid_bits_reads_bytes_4_and_5() -> None:
    # Real capture: nodes process 459 = 0x01cb, GUID 010fe8e0.cb01af68...
    assert fast_pid_bits("010fe8e0.cb01af68.00000000.000001c1") == 459
    assert fast_pid_bits("0110d63c.cb01af68.00000000.000001c1") is None  # not eProsima
    assert fast_pid_bits("junk") is None


def test_name_match_flags_a_recent_spawn_only() -> None:
    ghost = _participant("a.b.c.d", node_names=["/_ros2cli_100"], node_names_source="ros2_cli")
    other = _participant("e.f.g.h", node_names=["/_ros2cli_999"], node_names_source="ros2_cli")
    robot = _participant("i.j.k.l", node_names=["/talker"])
    out = flag_topicforge_cli([ghost, other, robot], [(100, NOW - 5 * SEC)], now_ns=NOW)
    assert [p.is_topicforge_cli for p in out] == [True, False, False]


def test_spawns_older_than_the_window_are_forgotten() -> None:
    ghost = _participant("a.b.c.d", node_names=["/_ros2cli_100"])
    out = flag_topicforge_cli([ghost], [(100, NOW - 31 * SEC)], now_ns=NOW)
    assert not out[0].is_topicforge_cli


def test_fast_guid_pid_bits_flag_without_a_node_name() -> None:
    fast = _participant("010fe8e0.cb01af68.00000000.000001c1", vendor="fast", hostname="Box")
    cyclone = _participant("0110d63c.cb01af68.00000000.000001c1", vendor="cyclone", hostname="box")
    out = flag_topicforge_cli([fast, cyclone], [(459 + 65536, NOW)], now_ns=NOW, local_host="box")
    assert [p.is_topicforge_cli for p in out] == [True, False]


def test_pid_bits_alone_do_not_flag_when_the_host_is_unknown() -> None:
    fast = _participant("010fe8e0.cb01af68.00000000.000001c1", vendor="fast")
    out = flag_topicforge_cli([fast], [(459, NOW)], now_ns=NOW, local_host="box")
    assert not out[0].is_topicforge_cli


def test_same_pid_on_another_host_is_not_flagged() -> None:
    remote = _participant("a.b.c.d", hostname="other", node_names=["/_ros2cli_100", "/n"])
    local = _participant("e.f.g.h", hostname="box", node_names=["/_ros2cli_100"])
    out = flag_topicforge_cli(
        [remote, local], [(100, NOW)], now_ns=NOW, extra_pids={"a.b.c.d": 100}, local_host="box"
    )
    assert [p.is_topicforge_cli for p in out] == [False, True]


def test_declared_pid_flags_a_cyclone_participant() -> None:
    p = _participant("0110d63c.f086f4cc.a8799a7e.000001c1", hostname="box")
    out = flag_topicforge_cli(
        [p], [(4321, NOW)], now_ns=NOW, extra_pids={p.guid: 4321}, local_host="box"
    )
    assert out[0].is_topicforge_cli
    out = flag_topicforge_cli(
        [p], [(4321, NOW)], now_ns=NOW, extra_pids={p.guid: 1}, local_host="box"
    )
    assert not out[0].is_topicforge_cli


def test_the_observer_is_never_flagged_and_nothing_is_excluded() -> None:
    me = _participant("a.b.c.d", is_observer=True, node_names=["/_ros2cli_5"])
    other = _participant("e.f.g.h")
    out = flag_topicforge_cli([me, other], [(5, NOW)], now_ns=NOW)
    assert len(out) == 2 and not any(p.is_topicforge_cli for p in out)


def test_no_spawns_returns_the_list_untouched() -> None:
    ps = [_participant("a.b.c.d")]
    assert flag_topicforge_cli(ps, [], now_ns=NOW) == ps


def test_time_correlation_counts_unidentified_candidates_for_the_note() -> None:
    near = _participant("a.b.c.d", status="active", first_seen_ns=NOW + SEC)
    early = _participant("e.f.g.h", status="active", first_seen_ns=NOW - SEC)
    late = _participant("i.j.k.l", status="active", first_seen_ns=NOW + 20 * SEC)
    flagged = _participant(
        "m.n.o.p", status="active", first_seen_ns=NOW + SEC, is_topicforge_cli=True
    )
    left = _participant("q.r.s.t", status="left", first_seen_ns=NOW + SEC)
    ps = [near, early, late, flagged, left]
    assert probable_ghost_count(ps, [(1, NOW)], now_ns=NOW + 2 * SEC) == 1


def test_ghost_note_text() -> None:
    assert cli_ghost_note(0, 0) is None
    assert "is_topicforge_cli" in (cli_ghost_note(2, 0) or "")
    assert "may be its ghosts" in (cli_ghost_note(0, 1) or "")


def test_run_process_records_the_pid_it_spawned() -> None:
    from topicforge.adapters.ros2_live.process_runner import run_process

    spawned_pids.clear_spawns()
    result = run_process([sys.executable, "-c", "pass"], deadline_s=20)
    assert result.returncode == 0
    assert len(spawned_pids.recent_spawns()) == 1
    spawned_pids.clear_spawns()


def test_spawn_ring_is_bounded() -> None:
    spawned_pids.clear_spawns()
    for i in range(spawned_pids.MAX_SPAWNS + 10):
        spawned_pids.record_spawn(i, now_ns=i)
    ring = spawned_pids.recent_spawns()
    assert len(ring) == spawned_pids.MAX_SPAWNS and ring[-1][0] == spawned_pids.MAX_SPAWNS + 9
    spawned_pids.clear_spawns()


# ----- endpoint gid -> node name -----


def _encode_nodes(
    nodes: list[tuple[str, str, list[bytes], list[bytes]]], gid: bytes, width: int
) -> bytes:
    """XCDR1 little-endian message whose nodes carry explicit reader and writer gids."""
    enc = _Enc(True)
    enc.buf += gid[:width].ljust(width, b"\x00")
    enc.u32(len(nodes))
    for ns, name, readers, writers in nodes:
        enc.string(ns)
        enc.string(name)
        for gids in (readers, writers):
            enc.u32(len(gids))
            for g in gids:
                enc.buf += g[:width].ljust(width, b"\x00")
    return bytes([0, 1, 0, 0]) + bytes(enc.buf)


_PREFIX = bytes.fromhex("010fe8e0f2017f1f00000000")


def _guid(entity: int) -> bytes:
    return _PREFIX + entity.to_bytes(4, "big")


@pytest.mark.parametrize("width", [24, 16])
def test_endpoint_guid_is_the_first_sixteen_bytes_of_a_gid(width: int) -> None:
    data = _encode_nodes(
        [
            ("/", "bench_robot", [_guid(0x704)], [_guid(0x504)]),
            ("/ns", "other", [], [_guid(0x904)]),
        ],
        _PREFIX + (0x1C1).to_bytes(4, "big"),
        width,
    )
    info = decode_participant_entities(data)
    assert info.gid_width == width
    assert info.endpoint_nodes == {
        "010fe8e0.f2017f1f.00000000.00000704": "/bench_robot",
        "010fe8e0.f2017f1f.00000000.00000504": "/bench_robot",
        "010fe8e0.f2017f1f.00000000.00000904": "/ns/other",
    }


@pytest.mark.parametrize("name", ["humble_two_nodes.hex", "jazzy_two_nodes.hex"])
def test_the_real_captures_map_every_endpoint_inside_the_participant_prefix(name: str) -> None:
    info = decode_participant_entities(bytes.fromhex(FIXTURE.with_name(name).read_text().strip()))
    mapped = info.endpoint_nodes
    assert len(mapped) == sum(len(n.reader_gids) + len(n.writer_gids) for n in info.nodes)
    assert all(guid.startswith(info.prefix_key + ".") for guid in mapped)
    assert set(mapped.values()) == {"/demo/talker", "/demo/sub/listener"}


def test_the_cache_keeps_endpoint_nodes_after_the_final_empty_message() -> None:
    cache = NodeNameCache()
    gid = _PREFIX + (0x1C1).to_bytes(4, "big")
    full = _encode_nodes([("/", "a", [], [_guid(0x504)])], gid, 16)
    apply_info_samples(cache, [full])
    assert cache.endpoint_nodes() == {"010fe8e0.f2017f1f.00000000.00000504": "/a"}
    apply_info_samples(cache, [_encode_nodes([], gid, 16)])
    assert cache.as_dict()["010fe8e0.f2017f1f.00000000"] == ()
    assert cache.endpoint_nodes() == {"010fe8e0.f2017f1f.00000000.00000504": "/a"}


def test_the_cache_replaces_endpoint_nodes_with_the_latest_message_and_stays_bounded() -> None:
    cache = NodeNameCache(max_items=1)
    gid = _PREFIX + (0x1C1).to_bytes(4, "big")
    apply_info_samples(cache, [_encode_nodes([("/", "a", [], [_guid(1)])], gid, 16)])
    apply_info_samples(cache, [_encode_nodes([("/", "a", [], [_guid(2)])], gid, 16)])
    assert list(cache.endpoint_nodes()) == ["010fe8e0.f2017f1f.00000000.00000002"]
    other = bytes(range(16))
    apply_info_samples(cache, [_encode_nodes([("/", "b", [], [other])], other, 16)])
    assert cache.endpoint_nodes() == {"00010203.04050607.08090a0b.0c0d0e0f": "/b"}


def test_the_tracker_snapshot_carries_endpoint_nodes() -> None:
    caches = DiscoveryCaches()
    gid = _PREFIX + (0x1C1).to_bytes(4, "big")
    _tracker_pass(caches, [_encode_nodes([("/", "bench_robot", [], [_guid(0x504)])], gid, 24)])
    assert caches.snapshot(0).endpoint_nodes == {
        "010fe8e0.f2017f1f.00000000.00000504": "/bench_robot"
    }
