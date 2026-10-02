"""Continuous discovery tracking for polling adapters: binding-free.

The Cyclone binding has no safe at-discovery callback (its listeners run on
DDS receive threads, where taking the GIL hurts DDS and can deadlock), so a
dedicated daemon thread drains the three builtin discovery readers every
`DEFAULT_PERIOD_S` and folds what it took into in-memory caches. Tool
handlers only read those caches, never the readers: a lifecycle that only
moved when a tool was called reported one restart for three and dated a
crash at the next call.

Everything that decides something is `apply_builtin_samples`, a pure function
over raw builtin samples (valid or invalid, with a `sample_info`), so it is
unit-tested with fakes. `DiscoveryTracker` is only the thread around it: it
swallows and counts errors, and stops cleanly.

Timestamps, never mixed: `announced_ns` is the DDS source timestamp of a
valid sample (announcing side's clock), `lost_ns` the source timestamp of the
dispose, `observed_ns` the local time this module saw the sample.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from topicforge.adapters.common.dds_helpers import canonicalize_vendor_id, format_guid
from topicforge.adapters.common.dds_introspection import (
    cyclone_extract_guid,
    cyclone_extract_hostname,
    cyclone_extract_participant_name,
    cyclone_extract_vendor_id,
    vendor_id_from_guid,
)
from topicforge.adapters.common.endpoints import announced_ns_of
from topicforge.adapters.common.lifecycle import LifecycleBuffer

DEFAULT_PERIOD_S = 0.5
"""Seconds between two passes of the tracker thread."""

WARM_MIN_PASSES = 2
WARM_MIN_AGE_S = 2.0
WARM_MAX_WAIT_S = 3.0
"""A tracker is warm after 2 passes and 2 s of observation; callers wait at most 3 s."""

MAX_CACHED_SAMPLES = 4096
"""Bound of each sample cache, oldest entry dropped first."""

_INSTANCE_ALIVE = 16  # DDS 1.4 InstanceStateKind: ALIVE; 32 DISPOSED, 64 NO_WRITERS

log = logging.getLogger(__name__)


def _is_valid(sample: Any) -> bool:
    return getattr(getattr(sample, "sample_info", None), "valid_data", True) is not False


def _is_alive_instance(sample: Any) -> bool:
    state = getattr(getattr(sample, "sample_info", None), "instance_state", None)
    return state is None or int(state) == _INSTANCE_ALIVE


class SampleCache:
    """Thread-safe, bounded map of the latest alive discovery sample per entity guid."""

    def __init__(self, max_items: int = MAX_CACHED_SAMPLES) -> None:
        self._lock = threading.RLock()
        self._items: OrderedDict[str, Any] = OrderedDict()
        self._max = max_items

    def put(self, guid: str, sample: Any) -> None:
        with self._lock:
            self._items.pop(guid, None)
            self._items[guid] = sample
            while len(self._items) > self._max:
                self._items.popitem(last=False)

    def remove(self, guid: str) -> None:
        with self._lock:
            self._items.pop(guid, None)

    def values(self, limit: int | None = None) -> list[Any]:
        """Defensive copy of the cached samples, oldest first."""
        with self._lock:
            items = list(self._items.values())
        return items if limit is None else items[:limit]


@dataclass
class DiscoveryCaches:
    """The three sample caches plus the participant lifecycle they feed."""

    lifecycle: LifecycleBuffer = field(default_factory=LifecycleBuffer)
    participants: SampleCache = field(default_factory=SampleCache)
    publications: SampleCache = field(default_factory=SampleCache)
    subscriptions: SampleCache = field(default_factory=SampleCache)


def _by_time(samples: list[Any], now_ns: int) -> list[Any]:
    """Stable order by DDS timestamp, so events within one pass stay chronological."""
    return sorted(samples, key=lambda s: announced_ns_of(s) or now_ns)


def _apply_participant(caches: DiscoveryCaches, sample: Any, now_ns: int, domain_id: int) -> None:
    guid = format_guid(cyclone_extract_guid(sample))
    ts = announced_ns_of(sample)
    life = caches.lifecycle
    if _is_valid(sample):
        life.record_seen(
            guid=guid,
            vendor=canonicalize_vendor_id(cyclone_extract_vendor_id(sample)),
            hostname=cyclone_extract_hostname(sample),
            name=cyclone_extract_participant_name(sample),
            domain_id=domain_id,
            now_ns=now_ns,
            announced_ns=ts,
        )
        if _is_alive_instance(sample):
            caches.participants.put(guid, sample)
            return
        # Born and gone between two passes: the valid sample is still unread
        # but the instance is already disposed. Its dispose time is unknown.
        caches.participants.remove(guid)
        life.record_lost(guid=guid, domain_id=domain_id, now_ns=now_ns)
        return
    # Invalid sample: the participant was disposed (clean leave) or its lease
    # expired. Both arrive here with a timestamp that is an upper bound of the
    # death, see `ParticipantEvent.time_source`.
    caches.participants.remove(guid)
    if not life.is_known(guid):
        # Never saw it announce (it died inside one pass, and its announcement
        # sample was already gone): still report the pair, name unknown.
        life.record_seen(
            guid=guid,
            vendor=canonicalize_vendor_id(vendor_id_from_guid(cyclone_extract_guid(sample))),
            hostname=None,
            domain_id=domain_id,
            now_ns=now_ns,
        )
    life.record_lost(
        guid=guid,
        domain_id=domain_id,
        now_ns=now_ns,
        lost_ns=ts,
        time_source="dds_source_timestamp" if ts else None,
    )


def _apply_endpoint(cache: SampleCache, sample: Any) -> None:
    guid = format_guid(cyclone_extract_guid(sample))
    if _is_valid(sample) and _is_alive_instance(sample):
        cache.put(guid, sample)
    else:
        cache.remove(guid)


def apply_builtin_samples(
    caches: DiscoveryCaches,
    participant_samples: list[Any],
    publication_samples: list[Any],
    subscription_samples: list[Any],
    now_ns: int,
    *,
    domain_id: int,
) -> None:
    """Fold one pass of taken builtin samples into the caches and the lifecycle.

    Valid samples announce or refresh an entity. Invalid samples (the builtin
    reader returns them with only a key and a `sample_info` once an instance
    is disposed or has no writers) remove it and, for participants, record the
    loss at the dispose's source timestamp. Cycles faster than the builtin
    reader's history depth can still be missed: a known limit.
    """
    for sample in _by_time(participant_samples, now_ns):
        _apply_participant(caches, sample, now_ns, domain_id)
    for sample in publication_samples:
        _apply_endpoint(caches.publications, sample)
    for sample in subscription_samples:
        _apply_endpoint(caches.subscriptions, sample)


TakeAll = Callable[[], tuple[list[Any], list[Any], list[Any]]]


class DiscoveryTracker:
    """Daemon thread running `apply_builtin_samples` every `period_s`.

    `take_all` returns the three raw sample lists taken from the builtin
    readers; the tracker thread is the only caller, which is the invariant
    that keeps `take()` from hiding samples from anyone else. A failing pass
    is logged, counted and retried at the next tick: the thread never dies
    silently.
    """

    def __init__(
        self,
        take_all: TakeAll,
        caches: DiscoveryCaches,
        *,
        domain_id: int,
        period_s: float = DEFAULT_PERIOD_S,
        clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        self._take_all = take_all
        self._caches = caches
        self._domain_id = domain_id
        self._period_s = period_s
        self._clock_ns = clock_ns
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._passes = 0
        self._errors = 0
        self._last_pass_ns: int | None = None
        self._last_error: str | None = None
        self._started_at: float | None = None

    def start(self) -> None:
        """Start the daemon thread (no-op when already running)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._started_at = time.monotonic()
        self._thread = threading.Thread(target=self._run, name="topicforge-discovery", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Ask the thread to stop and wait for it."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def run_pass(self) -> None:
        """One pass, never raises: take, apply, update the counters."""
        try:
            parts, pubs, subs = self._take_all()
            now_ns = self._clock_ns()
            apply_builtin_samples(
                self._caches, parts, pubs, subs, now_ns, domain_id=self._domain_id
            )
        except Exception as exc:
            with self._lock:
                self._errors += 1
                self._last_error = f"{type(exc).__name__}: {exc}"
                first = self._errors == 1
            log.warning("discovery tracker pass failed", exc_info=first)
            return
        with self._lock:
            self._passes += 1
            self._last_pass_ns = now_ns

    def is_warm(self, min_passes: int = WARM_MIN_PASSES, min_age_s: float = WARM_MIN_AGE_S) -> bool:
        """True once enough passes completed and the observer is old enough to have heard the bus."""
        with self._lock:
            passes = self._passes
        started = self._started_at
        return (
            started is not None and passes >= min_passes and time.monotonic() - started >= min_age_s
        )

    def wait_warm(
        self,
        timeout_s: float = WARM_MAX_WAIT_S,
        *,
        min_passes: int = WARM_MIN_PASSES,
        min_age_s: float = WARM_MIN_AGE_S,
    ) -> bool:
        """Block until `is_warm`, at most `timeout_s`; returns whether it is warm.

        Only matters in the first seconds of a server's life: discovery needs a
        moment to hear the bus, and a tool answering before that sees it
        incomplete. Returns immediately once warm, or if the tracker never started.
        """
        deadline = time.monotonic() + timeout_s
        while not self.is_warm(min_passes, min_age_s):
            if self._started_at is None or time.monotonic() >= deadline:
                return False
            time.sleep(0.05)
        return True

    def status(self) -> dict[str, Any]:
        """Counters for `health_check`: running, passes, errors, last pass time."""
        with self._lock:
            return {
                "running": self._thread is not None and self._thread.is_alive(),
                "passes": self._passes,
                "errors": self._errors,
                "last_pass_ns": self._last_pass_ns,
                "last_error": self._last_error,
            }

    def _run(self) -> None:
        while True:
            self.run_pass()
            if self._stop.wait(self._period_s):
                return
