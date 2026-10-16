"""`restarts_by_node` and the node names a participant keeps after it left."""

from __future__ import annotations

from topicforge.adapters.common.ros_discovery import NodeNameCache, with_node_names
from topicforge.models import ParticipantInfo
from topicforge.services.dds_views import restarts_from_nodes


def _p(guid: str, names: list[str], status: str = "active", **kw: object) -> ParticipantInfo:
    return ParticipantInfo(
        guid=guid,
        vendor="fast",
        domain_id=0,
        status=status,  # type: ignore[arg-type]
        node_names=names,
        node_names_source="ros_discovery_info" if names else "none",
        **kw,  # type: ignore[arg-type]
    )


def test_a_restarted_node_shows_two_lives_and_one_departure() -> None:
    old = _p("a.b.c.d", ["/nav/planner", "/nav/costmap"], status="left")
    new = _p("e.f.g.h", ["/nav/planner", "/nav/costmap"])
    other = _p("i.j.k.l", ["/lidar"])
    table = restarts_from_nodes([old, new, other])
    assert set(table) == {"/nav/planner", "/nav/costmap"}
    assert table["/nav/planner"].lives == 2 and table["/nav/planner"].departures == 1


def test_a_node_that_ran_once_and_stayed_is_absent() -> None:
    assert restarts_from_nodes([_p("a.b.c.d", ["/lidar"])]) == {}


def test_the_topicforge_cli_ghost_is_not_a_restarting_node() -> None:
    ghost = _p("a.b.c.d", ["/_ros2cli_42"], status="left", is_topicforge_cli=True)
    assert restarts_from_nodes([ghost]) == {}


def test_cache_remembers_the_last_non_empty_list_across_an_empty_goodbye() -> None:
    cache = NodeNameCache()
    cache.put("k", ["/a"])
    cache.put("k", [])
    assert cache.as_dict() == {"k": ()}
    assert cache.last_dict() == {"k": ("/a",)}


def test_a_left_participant_falls_back_to_its_last_names_but_an_active_one_does_not() -> None:
    gone = _p("00000001.00000002.00000003.000001c1", [], status="left")
    live = _p("00000004.00000005.00000006.000001c1", [])
    names = {"00000001.00000002.00000003": (), "00000004.00000005.00000006": ()}
    last = {"00000001.00000002.00000003": ("/a",), "00000004.00000005.00000006": ("/b",)}
    out = with_node_names([gone, live], names, last)
    assert out[0].node_names == ["/a"] and out[0].node_names_source == "ros_discovery_info"
    assert out[1].node_names == []


def test_a_remote_participant_with_a_cli_pid_name_is_not_flagged_but_counted() -> None:
    from topicforge.adapters.common.ros_discovery import flag_topicforge_cli

    old = _p("a.b.c.d", ["/lidar", "/_ros2cli_77"], status="left", hostname="other")
    new = _p("e.f.g.h", ["/lidar"], hostname="other")
    out = flag_topicforge_cli([old, new], [(77, 10)], now_ns=10, local_host="box")
    assert not any(p.is_topicforge_cli for p in out)
    assert restarts_from_nodes(out)["/lidar"].lives == 2
