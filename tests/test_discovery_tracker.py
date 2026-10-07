"""Tests for continuous discovery tracking: the pure pass and the thread wrapper.

Synthetic duck-typed builtin samples only (valid and invalid, with an integer
`instance_state` like the binding): no DDS binding required.
"""

from __future__ import annotations

import contextlib
import threading
import time
import uuid
from types import SimpleNamespace
from typing import Any

from topicforge.adapters.common import (
    DiscoveryCaches,
    DiscoveryTracker,
    apply_builtin_samples,
)

SEC = 1_000_000_000
T0 = 1_700_000_000 * SEC
ALIVE, DISPOSED, NO_WRITERS = 16, 32, 64


def _key(n: int) -> uuid.UUID:
    # First two bytes 01.10: the Eclipse (cyclone) vendor id.
    return uuid.UUID(bytes=bytes([0x01, 0x10]) + n.to_bytes(14, "big"))


def _participant(n: int, *, name: str | None = None, ts: int = T0, state: int = ALIVE) -> Any:
    qos = []
    if name:
        policy = type("EntityName", (), {})()
        policy.name = name
        qos = [policy]
    return SimpleNamespace(
        key=_key(n),
        qos=qos,
        sample_info=SimpleNamespace(valid_data=True, instance_state=state, source_timestamp=ts),
    )


def _invalid(n: int, *, ts: int, state: int = DISPOSED) -> Any:
    """What a builtin reader returns once an instance is gone: key + sample_info only."""
    return SimpleNamespace(
        key=_key(n),
        sample_info=SimpleNamespace(valid_data=False, instance_state=state, source_timestamp=ts),
    )


def _endpoint(n: int, topic: str = "/t", state: int = ALIVE) -> Any:
    return SimpleNamespace(
        key=_key(n),
        participant_key=_key(1),
        topic_name=topic,
        type_name="T",
        qos=[],
        sample_info=SimpleNamespace(valid_data=True, instance_state=state, source_timestamp=T0),
    )


def _apply(caches: DiscoveryCaches, parts=(), pubs=(), subs=(), now: int = T0) -> None:
    apply_builtin_samples(caches, list(parts), list(pubs), list(subs), now, domain_id=0)


def _events(caches: DiscoveryCaches) -> list[Any]:
    return list(reversed(caches.lifecycle.events_since(lookback_seconds=86400, now_ns=T0 + SEC)))


def test_valid_participant_is_cached_and_discovered_at_dds_time() -> None:
    caches = DiscoveryCaches()
    _apply(caches, [_participant(2, name="nav", ts=T0 - 2 * SEC)], now=T0)

    assert len(caches.participants.values()) == 1
    (event,) = _events(caches)
    assert event.event_type == "discovered"
    assert event.timestamp_ns == T0 - 2 * SEC
    assert event.time_source == "dds_source_timestamp"
    assert event.observed_ns == T0
    (info,) = caches.lifecycle.snapshot_participants()
    assert (info.name, info.status, info.announced_ns) == ("nav", "active", T0 - 2 * SEC)


def test_dispose_records_loss_at_source_timestamp_not_observation_time() -> None:
    caches = DiscoveryCaches()
    _apply(caches, [_participant(2, name="nav", ts=T0)], now=T0)
    _apply(caches, [_invalid(2, ts=T0 + 10 * SEC)], now=T0 + 40 * SEC)

    assert caches.participants.values() == []
    lost = _events(caches)[-1]
    assert lost.event_type == "lost"
    assert lost.timestamp_ns == T0 + 10 * SEC
    assert lost.observed_ns == T0 + 40 * SEC
    assert lost.time_source == "dds_source_timestamp"
    assert lost.name == "nav"
    (info,) = caches.lifecycle.snapshot_participants()
    assert (info.status, info.lost_ns, info.lost_time_source) == (
        "left",
        T0 + 10 * SEC,
        "dds_source_timestamp",
    )


def test_no_writers_state_counts_as_loss() -> None:
    caches = DiscoveryCaches()
    _apply(caches, [_participant(2)])
    _apply(caches, [_invalid(2, ts=T0 + SEC, state=NO_WRITERS)], now=T0 + 2 * SEC)

    assert _events(caches)[-1].event_type == "lost"


