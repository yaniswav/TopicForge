"""Unit tests for `topicforge.adapters.common.lifecycle.LifecycleBuffer`.

Pure logic — no DDS dependency. Same convention as
`tests/test_qos_analyzer.py` (the analyzer it joins in `adapters/common/`).
"""

from __future__ import annotations

import threading

from topicforge.adapters.common.lifecycle import MAX_EVENTS, LifecycleBuffer


def test_record_seen_inserts_new_participant() -> None:
    buf = LifecycleBuffer()
    buf.record_seen(
        guid="g1",
        vendor="cyclone",
        hostname="host-1",
        domain_id=0,
        now_ns=1_000_000_000,
    )

    snapshot = buf.snapshot_participants()
    assert len(snapshot) == 1
    p = snapshot[0]
    assert p.guid == "g1"
    assert p.status == "active"
    assert p.first_seen_ns == 1_000_000_000
    assert p.last_seen_ns == 1_000_000_000
    assert p.seen_count == 1


def test_repeated_record_seen_updates_last_seen_and_count() -> None:
    buf = LifecycleBuffer()
    buf.record_seen(guid="g1", vendor="cyclone", hostname="h", domain_id=0, now_ns=100)
    buf.record_seen(guid="g1", vendor="cyclone", hostname="h", domain_id=0, now_ns=200)
    buf.record_seen(guid="g1", vendor="cyclone", hostname="h", domain_id=0, now_ns=300)

    p = buf.snapshot_participants()[0]
    assert p.first_seen_ns == 100
    assert p.last_seen_ns == 300
    assert p.seen_count == 3


def test_repeated_record_seen_emits_only_one_discovered_event() -> None:
    """Steady-state observations should not flood the event log."""
    buf = LifecycleBuffer()
    for ts in (100, 200, 300, 400):
        buf.record_seen(guid="g1", vendor="cyclone", hostname=None, domain_id=0, now_ns=ts)

    events = buf.events_since(lookback_seconds=10, now_ns=500)
    assert len(events) == 1
    assert events[0].event_type == "discovered"


def test_record_lost_flips_status_and_emits_event() -> None:
    buf = LifecycleBuffer()
    buf.record_seen(guid="g1", vendor="cyclone", hostname=None, domain_id=0, now_ns=100)
    buf.record_lost(guid="g1", now_ns=500)

    p = buf.snapshot_participants()[0]
    assert p.status == "left"

    events = buf.events_since(lookback_seconds=10, now_ns=600)
    # Newest first: lost, then discovered.
    assert [e.event_type for e in events] == ["lost", "discovered"]


def test_record_lost_is_idempotent() -> None:
    buf = LifecycleBuffer()
    buf.record_seen(guid="g1", vendor="cyclone", hostname=None, domain_id=0, now_ns=100)
    buf.record_lost(guid="g1", now_ns=500)
    buf.record_lost(guid="g1", now_ns=600)

    events = buf.events_since(lookback_seconds=10, now_ns=700)
    lost_events = [e for e in events if e.event_type == "lost"]
    assert len(lost_events) == 1


def test_record_lost_on_unknown_guid_is_noop() -> None:
    """We cannot synthesize a participant we never observed."""
    buf = LifecycleBuffer()
    buf.record_lost(guid="never-seen", now_ns=100)

    assert buf.snapshot_participants() == []
    assert buf.events_since(lookback_seconds=10, now_ns=200) == []


def test_re_join_emits_new_discovered_event() -> None:
    buf = LifecycleBuffer()
    buf.record_seen(guid="g1", vendor="cyclone", hostname=None, domain_id=0, now_ns=100)
    buf.record_lost(guid="g1", now_ns=200)
    buf.record_seen(guid="g1", vendor="cyclone", hostname=None, domain_id=0, now_ns=300)

    events = buf.events_since(lookback_seconds=10, now_ns=400)
    assert [e.event_type for e in events] == ["discovered", "lost", "discovered"]
    p = buf.snapshot_participants()[0]
    assert p.status == "active"


def test_reconcile_marks_missing_active_as_lost() -> None:
    buf = LifecycleBuffer()
    buf.record_seen(guid="g1", vendor="cyclone", hostname=None, domain_id=0, now_ns=100)
    buf.record_seen(guid="g2", vendor="cyclone", hostname=None, domain_id=0, now_ns=100)

    # Next polling round: only g1 still visible.
    buf.reconcile(observed_guids={"g1"}, domain_id=0, now_ns=200)

    by_guid = {p.guid: p for p in buf.snapshot_participants()}
    assert by_guid["g1"].status == "active"
    assert by_guid["g2"].status == "left"


