"""Tests for continuous discovery tracking: the pure pass and the thread wrapper.

Synthetic duck-typed builtin samples only (valid and invalid, with an integer
`instance_state` like the binding): no DDS binding required.
"""

from __future__ import annotations

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

    tracker = DiscoveryTracker(take, DiscoveryCaches(), domain_id=0, period_s=0.01)
    tracker.start()
    tracker.start()  # idempotent: still one thread
    assert ticked.wait(2)
    assert sum(t.name == "topicforge-discovery" for t in threading.enumerate()) == 1
    assert tracker.status()["running"] is True

    tracker.stop()
    deadline = time.monotonic() + 2
    while tracker.status()["running"] and time.monotonic() < deadline:
        time.sleep(0.01)
    assert tracker.status()["running"] is False
    assert not any(t.name == "topicforge-discovery" for t in threading.enumerate())


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