def test_three_restarts_across_passes_give_four_discovered_and_three_lost_in_order() -> None:
    caches = DiscoveryCaches()
    for i in range(4):
        birth = T0 + i * 10 * SEC
        if i:
            _apply(caches, [_invalid(9 + i, ts=birth - 4 * SEC)], now=birth - 3 * SEC)
        _apply(caches, [_participant(10 + i, name="nav_planner", ts=birth)], now=birth + SEC)

    events = _events(caches)
    assert [e.event_type for e in events] == ["discovered"] + ["lost", "discovered"] * 3
    assert {e.name for e in events} == {"nav_planner"}
    assert len({e.guid for e in events}) == 4
    stamps = [e.timestamp_ns for e in events]
    assert stamps == sorted(stamps)
    assert [p.status for p in caches.lifecycle.snapshot_participants()] == [
        "left",
        "left",
        "left",
        "active",
    ]


def test_three_restarts_inside_one_pass_still_give_four_discovered_and_three_lost() -> None:
    caches = DiscoveryCaches()
    batch = [
        _participant(
            10 + i, name="nav_planner", ts=T0 + i * SEC, state=ALIVE if i == 3 else DISPOSED
        )
        for i in range(4)
    ]
    _apply(caches, batch, now=T0 + 60 * SEC)

    events = _events(caches)
    assert [e.event_type for e in events] == ["discovered", "lost"] * 3 + ["discovered"]
    assert {e.name for e in events} == {"nav_planner"}
    assert all(e.time_source == "observed_local" for e in events if e.event_type == "lost")


def test_born_and_dead_within_one_pass_still_reports_the_pair() -> None:
    caches = DiscoveryCaches()
    # Valid sample whose instance is already disposed: no dispose time known.
    _apply(caches, [_participant(2, name="blink", ts=T0 - SEC, state=DISPOSED)], now=T0)

    assert [e.event_type for e in _events(caches)] == ["discovered", "lost"]
    assert _events(caches)[-1].time_source == "observed_local"
    assert caches.participants.values() == []


def test_dispose_of_a_participant_never_announced_emits_pair_with_unknown_name() -> None:
    caches = DiscoveryCaches()
    _apply(caches, [_invalid(7, ts=T0 + SEC)], now=T0 + 2 * SEC)

    events = _events(caches)
    assert [e.event_type for e in events] == ["discovered", "lost"]
    assert events[0].name is None
    assert events[0].vendor == "cyclone"
    assert events[1].timestamp_ns == T0 + SEC


def test_repeated_dispose_is_idempotent() -> None:
    caches = DiscoveryCaches()
    _apply(caches, [_participant(2)])
    _apply(caches, [_invalid(2, ts=T0 + SEC)])
    _apply(caches, [_invalid(2, ts=T0 + 2 * SEC)])

    assert [e.event_type for e in _events(caches)].count("lost") == 1


def test_endpoint_caches_follow_announce_and_dispose() -> None:
    caches = DiscoveryCaches()
    _apply(caches, pubs=[_endpoint(5), _endpoint(6)], subs=[_endpoint(8)])
    assert len(caches.publications.values()) == 2
    assert len(caches.subscriptions.values()) == 1

    _apply(caches, pubs=[_invalid(5, ts=T0)], subs=[_endpoint(8, state=DISPOSED)])
    assert len(caches.publications.values()) == 1
    assert caches.subscriptions.values() == []


def test_cached_sample_is_replaced_by_the_latest_one() -> None:
    caches = DiscoveryCaches()
    _apply(caches, pubs=[_endpoint(5, topic="/a")])
    _apply(caches, pubs=[_endpoint(5, topic="/b")])

    (sample,) = caches.publications.values()
    assert sample.topic_name == "/b"


def test_rejoin_of_a_left_guid_clears_the_loss() -> None:
    caches = DiscoveryCaches()
    _apply(caches, [_participant(2)])
    _apply(caches, [_invalid(2, ts=T0 + SEC)])
    _apply(caches, [_participant(2, ts=T0 + 5 * SEC)], now=T0 + 6 * SEC)

    (info,) = caches.lifecycle.snapshot_participants()
    assert (info.status, info.lost_ns, info.lost_time_source) == ("active", None, None)


# ----- thread wrapper -----


def _empty() -> tuple[list[Any], list[Any], list[Any]]:
    return [], [], []


def test_run_pass_counts_and_stamps_success() -> None:
    tracker = DiscoveryTracker(_empty, DiscoveryCaches(), domain_id=0, clock_ns=lambda: 42)
    tracker.run_pass()

    status = tracker.status()
    assert (status["passes"], status["errors"], status["last_pass_ns"]) == (1, 0, 42)
    assert status["running"] is False


def test_failing_pass_is_swallowed_counted_and_the_next_one_recovers() -> None:
    calls = {"n": 0}

    def flaky() -> tuple[list[Any], list[Any], list[Any]]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("binding hiccup")
        return [_participant(2)], [], []

    caches = DiscoveryCaches()
    tracker = DiscoveryTracker(flaky, caches, domain_id=0)
    tracker.run_pass()
    tracker.run_pass()

    status = tracker.status()
    assert (status["passes"], status["errors"]) == (1, 1)
    assert "binding hiccup" in status["last_error"]
    assert len(caches.participants.values()) == 1


