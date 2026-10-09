"""QoS parsing, `ros2 topic list -v` and `sample_messages` echo flags in the live adapter.

The `ros2 topic info --verbose` fixtures are captures from a ROS 2 Humble /
Fast DDS system (OmniSim). They print `History (Depth): UNKNOWN`, which is what
Fast DDS reports. The `topic list -v` text follows `ros2topic/verb/list.py`.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from topicforge.adapters.base import AdapterError
from topicforge.adapters.ros2_live.adapter import (
    Ros2CliAdapter,
    parse_topic_info,
)
from topicforge.adapters.ros2_live.echo_stream import EchoRun
from topicforge.adapters.ros2_live.parsers import (
    EndpointQos,
    parse_topic_endpoint_qos,
    parse_topic_list_verbose,
    qualified_node_name,
    side_nodes,
    summarize_side_qos,
)
from topicforge.models import SideQos, TopicListItem

_FIXTURES = Path(__file__).parent / "fixtures"
_MODULE = "topicforge.adapters.ros2_live.adapter"
_BAG_DIR = _FIXTURES / "bags" / "omnisim_humble"


def _fixture(name: str) -> str:
    return (_FIXTURES / f"ros2_topic_info_verbose_{name}.txt").read_text(encoding="utf-8")


# ---- topic info --verbose --------------------------------------------------


def test_endpoint_qos_of_a_single_publisher() -> None:
    assert parse_topic_endpoint_qos(_fixture("scan")) == [
        EndpointQos("PUBLISHER", "reliable", "volatile", "/omnisim_sensors")
    ]


def test_endpoint_qos_of_a_subscription_only_topic() -> None:
    endpoints = parse_topic_endpoint_qos(_fixture("cmd_vel"))
    assert [e.endpoint_type for e in endpoints] == ["SUBSCRIPTION"]
    assert summarize_side_qos(endpoints, "PUBLISHER", 0)[0] is None
    assert summarize_side_qos(endpoints, "SUBSCRIPTION", 1) == (
        SideQos(reliability="reliable", durability="volatile", endpoint_count=1),
        None,
    )


def test_endpoint_qos_reads_every_endpoint_of_a_busy_topic() -> None:
    endpoints = parse_topic_endpoint_qos(_fixture("parameter_events"))
    assert len(endpoints) > 4
    assert {e.endpoint_type for e in endpoints} == {"PUBLISHER"}


def test_latched_topic_reports_transient_local() -> None:
    qos, note = summarize_side_qos(
        parse_topic_endpoint_qos(_fixture("tf_static")), "PUBLISHER", 2
    )
    assert note is None
    assert qos is not None and (qos.reliability, qos.durability) == ("reliable", "transient_local")


def test_publishers_that_agree_give_one_value() -> None:
    endpoints = parse_topic_endpoint_qos(_fixture("tf"))
    assert len(endpoints) == 2
    qos, _ = summarize_side_qos(endpoints, "PUBLISHER", 2)
    assert qos == SideQos(reliability="reliable", durability="volatile", endpoint_count=2)


def test_publishers_that_disagree_give_mixed() -> None:
    endpoints = [
        EndpointQos("PUBLISHER", "reliable", "volatile"),
        EndpointQos("PUBLISHER", "best_effort", "transient_local"),
        EndpointQos("SUBSCRIPTION", "best_effort", "volatile"),
    ]
    assert summarize_side_qos(endpoints, "PUBLISHER", 2)[0] == SideQos(
        reliability="mixed", durability="mixed", endpoint_count=2
    )
    assert summarize_side_qos(endpoints, "SUBSCRIPTION", 1)[0] == SideQos(
        reliability="best_effort", durability="volatile", endpoint_count=1
    )


def test_unknown_policy_values_are_ignored_when_another_endpoint_reports() -> None:
    text = (
        "Type: a/msg/A\n\nPublisher count: 2\n\n"
        "Node name: n1\nEndpoint type: PUBLISHER\nQoS profile:\n"
        "  Reliability: BEST_EFFORT\n  History (Depth): KEEP_LAST (10)\n  Durability: UNKNOWN\n\n"
        "Node name: n2\nEndpoint type: PUBLISHER\nQoS profile:\n"
        "  Reliability: SYSTEM_DEFAULT\n  Durability: VOLATILE\n"
    )
    qos, _ = summarize_side_qos(parse_topic_endpoint_qos(text), "PUBLISHER", 2)
    assert qos == SideQos(reliability="best_effort", durability="volatile", endpoint_count=2)


def test_a_policy_nobody_reports_leaves_the_side_without_qos() -> None:
    text = (
        "Type: a/msg/A\n\nPublisher count: 1\n\n"
        "Node name: n1\nNode namespace: /\nEndpoint type: PUBLISHER\nQoS profile:\n"
        "  Reliability: BEST_EFFORT\n  Durability: UNKNOWN\n"
    )
    qos, note = summarize_side_qos(parse_topic_endpoint_qos(text), "PUBLISHER", 1)
    assert qos is None
    assert note is not None and "UNKNOWN" in note


def test_no_endpoint_blocks_means_no_qos() -> None:
    assert parse_topic_endpoint_qos("Type: a/msg/A\nPublisher count: 0\n") == []
    qos, note = summarize_side_qos([], "PUBLISHER", 0)
    assert qos is None and note == "The topic has no publisher, so there is no QoS to report."
    qos, note = summarize_side_qos([], "SUBSCRIPTION", 2)
    assert qos is None and note == "The `ros2` CLI did not list the QoS of the subscriptions."


def test_parse_topic_info_fills_qos_fields() -> None:
    info = parse_topic_info(
        _fixture("tf_static"), fallback_name="/tf_static", mode_effective="live"
    )
    assert info is not None
    assert info.message_type == "tf2_msgs/msg/TFMessage"
    assert info.publisher_count == 2
    assert info.publisher_qos == SideQos(
        reliability="reliable", durability="transient_local", endpoint_count=2
    )
    assert info.publisher_qos_note is None
    assert info.subscription_qos is None
    assert info.subscription_qos_note == "The topic has no subscriber, so there is no QoS to report."


def test_parse_topic_info_without_verbose_block_leaves_qos_empty() -> None:
    text = "Type: a/msg/A\nPublisher count: 1\nSubscription count: 0\n"
    info = parse_topic_info(text, fallback_name="/a", mode_effective="live")
    assert info is not None
    assert info.publisher_qos is None and info.subscription_qos is None
    assert info.publisher_qos_note == "The `ros2` CLI did not list the QoS of the publishers."
    assert info.publisher_nodes == [] and info.subscriber_nodes == []


# ---- nodes on each side ------------------------------------------------------


def test_qualified_node_name_joins_namespace_and_name() -> None:
    assert qualified_node_name("/", "lidar") == "/lidar"
    assert qualified_node_name("/robot1", "lidar") == "/robot1/lidar"
    assert qualified_node_name("/robot1/", "lidar") == "/robot1/lidar"
    assert qualified_node_name("/", "_NODE_NAME_UNKNOWN_") is None
    assert qualified_node_name("/", "") is None


def test_clock_has_one_publisher_and_five_subscribers() -> None:
    # Shape of OmniSim's /clock: 1 publisher, 5 subscribers (node names from the captures).
    info = parse_topic_info(_fixture("clock"), fallback_name="/clock", mode_effective="live")
    assert info is not None
    assert (info.publisher_count, info.subscriber_count) == (1, 5)
    assert info.publisher_nodes == ["/omnisim_clock"]
    assert info.subscriber_nodes == [
        "/omnisim_sensors",
        "/omnisim_command",
        "/omnisim_odometry",
        "/omnisim_tf",
        "/husky/omnisim_bridge",
    ]
    assert info.publisher_qos == SideQos(
        reliability="reliable", durability="volatile", endpoint_count=1
    )
    assert info.subscription_qos == SideQos(
        reliability="best_effort", durability="volatile", endpoint_count=5
    )


def test_cmd_vel_has_no_publisher_and_one_subscriber() -> None:
    # Subscriber-only topic: the subscription side is reported, the publisher side says why not.
    info = parse_topic_info(_fixture("cmd_vel"), fallback_name="/cmd_vel", mode_effective="live")
    assert info is not None
    assert (info.publisher_count, info.subscriber_count) == (0, 1)
    assert info.publisher_nodes == []
    assert info.subscriber_nodes == ["/omnisim_command"]
    assert info.publisher_qos is None
    assert info.publisher_qos_note == "The topic has no publisher, so there is no QoS to report."
    assert info.subscription_qos == SideQos(
        reliability="reliable", durability="volatile", endpoint_count=1
    )
    assert info.subscription_qos_note is None


def test_side_nodes_are_distinct_and_in_cli_order() -> None:
    endpoints = [
        EndpointQos("PUBLISHER", "reliable", "volatile", "/b"),
        EndpointQos("PUBLISHER", "reliable", "volatile", "/a"),
        EndpointQos("PUBLISHER", "reliable", "volatile", "/b"),
        EndpointQos("PUBLISHER", "reliable", "volatile", None),
        EndpointQos("SUBSCRIPTION", "reliable", "volatile", "/c"),
    ]
    assert side_nodes(endpoints, "PUBLISHER") == ["/b", "/a"]
    assert side_nodes(endpoints, "SUBSCRIPTION") == ["/c"]


# ---- topic list -v ---------------------------------------------------------

_LIST_V = (
    "Published topics:\n"
    " * /clock [rosgraph_msgs/msg/Clock] 1 publisher\n"
    " * /tf [tf2_msgs/msg/TFMessage] 2 publishers\n"
    " * /scan [sensor_msgs/msg/LaserScan] 1 publisher\n"
    "\n"
    "Subscribed topics:\n"
    " * /cmd_vel [geometry_msgs/msg/Twist] 1 subscriber\n"
    " * /tf [tf2_msgs/msg/TFMessage] 3 subscribers\n"
    "\n"
)


def test_parse_topic_list_verbose_merges_both_sections() -> None:
    assert parse_topic_list_verbose(_LIST_V) == {
        "/clock": (1, 0),
        "/tf": (2, 3),
        "/scan": (1, 0),
        "/cmd_vel": (0, 1),
    }


def test_parse_topic_list_verbose_handles_several_types() -> None:
    text = "Published topics:\n * /x [a/msg/A, b/msg/B] 1 publisher\n\nSubscribed topics:\n\n"
    assert parse_topic_list_verbose(text) == {"/x": (1, 0)}


def test_parse_topic_list_verbose_empty_graph_is_not_a_failure() -> None:
    assert parse_topic_list_verbose("Published topics:\n\nSubscribed topics:\n\n") == {}


@pytest.mark.parametrize("text", ["", "/a [t/msg/T]\n", "garbage\n"])
def test_parse_topic_list_verbose_unrecognized_format_is_none(text: str) -> None:
    assert parse_topic_list_verbose(text) is None


# ---- adapter wiring --------------------------------------------------------


class _Cli:
    """Stubs `run_process`: answers by CLI subcommand and records the commands."""

    def __init__(self, answers: dict[str, str | int]) -> None:
        self.answers = answers
        self.commands: list[list[str]] = []
        self.kwargs: list[dict[str, object]] = []

    def __call__(self, cmd: list[str], **kwargs: object) -> object:
        self.commands.append(cmd)
        self.kwargs.append(kwargs)
        key = " ".join(cmd[1:3]) + (" -v" if "-v" in cmd else "")
        answer = self.answers.get(key, "")
        if isinstance(answer, int):
            return SimpleNamespace(timed_out=False, returncode=answer, stdout="", stderr="boom")
        return SimpleNamespace(timed_out=False, returncode=0, stdout=answer, stderr="")


def _install(monkeypatch: pytest.MonkeyPatch, cli: _Cli) -> None:
    monkeypatch.setattr(f"{_MODULE}.shutil.which", lambda name: f"/fake/bin/{name}")
    monkeypatch.setattr(f"{_MODULE}.run_process", cli)


_LIST_T = (
    "/clock [rosgraph_msgs/msg/Clock]\n"
    "/tf [tf2_msgs/msg/TFMessage]\n"
    "/cmd_vel [geometry_msgs/msg/Twist]\n"
    "/lonely [std_msgs/msg/String]\n"
)


def test_list_topics_reads_counts_from_the_verbose_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = _Cli({"topic list": _LIST_T, "topic list -v": _LIST_V})
    _install(monkeypatch, cli)
    topics = {t.name: t for t in Ros2CliAdapter().list_topics()}
    assert (topics["/tf"].publisher_count, topics["/tf"].subscriber_count) == (2, 3)
    assert (topics["/cmd_vel"].publisher_count, topics["/cmd_vel"].subscriber_count) == (0, 1)
    assert all(set(t.model_dump()) == set(TopicListItem.model_fields) for t in topics.values())
    # Only the topic absent from both sections costs a per-topic call.
    assert sum(c[1:3] == ["topic", "info"] for c in cli.commands) == 1


def test_list_topics_asks_about_a_topic_missing_from_the_verbose_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    info = "Type: std_msgs/msg/String\nPublisher count: 1\nSubscription count: 0\n"
    cli = _Cli({"topic list": _LIST_T, "topic list -v": _LIST_V, "topic info": info})
    _install(monkeypatch, cli)
    topics = {t.name: t for t in Ros2CliAdapter().list_topics()}
    assert (topics["/lonely"].publisher_count, topics["/lonely"].subscriber_count) == (1, 0)
    assert (topics["/tf"].publisher_count, topics["/tf"].subscriber_count) == (2, 3)


def test_list_topics_falls_back_to_per_topic_info_when_verbose_list_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    info = "Type: t/msg/T\nPublisher count: 4\nSubscription count: 1\n"
    cli = _Cli({"topic list": _LIST_T, "topic list -v": 1, "topic info": info})
    _install(monkeypatch, cli)
    topics = Ros2CliAdapter().list_topics()
    assert len(topics) == 4
    assert all((t.publisher_count, t.subscriber_count) == (4, 1) for t in topics)
    assert sum("info" in c for c in cli.commands) == 4


def test_list_topics_falls_back_when_verbose_output_is_unrecognized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    info = "Type: t/msg/T\nPublisher count: 2\nSubscription count: 0\n"
    cli = _Cli({"topic list": _LIST_T, "topic list -v": "something else\n", "topic info": info})
    _install(monkeypatch, cli)
    assert all(t.publisher_count == 2 for t in Ros2CliAdapter().list_topics())


def test_get_topic_info_reports_qos(monkeypatch: pytest.MonkeyPatch) -> None:
    cli = _Cli({"topic info": _fixture("tf_static")})
    _install(monkeypatch, cli)
    info = Ros2CliAdapter().get_topic_info("/tf_static")
    assert info.publisher_qos == SideQos(
        reliability="reliable", durability="transient_local", endpoint_count=2
    )


# ---- sample_messages flags -------------------------------------------------


def _echo_command(
    monkeypatch: pytest.MonkeyPatch, info: str | None = None, **options: object
) -> list[str]:
    cli = _Cli(
        {
            "topic info": info
            or "Type: sensor_msgs/msg/LaserScan\nPublisher count: 1\nSubscription count: 0\n"
        }
    )
    _install(monkeypatch, cli)
    seen: list[list[str]] = []

    def fake_stream(cmd: list[str], **_kw: object) -> EchoRun:
        seen.append(cmd)
        return EchoRun()

    monkeypatch.setattr(f"{_MODULE}.stream_echo", fake_stream)
    Ros2CliAdapter().sample_messages("/scan", 1, **options)  # type: ignore[arg-type]
    return seen[0]


def _info(reliability: str, durability: str, count: int = 1) -> str:
    block = (
        "Type: sensor_msgs/msg/LaserScan\nPublisher count: {n}\nSubscription count: 0\n\n"
        "Node name: talker\nEndpoint type: PUBLISHER\n"
        "Reliability: {r}\nDurability: {d}\n"
    )
    return block.format(n=count, r=reliability, d=durability)


def test_echo_command_streams_yaml_with_explicit_qos(monkeypatch: pytest.MonkeyPatch) -> None:
    cmd = _echo_command(monkeypatch, _info("RELIABLE", "VOLATILE"))
    assert cmd[1:] == [
        "topic",
        "echo",
        "--no-lost-messages",
        "--qos-reliability",
        "reliable",
        "--qos-durability",
        "volatile",
        "/scan",
    ]
    assert "--once" not in cmd and "--csv" not in cmd


def test_echo_command_matches_a_latched_publisher(monkeypatch: pytest.MonkeyPatch) -> None:
    cmd = _echo_command(monkeypatch, _info("RELIABLE", "TRANSIENT_LOCAL"))
    assert cmd[cmd.index("--qos-durability") + 1] == "transient_local"


def test_echo_command_is_permissive_for_best_effort_publishers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cmd = _echo_command(monkeypatch, _info("BEST_EFFORT", "VOLATILE"))
    assert cmd[cmd.index("--qos-reliability") + 1] == "best_effort"


def test_echo_command_is_permissive_for_mixed_or_unknown_publishers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mixed = _info("RELIABLE", "TRANSIENT_LOCAL") + (
        "\nNode name: other\nEndpoint type: PUBLISHER\n"
        "Reliability: BEST_EFFORT\nDurability: VOLATILE\n"
    )
    cmd = _echo_command(monkeypatch, mixed)
    assert cmd[cmd.index("--qos-reliability") + 1] == "best_effort"
    assert cmd[cmd.index("--qos-durability") + 1] == "volatile"
    cmd = _echo_command(monkeypatch)  # no QoS block at all
    assert cmd[cmd.index("--qos-reliability") + 1] == "best_effort"


def test_echo_default_keeps_the_cli_default_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    cmd = _echo_command(monkeypatch)
    assert "--truncate-length" not in cmd and "--full-length" not in cmd


def test_echo_custom_truncate_length(monkeypatch: pytest.MonkeyPatch) -> None:
    cmd = _echo_command(monkeypatch, max_array_length=1024)
    assert cmd[cmd.index("--truncate-length") + 1] == "1024"


def test_echo_none_means_full_length(monkeypatch: pytest.MonkeyPatch) -> None:
    cmd = _echo_command(monkeypatch, max_array_length=None)
    assert "--full-length" in cmd and "--truncate-length" not in cmd


def test_echo_arrays_summary_only(monkeypatch: pytest.MonkeyPatch) -> None:
    cmd = _echo_command(monkeypatch, arrays_summary_only=True)
    assert "--no-arr" in cmd


# ---- DDS tools without a DDS backend ---------------------------------------


def test_dds_tool_error_carries_the_injected_reason() -> None:
    adapter = Ros2CliAdapter(dds_inactive_reason="no DDS backend is selected: X.")
    with pytest.raises(AdapterError, match="DDS module is not active: no DDS backend is selected"):
        adapter.list_participants()


def test_dds_tool_error_has_a_default_reason() -> None:
    with pytest.raises(AdapterError, match="DDS module is not active: install the Cyclone"):
        Ros2CliAdapter().detect_qos_mismatches()


# ---- through the live adapter, with `ros2 bag info` stubbed -------------------


def test_live_analyze_bag_adds_per_topic_spans(monkeypatch: pytest.MonkeyPatch) -> None:
    info = (_BAG_DIR / "ros2_bag_info.txt").read_text()
    monkeypatch.setattr(Ros2CliAdapter, "_run", lambda self, cmd, timeout=8.0: info)
    result = Ros2CliAdapter().analyze_bag(str(_BAG_DIR))
    by_name = {t.name: t for t in result.topics}
    assert result.message_count == 1434
    assert by_name["/scan"].message_count == 177
    assert by_name["/scan"].frequency_hz == pytest.approx(4.9974, abs=1e-4)
    assert by_name["/clock"].frequency_hz == pytest.approx(9.9965, abs=1e-4)
    assert by_name["/tf_static"].latched is True
    assert by_name["/rosout"].latched is True
    assert by_name["/scan"].first_timestamp_ns is not None


def test_live_analyze_bag_keeps_duration_basis_when_the_bag_is_not_readable_here(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    info = (_BAG_DIR / "ros2_bag_info.txt").read_text()
    monkeypatch.setattr(Ros2CliAdapter, "_run", lambda self, cmd, timeout=8.0: info)
    monkeypatch.setattr("topicforge.services.bag_service.is_rosbags_available", lambda: False)
    unreadable = tmp_path / "x.mcap"
    unreadable.write_bytes(b"")
    result = Ros2CliAdapter().analyze_bag(str(unreadable))
    scan = next(t for t in result.topics if t.name == "/scan")
    assert scan.frequency_basis == "bag_duration"
    assert scan.first_timestamp_ns is None and scan.latched is None


def test_live_analyze_bag_missing_path_raises() -> None:
    with pytest.raises(AdapterError, match="does not exist"):
        Ros2CliAdapter().analyze_bag(str(_BAG_DIR / "absent"))
