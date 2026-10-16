"""Ghost `ros2` CLI participants: flagged in the composite, explained in the listing note."""

from __future__ import annotations

import time
from typing import Any

import pytest

from topicforge.adapters.common import spawned_pids
from topicforge.adapters.composite import CompositeAdapter
from topicforge.models import ParticipantInfo
from topicforge.services.inspector import Inspector


def _p(guid: str, **kw: Any) -> ParticipantInfo:
    kw.setdefault("vendor", "cyclone")
    return ParticipantInfo(guid=guid, domain_id=0, **kw)


class _Half:
    """Stands in for either half; the DDS one serves a fixed participant list."""

    def __init__(self, name: str, participants: list[ParticipantInfo] | None = None) -> None:
        self.name = name
        self.effective_mode = "live"
        self._participants = participants or []
        self.pids: dict[str, int] = {}

    def is_available(self) -> bool:
        return True

    def list_participants(self, domain_id: int = 0) -> list[ParticipantInfo]:
        return list(self._participants)

    def participant_pids(self) -> dict[str, int]:
        return self.pids


@pytest.fixture(autouse=True)
def _clean_ring():
    spawned_pids.clear_spawns()
    yield
    spawned_pids.clear_spawns()


def _composite(participants: list[ParticipantInfo]) -> tuple[CompositeAdapter, _Half]:
    dds = _Half("cyclone", participants)
    return CompositeAdapter(_Half("ros2_cli"), dds), dds  # type: ignore[arg-type]


def test_a_cli_ghost_is_flagged_not_excluded() -> None:
    ghost = _p("a.b.c.d", node_names=["/_ros2cli_321"], node_names_source="ros_discovery_info")
    robot = _p("e.f.g.h", node_names=["/talker"], node_names_source="ros_discovery_info")
    composite, _ = _composite([ghost, robot])
    spawned_pids.record_spawn(321)
    out = composite.list_participants()
    assert [(p.guid, p.is_topicforge_cli) for p in out] == [("a.b.c.d", True), ("e.f.g.h", False)]


def test_a_cyclone_pid_property_flags_a_ghost() -> None:
    p = _p("a.b.c.d")
    composite, dds = _composite([p])
    dds.pids = {"a.b.c.d": 555}
    spawned_pids.record_spawn(555)
    assert composite.list_participants()[0].is_topicforge_cli


def test_nothing_is_flagged_without_a_spawn() -> None:
    composite, _ = _composite([_p("a.b.c.d", node_names=["/_ros2cli_321"])])
    assert not composite.list_participants()[0].is_topicforge_cli


def test_listing_note_names_the_ghosts() -> None:
    ghost = _p("a.b.c.d", node_names=["/_ros2cli_321"])
    composite, _ = _composite([ghost])
    spawned_pids.record_spawn(321)
    listing = Inspector(composite).list_participants(0)  # type: ignore[arg-type]
    assert listing.participants[0].is_topicforge_cli
    assert listing.note and "is_topicforge_cli" in listing.note
    assert listing.returned == 1


def test_unidentified_late_arrivals_only_reach_the_note() -> None:
    now = time.time_ns()
    late = _p("a.b.c.d", status="active", first_seen_ns=now + 1_000_000_000)
    composite, _ = _composite([late])
    spawned_pids.record_spawn(1, now_ns=now)
    listing = Inspector(composite).list_participants(0)  # type: ignore[arg-type]
    assert not listing.participants[0].is_topicforge_cli
    assert listing.note and "may be its ghosts" in listing.note


def test_no_ghost_means_no_note() -> None:
    composite, _ = _composite([_p("a.b.c.d")])
    assert Inspector(composite).list_participants(0).note is None  # type: ignore[arg-type]
