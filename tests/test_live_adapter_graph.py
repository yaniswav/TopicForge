"""QoS parsing, `ros2 topic list -v`, echo flags and CSV truncation in the live adapter.

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
    parse_csv_echo,
    parse_topic_info,
)
from topicforge.adapters.ros2_live.parsers import (
    EndpointQos,
    parse_topic_endpoint_qos,
    parse_topic_list_verbose,
    summarize_publisher_qos,
)

_FIXTURES = Path(__file__).parent / "fixtures"
_MODULE = "topicforge.adapters.ros2_live.adapter"
_BAG_DIR = _FIXTURES / "bags" / "omnisim_humble"


def _fixture(name: str) -> str:
    return (_FIXTURES / f"ros2_topic_info_verbose_{name}.txt").read_text(encoding="utf-8")


# ---- topic info --verbose --------------------------------------------------


def test_endpoint_qos_of_a_single_publisher() -> None:
    assert parse_topic_endpoint_qos(_fixture("scan")) == [
        EndpointQos("PUBLISHER", "reliable", "volatile")
    ]


def test_endpoint_qos_of_a_subscription_only_topic() -> None:
    endpoints = parse_topic_endpoint_qos(_fixture("cmd_vel"))
    assert [e.endpoint_type for e in endpoints] == ["SUBSCRIPTION"]
    assert summarize_publisher_qos(endpoints) == (None, None)


def test_endpoint_qos_reads_every_endpoint_of_a_busy_topic() -> None:
    endpoints = parse_topic_endpoint_qos(_fixture("parameter_events"))
    assert len(endpoints) > 4
    assert {e.endpoint_type for e in endpoints} == {"PUBLISHER"}


def test_latched_topic_reports_transient_local() -> None:
    assert summarize_publisher_qos(parse_topic_endpoint_qos(_fixture("tf_static"))) == (
        "reliable",
        "transient_local",
    )


def test_publishers_that_agree_give_one_value() -> None:
    endpoints = parse_topic_endpoint_qos(_fixture("tf"))
    assert len(endpoints) == 2
    assert summarize_publisher_qos(endpoints) == ("reliable", "volatile")


def test_publishers_that_disagree_give_mixed() -> None:
    endpoints = [
        EndpointQos("PUBLISHER", "reliable", "volatile"),
        EndpointQos("PUBLISHER", "best_effort", "transient_local"),
        EndpointQos("SUBSCRIPTION", "best_effort", "volatile"),
    ]
    assert summarize_publisher_qos(endpoints) == ("mixed", "mixed")


def test_unknown_policy_values_are_ignored() -> None:
    text = (
        "Type: a/msg/A\n\nPublisher count: 2\n\n"
        "Node name: n1\nEndpoint type: PUBLISHER\nQoS profile:\n"
        "  Reliability: BEST_EFFORT\n  History (Depth): KEEP_LAST (10)\n  Durability: UNKNOWN\n\n"
        "Node name: n2\nEndpoint type: PUBLISHER\nQoS profile:\n"
        "  Reliability: SYSTEM_DEFAULT\n  Durability: VOLATILE\n"
    )
    assert summarize_publisher_qos(parse_topic_endpoint_qos(text)) == ("best_effort", "volatile")


def test_no_endpoint_blocks_means_no_qos() -> None:
    assert parse_topic_endpoint_qos("Type: a/msg/A\nPublisher count: 0\n") == []
    assert summarize_publisher_qos([]) == (None, None)


def test_parse_topic_info_fills_qos_fields() -> None:
    info = parse_topic_info(
        _fixture("tf_static"), fallback_name="/tf_static", mode_effective="live"
    )
    assert info is not None
    assert info.message_type == "tf2_msgs/msg/TFMessage"
    assert info.publisher_count == 2
    assert info.qos_reliability == "reliable"
    assert info.qos_durability == "transient_local"


def test_parse_topic_info_without_verbose_block_leaves_qos_empty() -> None:
    text = "Type: a/msg/A\nPublisher count: 1\nSubscription count: 0\n"
    info = parse_topic_info(text, fallback_name="/a", mode_effective="live")
    assert info is not None
    assert info.qos_reliability is None and info.qos_durability is None


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
    """Stubs `subprocess.run`: answers by CLI subcommand and records the commands."""

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
            return SimpleNamespace(returncode=answer, stdout="", stderr="boom")
        return SimpleNamespace(returncode=0, stdout=answer, stderr="")


def _install(monkeypatch: pytest.MonkeyPatch, cli: _Cli) -> None:
    monkeypatch.setattr(f"{_MODULE}.shutil.which", lambda name: f"/fake/bin/{name}")
    monkeypatch.setattr(f"{_MODULE}.subprocess.run", cli)


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
    assert all(t.qos_reliability is None and t.qos_durability is None for t in topics.values())
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
    assert (info.qos_reliability, info.qos_durability) == ("reliable", "transient_local")


def test_subprocess_output_is_decoded_as_utf8_with_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = _Cli({"topic list": _LIST_T, "topic list -v": _LIST_V})
    _install(monkeypatch, cli)
    Ros2CliAdapter().list_topics()
    assert all(k["encoding"] == "utf-8" and k["errors"] == "replace" for k in cli.kwargs)


# ---- sample_messages flags -------------------------------------------------


def _echo_command(monkeypatch: pytest.MonkeyPatch, **options: object) -> list[str]:
    cli = _Cli(
        {
            "topic info": "Type: sensor_msgs/msg/LaserScan\nPublisher count: 1\n",
            "topic echo": "1,2,3,4\n",
        }
    )
    _install(monkeypatch, cli)
    Ros2CliAdapter().sample_messages("/scan", 1, **options)  # type: ignore[arg-type]
    return next(c for c in cli.commands if "echo" in c)


def test_echo_default_keeps_the_cli_default_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    cmd = _echo_command(monkeypatch)
    assert cmd[1:] == ["topic", "echo", "--csv", "--once", "/scan"]


def test_echo_custom_truncate_length(monkeypatch: pytest.MonkeyPatch) -> None:
    cmd = _echo_command(monkeypatch, max_array_length=1024)
    assert cmd[1:] == ["topic", "echo", "--csv", "--once", "--truncate-length", "1024", "/scan"]


def test_echo_none_means_full_length(monkeypatch: pytest.MonkeyPatch) -> None:
    cmd = _echo_command(monkeypatch, max_array_length=None)
    assert "--full-length" in cmd and "--truncate-length" not in cmd


def test_echo_arrays_summary_only(monkeypatch: pytest.MonkeyPatch) -> None:
    cmd = _echo_command(monkeypatch, arrays_summary_only=True)
    assert "--no-arr" in cmd


# ---- CSV truncation mark ---------------------------------------------------


def test_csv_truncation_cell_is_not_a_data_column() -> None:
    row = "10,500,frame,1.0,2.0,...,9.0"
    [(ts, payload)] = parse_csv_echo(row)
    assert ts == 0  # 10 is not a plausible epoch second
    assert [payload[f"col_{i}"] for i in range(5)] == ["10", "500", "frame", "1.0", "2.0"]
    assert payload["col_5"] == "9.0"
    assert "col_6" not in payload
    assert payload["_truncated_after_columns"] == [4]
    assert payload["_raw_text"] == row


def test_csv_truncation_with_header_stamp_reindexes_after_the_stamp() -> None:
    [(ts, payload)] = parse_csv_echo("1715600000,5,base_laser,1.5,2.5,...,0.1")
    assert ts == 1_715_600_000_000_000_005
    assert payload["col_0"] == "base_laser"
    assert payload["_truncated_after_columns"] == [2]
    assert payload["col_3"] == "0.1"


def test_csv_without_truncation_has_no_marker_key() -> None:
    [(_, payload)] = parse_csv_echo("1,2,3")
    assert "_truncated_after_columns" not in payload


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


# ---- CSV: --no-arr summaries and cut strings ---------------------------------


def test_csv_sequence_summaries_stay_one_cell_each() -> None:
    row = (
        "1715600000,5,laser,<sequence type: float, length: 541>,"
        "<sequence type: float[8], length: 3>,<array type: float[9]>,7.5"
    )
    [(ts, payload)] = parse_csv_echo(row)
    assert ts == 1_715_600_000_000_000_005
    assert payload["col_0"] == "laser"
    assert payload["col_1"] == "<sequence type: float, length: 541>"
    assert payload["col_2"] == "<sequence type: float[8], length: 3>"
    assert payload["col_3"] == "<array type: float[9]>"
    assert payload["col_4"] == "7.5"
    assert "col_5" not in payload


def test_csv_lists_strings_cut_at_the_truncate_length() -> None:
    row = "1715600000,5,abcd...,short,xy,1.0"
    [(_, payload)] = parse_csv_echo(row, truncate_length=4)
    assert payload["_truncated_columns"] == [0]
    assert "_truncated_after_columns" not in payload
    [(_, uncut)] = parse_csv_echo(row)
    assert "_truncated_columns" not in uncut


def test_csv_cut_string_detection_needs_an_active_truncate_length() -> None:
    [(_, payload)] = parse_csv_echo("1715600000,5,abcd...", truncate_length=None)
    assert "_truncated_columns" not in payload
    [(_, payload)] = parse_csv_echo("1715600000,5,abc...", truncate_length=4)
    assert "_truncated_columns" not in payload


def test_sample_messages_passes_the_effective_truncate_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = _Cli(
        {
            "topic info": "Type: std_msgs/msg/String\nPublisher count: 1\n",
            "topic echo": "1715600000,5,hell...\n",
        }
    )
    _install(monkeypatch, cli)
    [sample] = Ros2CliAdapter().sample_messages("/chat", 1, max_array_length=4)
    assert sample.payload["_truncated_columns"] == [0]
