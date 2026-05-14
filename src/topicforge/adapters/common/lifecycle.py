"""Lifecycle buffer — shared participant tracking across DDS adapters.

A bounded, RLock-protected ring buffer of `ParticipantEvent` plus a
dictionary of currently-known participants. Both Cyclone (polling-delta)
and Fast DDS (listener-callback) adapters feed this buffer ; the
`participant_events` MCP tool reads from it.

Design rules:

* **Pure logic at module level.** No DDS dependency. Tests pin behavior
  against synthetic input — same convention as `parse_topic_list` and
  `detect_mismatches` (the *"pure parsers / analyzers"* convention).
* **Bounded.** The event ring tops out at `MAX_EVENTS` (default 200) ;
  overflow drops the oldest. Matches the
  `MAX_SAMPLE_COUNT=50` ergonomic of `sample_messages` — tools should
  never return unbounded collections.
* **Thread-safe.** Discovery callbacks fire on the underlying DDS
  library's worker thread (Fast) ; tool calls fire on the MCP request
  thread. An RLock guards every mutating method ; readers (
  `snapshot_participants`, `events_since`) return defensive copies.
* **No background thread.** The buffer is updated in-band when an
  adapter polls or a callback fires. A participant that joined and
  left between two adapter touches is invisible — this is the
  documented Cyclone caveat in `participant_events` tool description.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Literal

from topicforge.adapters.common.dds_helpers import VendorTag
from topicforge.models import ParticipantEvent, ParticipantInfo

MAX_EVENTS = 200
"""Hard cap on the event ring. Older entries drop out as new ones arrive."""

EventType = Literal["discovered", "lost"]
EffectiveMode = Literal["mock", "live"]


class LifecycleBuffer:
    """Tracks discovered DDS participants + a bounded ring of events.

    The buffer is keyed by GUID. A `record_seen` call either inserts a
    new participant (emitting a `discovered` event) or updates
    `last_seen_ns` + `seen_count` on an existing one. A `record_lost`
    call flips `status` to `"left"` and emits a `lost` event.

    The implementation uses `time.time_ns()` for timestamps so
    timestamps are wall-clock and comparable to user logs. Lookback
    filtering happens via the same clock.
    """

    def __init__(self, *, max_events: int = MAX_EVENTS) -> None:
        self._lock = threading.RLock()
        self._participants: dict[str, ParticipantInfo] = {}
        self._events: deque[ParticipantEvent] = deque(maxlen=max_events)

    # ------------------------- mutating operations --------------------------

    def record_seen(
        self,
        *,
        guid: str,
        vendor: VendorTag,
        hostname: str | None,
        domain_id: int,
        mode_effective: EffectiveMode = "live",
        now_ns: int | None = None,
    ) -> None:
        """Mark a participant as observed at `now_ns` (default: time.time_ns()).

        Idempotent on re-observation: updates `last_seen_ns`, increments
        `seen_count`, and re-emits a `discovered` event only when the
        participant was previously absent or in `"left"` state (i.e. a
        re-join, not a steady-state heartbeat).
        """
        ts = now_ns if now_ns is not None else time.time_ns()
        with self._lock:
            existing = self._participants.get(guid)
            if existing is None:
                self._participants[guid] = ParticipantInfo(
                    guid=guid,
                    vendor=vendor,
                    hostname=hostname,
                    domain_id=domain_id,
                    mode_effective=mode_effective,
                    first_seen_ns=ts,
                    last_seen_ns=ts,
                    status="active",
                    seen_count=1,
                )
                self._append_event(
                    guid=guid,
                    event_type="discovered",
                    vendor=vendor,
                    hostname=hostname,
                    domain_id=domain_id,
                    mode_effective=mode_effective,
                    ts=ts,
                )
                return
            re_joined = existing.status == "left"
            self._participants[guid] = existing.model_copy(
                update={
                    "last_seen_ns": ts,
                    "status": "active",
                    "seen_count": existing.seen_count + 1,
                    # Hostname may surface only on later discovery samples.
                    "hostname": hostname or existing.hostname,
                }
            )
            if re_joined:
                self._append_event(
                    guid=guid,
                    event_type="discovered",
                    vendor=vendor,
                    hostname=hostname or existing.hostname,
                    domain_id=domain_id,
                    mode_effective=mode_effective,
                    ts=ts,
                )

    def record_lost(
        self,
        *,
        guid: str,
        vendor: VendorTag | None = None,
        hostname: str | None = None,
        domain_id: int | None = None,
        mode_effective: EffectiveMode = "live",
        now_ns: int | None = None,
    ) -> None:
        """Mark a participant as left. Idempotent — emits one event per
        transition `active → left`. Re-calls while already `"left"` are
        no-ops. If the GUID was never seen, falls through to a no-op
        (we cannot synthesize a participant we never observed).
        """
        ts = now_ns if now_ns is not None else time.time_ns()
        with self._lock:
            existing = self._participants.get(guid)
            if existing is None or existing.status == "left":
                return
            self._participants[guid] = existing.model_copy(update={"status": "left"})
            self._append_event(
                guid=guid,
                event_type="lost",
                vendor=vendor or existing.vendor,
                hostname=hostname or existing.hostname,
                domain_id=domain_id if domain_id is not None else existing.domain_id,
                mode_effective=mode_effective,
                ts=ts,
            )

    def reconcile(
        self,
        *,
        observed_guids: set[str],
        domain_id: int,
        mode_effective: EffectiveMode = "live",
        now_ns: int | None = None,
    ) -> None:
        """Mark every previously-active GUID not in `observed_guids` as lost.

        Used by Cyclone's polling adapter: after every snapshot of the
        DCPSParticipant builtin reader, the adapter passes the set of
        currently-observed GUIDs and the buffer flips anyone missing to
        `"left"`. Fast DDS uses its listener-driven `record_lost` path
        and does not need to call this.
        """
        with self._lock:
            for guid, info in list(self._participants.items()):
                if info.status != "active":
                    continue
                if info.domain_id != domain_id:
                    continue
                if guid in observed_guids:
                    continue
                self.record_lost(
                    guid=guid,
                    vendor=info.vendor,
                    hostname=info.hostname,
                    domain_id=info.domain_id,
                    mode_effective=mode_effective,
                    now_ns=now_ns,
                )

    # ------------------------- read-only snapshots --------------------------

    def snapshot_participants(self, *, domain_id: int | None = None) -> list[ParticipantInfo]:
        """Return a defensive copy of currently-known participants.

        `domain_id=None` returns all domains ; pass a value to filter.
        Order is insertion-stable (oldest first), which keeps the
        wire output deterministic for tests.
        """
        with self._lock:
            if domain_id is None:
                return list(self._participants.values())
            return [p for p in self._participants.values() if p.domain_id == domain_id]

    def events_since(
        self,
        *,
        lookback_seconds: int,
        domain_id: int | None = None,
        now_ns: int | None = None,
    ) -> list[ParticipantEvent]:
        """Return events younger than `lookback_seconds`, newest first.

        Hard cap mirrors `MAX_EVENTS` — the underlying ring is already
        bounded so this is implicit. `domain_id=None` skips filtering.
        """
        ts = now_ns if now_ns is not None else time.time_ns()
        cutoff = ts - lookback_seconds * 1_000_000_000
        with self._lock:
            events = [e for e in self._events if e.timestamp_ns >= cutoff]
            if domain_id is not None:
                events = [e for e in events if e.domain_id == domain_id]
        # Newest first ; deque is append-right so reverse here.
        events.reverse()
        return events

    # --------------------------- private helpers ----------------------------

    def _append_event(
        self,
        *,
        guid: str,
        event_type: EventType,
        vendor: VendorTag,
        hostname: str | None,
        domain_id: int,
        mode_effective: EffectiveMode,
        ts: int,
    ) -> None:
        # Called under self._lock — do not acquire again.
        self._events.append(
            ParticipantEvent(
                guid=guid,
                event_type=event_type,
                vendor=vendor,
                timestamp_ns=ts,
                hostname=hostname,
                domain_id=domain_id,
                mode_effective=mode_effective,
            )
        )