def test_thread_runs_until_stopped_and_does_not_leak() -> None:
    ticked = threading.Event()

    def take() -> tuple[list[Any], list[Any], list[Any]]:
        ticked.set()
        return _empty()

    def discovery_threads() -> set[threading.Thread]:
        return {t for t in threading.enumerate() if t.name == "topicforge-discovery"}

    before = discovery_threads()  # other adapters may own threads; count only ours
    tracker = DiscoveryTracker(take, DiscoveryCaches(), domain_id=0, period_s=0.01)
    tracker.start()
    tracker.start()  # idempotent: still one thread
    assert ticked.wait(2)
    assert len(discovery_threads() - before) == 1
    assert tracker.status()["running"] is True

    tracker.stop()
    deadline = time.monotonic() + 2
    while tracker.status()["running"] and time.monotonic() < deadline:
        time.sleep(0.01)
    assert tracker.status()["running"] is False
    assert not discovery_threads() - before


def test_participant_dispose_moves_its_endpoints_to_departed() -> None:
    caches = DiscoveryCaches()
    _apply(caches, parts=[_participant(1, name="safety_monitor")], pubs=[_endpoint(5, "estop")])
    assert caches.departed.records(T0) == []
    _apply(caches, parts=[_invalid(1, ts=T0 + 5 * SEC)], now=T0 + 6 * SEC)
    assert caches.publications.values() == []
    ((guid, rec),) = caches.departed.records(T0 + 6 * SEC)
    assert rec.role == "writer" and rec.participant_name == "safety_monitor"
    assert rec.gone_ns == T0 + 5 * SEC and guid.endswith("5")


def test_endpoint_disposed_just_before_its_participant_still_departs() -> None:
    caches = DiscoveryCaches()
    _apply(caches, parts=[_participant(1, name="n")], subs=[_endpoint(8, "estop")])
    _apply(caches, subs=[_invalid(8, ts=T0 + SEC)], now=T0 + 2 * SEC)
    _apply(caches, parts=[_invalid(1, ts=T0 + 2 * SEC)], now=T0 + 3 * SEC)
    ((_, rec),) = caches.departed.records(T0 + 3 * SEC)
    assert rec.role == "reader" and rec.participant_name == "n"


def test_departed_store_is_bounded_and_expires() -> None:
    caches = DiscoveryCaches()
    from topicforge.adapters.common.discovery_tracker import DepartedRecord, DepartedStore

    store = DepartedStore(max_items=2, ttl_ns=10 * SEC)
    for i in range(3):
        store.add(f"g{i}", DepartedRecord("writer", None, T0 + i, None))
    assert [g for g, _ in store.records(T0 + 3)] == ["g1", "g2"]
    assert store.records(T0 + 60 * SEC) == []
    assert caches.departed.records(T0) == []


# ----- QA fixes: consistent reads, warm bound, no lost takes, phantom endpoints -----


def test_snapshot_never_sees_a_half_applied_pass() -> None:
    """A handler between record_seen and participants.put never reads a live participant as lost."""
    caches = DiscoveryCaches()
    seen_by_reader: list[Any] = []
    real_put = caches.participants.put

    def put_with_reader_in_between(guid: str, sample: Any) -> None:
        # record_seen already ran; the cache is not updated yet. A handler
        # starting now must wait for the pass to finish, not read this state.
        reader = threading.Thread(target=lambda: seen_by_reader.append(caches.snapshot(0)))
        reader.start()
        reader.join(0.3)
        assert reader.is_alive(), "the snapshot did not wait for the pass lock"
        seen_by_reader.append(reader)
        real_put(guid, sample)

    caches.participants.put = put_with_reader_in_between  # type: ignore[method-assign]
    _apply(caches, [_participant(2, name="nav")])
    thread = seen_by_reader[0]
    thread.join(2)
    snap = seen_by_reader[1]
    assert len(snap.participants) == 1
    assert [p.status for p in snap.participant_infos] == ["active"]


def test_endpoint_of_a_participant_departed_in_the_same_pass_is_not_cached_live() -> None:
    caches = DiscoveryCaches()
    _apply(caches, [_participant(1, name="nav", ts=T0)], now=T0)
    _apply(
        caches,
        parts=[_invalid(1, ts=T0 + SEC)],
        pubs=[_endpoint(7, "/late")],
        now=T0 + 2 * SEC,
    )
    assert caches.publications.values() == []
    snap = caches.snapshot(0, now_ns=T0 + 3 * SEC)
    assert [g for g, _ in snap.departed] and snap.publications == []
    (_, record) = snap.departed[0]
    assert (record.role, record.participant_name) == ("writer", "nav")