def test_reconcile_ignores_other_domains() -> None:
    buf = LifecycleBuffer()
    buf.record_seen(guid="g1", vendor="cyclone", hostname=None, domain_id=0, now_ns=100)
    buf.record_seen(guid="g2", vendor="cyclone", hostname=None, domain_id=42, now_ns=100)

    buf.reconcile(observed_guids={"g1"}, domain_id=0, now_ns=200)
    by_guid = {p.guid: p for p in buf.snapshot_participants()}
    assert by_guid["g1"].status == "active"
    assert by_guid["g2"].status == "active"  # different domain, untouched


def test_snapshot_filters_by_domain() -> None:
    buf = LifecycleBuffer()
    buf.record_seen(guid="g1", vendor="cyclone", hostname=None, domain_id=0, now_ns=100)
    buf.record_seen(guid="g2", vendor="cyclone", hostname=None, domain_id=42, now_ns=100)

    assert [p.guid for p in buf.snapshot_participants(domain_id=0)] == ["g1"]
    assert [p.guid for p in buf.snapshot_participants(domain_id=42)] == ["g2"]


def test_events_since_drops_old_entries() -> None:
    buf = LifecycleBuffer()
    buf.record_seen(guid="g1", vendor="cyclone", hostname=None, domain_id=0, now_ns=0)
    buf.record_seen(guid="g2", vendor="cyclone", hostname=None, domain_id=0, now_ns=10_000_000_000)

    # 5s lookback at t=11s drops the t=0 event.
    events = buf.events_since(lookback_seconds=5, now_ns=11_000_000_000)
    assert [e.guid for e in events] == ["g2"]


def test_events_since_orders_newest_first() -> None:
    buf = LifecycleBuffer()
    buf.record_seen(guid="g1", vendor="cyclone", hostname=None, domain_id=0, now_ns=100)
    buf.record_seen(guid="g2", vendor="cyclone", hostname=None, domain_id=0, now_ns=200)
    buf.record_seen(guid="g3", vendor="cyclone", hostname=None, domain_id=0, now_ns=300)

    events = buf.events_since(lookback_seconds=10, now_ns=400)
    assert [e.guid for e in events] == ["g3", "g2", "g1"]


def test_events_since_filters_by_domain() -> None:
    buf = LifecycleBuffer()
    buf.record_seen(guid="g1", vendor="cyclone", hostname=None, domain_id=0, now_ns=100)
    buf.record_seen(guid="g2", vendor="cyclone", hostname=None, domain_id=42, now_ns=200)

    assert [e.guid for e in buf.events_since(lookback_seconds=10, domain_id=0, now_ns=300)] == [
        "g1"
    ]
    assert [e.guid for e in buf.events_since(lookback_seconds=10, domain_id=42, now_ns=300)] == [
        "g2"
    ]


def test_ring_buffer_drops_oldest_at_overflow() -> None:
    buf = LifecycleBuffer(max_events=5)
    for i in range(10):
        buf.record_seen(guid=f"g{i}", vendor="cyclone", hostname=None, domain_id=0, now_ns=i * 100)

    events = buf.events_since(lookback_seconds=100, now_ns=10_000)
    assert len(events) == 5
    # Newest first — the 5 most recent: g9, g8, g7, g6, g5.
    assert [e.guid for e in events] == ["g9", "g8", "g7", "g6", "g5"]


def test_hostname_late_arrival_is_preserved() -> None:
    """A first sample with no hostname, then a later sample with one, must
    surface the hostname rather than overwrite it with None on the second
    record_seen call.
    """
    buf = LifecycleBuffer()
    buf.record_seen(guid="g1", vendor="cyclone", hostname=None, domain_id=0, now_ns=100)
    buf.record_seen(guid="g1", vendor="cyclone", hostname="late-host", domain_id=0, now_ns=200)

    p = buf.snapshot_participants()[0]
    assert p.hostname == "late-host"


def test_thread_safety_smoke() -> None:
    """A burst of concurrent inserts from two threads must not corrupt state."""
    buf = LifecycleBuffer(max_events=200)

    def feed(prefix: str) -> None:
        for i in range(50):
            buf.record_seen(
                guid=f"{prefix}-{i}",
                vendor="cyclone",
                hostname=None,
                domain_id=0,
                now_ns=i * 100,
            )

    threads = [threading.Thread(target=feed, args=(name,)) for name in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    snapshot = buf.snapshot_participants()
    assert len(snapshot) == 100  # 50 from each thread, all distinct GUIDs


def test_default_max_events_is_200() -> None:
    """Pin the constant — it is documented in the tool description."""
    assert MAX_EVENTS == 200
