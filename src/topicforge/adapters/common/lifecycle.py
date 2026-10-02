"""Participant tracking shared by the DDS adapters.

A bounded ring of `ParticipantEvent` plus a map of known participants,
read by `participant_events`. The Fast adapter feeds it from listener
callbacks, Cyclone from the `DiscoveryTracker` thread
(`common/discovery_tracker.py`); the buffer starts no thread itself.

The event ring holds `MAX_EVENTS` entries and the participant map
`MAX_PARTICIPANTS` (tombstoned `"left"` entries are dropped first). A
restarted node mints a new RTPS GUID, so a long-running server on a churny
bus would otherwise grow without bound. An RLock guards mutation because
discovery callbacks run on the DDS library's thread; readers get copies.
No DDS import, so tests use synthetic input.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Literal

from topicforge.adapters.common.dds_helpers import VendorTag
from topicforge.models import ParticipantEvent, ParticipantInfo
from topicforge.models.schemas import TimeSource

MAX_EVENTS = 200
"""Hard cap on the event ring. Older entries drop out as new ones arrive."""

MAX_PARTICIPANTS = 4096
"""Hard cap on the number of distinct participants tracked. On overflow a
tombstoned (`status == "left"`) participant is dropped first, else the
oldest-inserted one: so a churny bus cannot grow the map without bound."""

EventType = Literal["discovered", "lost"]
EffectiveMode = Literal["mock", "live"]


class LifecycleBuffer:
    """Known DDS participants keyed by GUID, plus a bounded ring of events.

    `record_seen` inserts a participant (emitting `discovered`) or updates
    `last_seen_ns` and `seen_count`; `record_lost` flips `status` to
    `"left"` and emits `lost`. Timestamps are wall-clock `time.time_ns()`.
    """

    def __init__(
        self, *, max_events: int = MAX_EVENTS, max_participants: int = MAX_PARTICIPANTS
    ) -> None:
        self._lock = threading.RLock()
        self._participants: dict[str, ParticipantInfo] = {}
        self._events: deque[ParticipantEvent] = deque(maxlen=max_events)
        self._max_participants = max_participants

    def record_seen(
        self,
        *,
        guid: str,
        vendor: VendorTag,
        hostname: str | None,
        domain_id: int,
        mode_effective: EffectiveMode = "live",
        now_ns: int | None = None,
        name: str | None = None,
        announced_ns: int | None = None,
    ) -> None:
        """Mark a participant as observed at `now_ns` (default: time.time_ns()).

        `announced_ns` is the DDS source timestamp of the announcement, when
        known: it becomes the `discovered` event time (`time_source`
        `dds_source_timestamp`), while `first_seen_ns` / `last_seen_ns` stay
        on the local clock.

        Re-observation updates `last_seen_ns` and `seen_count`; a
        `discovered` event is emitted again only for a participant that was
        absent or `"left"`.
        """
        ts = now_ns if now_ns is not None else time.time_ns()
        with self._lock:
            existing = self._participants.get(guid)
            if existing is None:
                if len(self._participants) >= self._max_participants:
                    self._evict_participant()
                self._participants[guid] = ParticipantInfo(
                    guid=guid,
                    vendor=vendor,
                    name=name,
                    hostname=hostname,
                    domain_id=domain_id,
                    mode_effective=mode_effective,
                    first_seen_ns=ts,
                    last_seen_ns=ts,
                    status="active",
                    seen_count=1,
                    announced_ns=announced_ns,
                    vendor_source="none" if vendor in ("unknown", "mock") else "guid_prefix",
                )
                self._append_event(
                    guid=guid,
                    event_type="discovered",
                    vendor=vendor,
                    hostname=hostname,
                    name=name,
                    domain_id=domain_id,
                    mode_effective=mode_effective,
                    ts=ts,
                    announced_ns=announced_ns,
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
                    "name": name or existing.name,
                    "announced_ns": announced_ns or existing.announced_ns,
                    **({"lost_ns": None, "lost_time_source": None} if re_joined else {}),
                }
            )
            if re_joined:
                self._append_event(
                    guid=guid,
                    event_type="discovered",
                    vendor=vendor,
                    hostname=hostname or existing.hostname,
                    name=name or existing.name,
                    domain_id=domain_id,
                    mode_effective=mode_effective,
                    ts=ts,
                    announced_ns=announced_ns,
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
        lost_ns: int | None = None,
        time_source: TimeSource | None = None,
    ) -> None:
        """Mark a participant as left, emitting one event per `active -> left`.

        A GUID that is unknown or already `"left"` is a no-op. `lost_ns` and
        `time_source` carry a DDS-derived loss time; without them the loss is
        stamped `observed_local` at `now_ns`.
        """
        ts = now_ns if now_ns is not None else time.time_ns()
        with self._lock:
            existing = self._participants.get(guid)
            if existing is None or existing.status == "left":
                return
            event_ts = lost_ns if lost_ns is not None else ts
            source: TimeSource = time_source or "observed_local"
            if lost_ns is None:
                source = "observed_local"
            self._participants[guid] = existing.model_copy(
                update={"status": "left", "lost_ns": event_ts, "lost_time_source": source}
            )
            self._append_event(
                guid=guid,
                event_type="lost",
                vendor=vendor or existing.vendor,
                hostname=hostname or existing.hostname,
                name=existing.name,
                domain_id=domain_id if domain_id is not None else existing.domain_id,
                mode_effective=mode_effective,
                ts=event_ts,
                time_source=source,
                observed_ns=ts,
            )

    def reconcile(
        self,
        *,
        observed_guids: set[str],
        domain_id: int,
        mode_effective: EffectiveMode = "live",
        now_ns: int | None = None,
    ) -> None:
        """Mark every active GUID of this domain missing from `observed_guids` as lost.

        For polling adapters (Cyclone); Fast DDS reports losses through its
        listener and calls `record_lost` directly.
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

    def is_known(self, guid: str) -> bool:
        """True when `guid` has been recorded, active or left."""
        with self._lock:
            return guid in self._participants

    def snapshot_participants(self, *, domain_id: int | None = None) -> list[ParticipantInfo]:
        """Copy of the known participants, oldest first; `domain_id` filters."""
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
        """Events younger than `lookback_seconds`, newest first; `domain_id` filters."""
        ts = now_ns if now_ns is not None else time.time_ns()
        cutoff = ts - lookback_seconds * 1_000_000_000
        with self._lock:
            events = [e for e in self._events if e.timestamp_ns >= cutoff]
            if domain_id is not None:
                events = [e for e in events if e.domain_id == domain_id]
        events.reverse()
        return events

    def _evict_participant(self) -> None:
        """Drop one participant, under the lock: a `"left"` one first, else the oldest."""
        for guid, info in self._participants.items():
            if info.status == "left":
                del self._participants[guid]
                return
        oldest = next(iter(self._participants), None)
        if oldest is not None:
            del self._participants[oldest]

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
        name: str | None = None,
        announced_ns: int | None = None,
        time_source: TimeSource | None = None,
        observed_ns: int | None = None,
    ) -> None:
        # Called under self._lock. `ts` is the local observation time; a DDS
        # announcement time, when given, becomes the event time.
        if time_source is None:
            time_source = "dds_source_timestamp" if announced_ns else "observed_local"
        self._events.append(
            ParticipantEvent(
                guid=guid,
                event_type=event_type,
                vendor=vendor,
                timestamp_ns=announced_ns if announced_ns else ts,
                time_source=time_source,
                observed_ns=observed_ns if observed_ns is not None else ts,
                hostname=hostname,
                name=name,
                domain_id=domain_id,
                mode_effective=mode_effective,
            )
        )