def test_revived_participant_guid_caches_endpoints_again() -> None:
    caches = DiscoveryCaches()
    _apply(caches, [_participant(1, ts=T0)], now=T0)
    _apply(caches, parts=[_invalid(1, ts=T0 + SEC)], now=T0 + SEC)
    _apply(caches, parts=[_participant(1, ts=T0 + 2 * SEC)], pubs=[_endpoint(7)], now=T0 + 3 * SEC)
    assert len(caches.publications.values()) == 1


def test_is_warm_gives_up_waiting_when_every_pass_fails() -> None:
    def boom() -> Any:
        raise RuntimeError("take failed")

    tracker = DiscoveryTracker(boom, DiscoveryCaches(), domain_id=0)
    tracker.run_pass()
    assert not tracker.is_warm()  # not started: nothing to wait on
    tracker._started_at = time.monotonic()
    assert not tracker.is_warm()
    tracker._started_at = time.monotonic() - 3.5
    assert tracker.is_warm()
    assert tracker.wait_warm(timeout_s=0.05) is True
    status = tracker.status()
    assert status["passes"] == 0
    assert status["failed_passes"] == 1
    assert status["warm"] is True


def test_wait_warm_returns_false_within_its_timeout_while_young() -> None:
    tracker = DiscoveryTracker(lambda: ([], [], []), DiscoveryCaches(), domain_id=0)
    tracker._started_at = time.monotonic()
    started = time.monotonic()
    assert tracker.wait_warm(timeout_s=0.2) is False
    assert time.monotonic() - started < 1.0


def test_buffered_take_keeps_taken_lists_when_a_later_take_raises() -> None:
    from topicforge.adapters.common import BufferedTake

    calls = {"subs": 0}
    dispose = _invalid(2, ts=T0 + SEC)

    def subs(sink: list[Any]) -> None:
        calls["subs"] += 1
        if calls["subs"] == 1:
            raise RuntimeError("third take failed")
        sink.append("sub")

    take = BufferedTake(
        lambda sink: sink.append(dispose) if not sink and calls["subs"] == 0 else None,
        lambda sink: sink.append("pub") if calls["subs"] == 0 else None,
        subs,
    )
    with contextlib.suppress(RuntimeError):
        take()
    parts, pubs, subs_ = take()
    assert parts == [dispose] and pubs == ["pub"] and subs_ == ["sub"]


def test_tracker_applies_a_dispose_taken_before_a_failing_take() -> None:
    from topicforge.adapters.common import BufferedTake

    caches = DiscoveryCaches()
    _apply(caches, [_participant(2, name="nav")])
    state = {"n": 0}

    def parts(sink: list[Any]) -> None:
        if state["n"] == 0:
            sink.append(_invalid(2, ts=T0 + SEC))

    def subs(sink: list[Any]) -> None:
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("boom")

    tracker = DiscoveryTracker(
        BufferedTake(parts, lambda sink: None, subs), caches, domain_id=0, clock_ns=lambda: T0 + SEC
    )
    tracker.run_pass()
    assert tracker.status()["failed_passes"] == 1
    tracker.run_pass()
    (info,) = caches.lifecycle.snapshot_participants()
    assert info.status == "left"


def test_sample_cache_counts_evictions_and_warns_once(caplog) -> None:
    from topicforge.adapters.common import SampleCache

    cache = SampleCache(max_items=2)
    with caplog.at_level("WARNING"):
        for i in range(5):
            cache.put(str(i), i)
    assert cache.evictions == 3
    assert len([r for r in caplog.records if "cache full" in r.getMessage()]) == 1
    caches = DiscoveryCaches()
    assert caches.evictions() == 0


def test_stop_warns_when_the_thread_does_not_join(caplog) -> None:
    release = threading.Event()

    def stuck() -> tuple[list[Any], list[Any], list[Any]]:
        release.wait(2)
        return [], [], []

    tracker = DiscoveryTracker(
        stuck,
        DiscoveryCaches(),
        domain_id=0,
        period_s=0.01,
    )
    tracker.start()
    time.sleep(0.1)
    with caplog.at_level("WARNING"):
        tracker.stop(timeout=0.05)
    release.set()
    tracker.stop(timeout=3)
    assert tracker.status()["stop_timed_out"] is True
    assert any("still running" in r.getMessage() for r in caplog.records)
