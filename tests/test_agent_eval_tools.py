"""Tests for the agent-evaluation tooling (`scripts/agent_eval`) and the bench fault scenarios.

No ROS 2 and no Docker: the pure parts only. `tf.py list --brief` is measured on the mock
server's own tool listing; the broker's command line, environment and request handling run
against a fake MCP session; the fault profiles, URDFs and scenario table are checked against
each other (a fault without a question, a companion script that does not exist).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from topicforge.adapters.common.urdf import (
    declared_frames_missing,
    frames_not_in_urdf,
    parse_urdf,
)
from topicforge.config import Settings
from topicforge.server import build_app

ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = ROOT / "scripts" / "agent_eval"
BENCH_DIR = ROOT / "tests" / "integration" / "ros2"
for folder in (EVAL_DIR, BENCH_DIR):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

import broker  # noqa: E402
import faults  # noqa: E402
import ground_truth  # noqa: E402
import ros2_scenarios as scenarios  # noqa: E402
import tf  # noqa: E402


@pytest.fixture(scope="module")
def catalogue() -> list[dict]:
    """The mock server's tools in the shape the broker returns them."""
    app = build_app(
        Settings(mode="mock", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False)
    )
    return [
        {"name": t.name, "description": t.description, "inputSchema": t.input_schema}
        for t in asyncio.run(app.list_tools())
    ]


# ---- tf.py list --brief -------------------------------------------------------------------


def test_the_brief_catalogue_is_a_small_fraction_of_the_full_one(catalogue: list[dict]) -> None:
    brief = tf.format_brief(catalogue)
    full = json.dumps(catalogue, indent=1)
    assert len(catalogue) >= 16
    assert len(brief.encode()) < 4_000
    assert len(brief) * 8 < len(full)
    assert "\n\n" not in brief and brief.isascii()


def test_every_tool_gets_one_line_with_its_inputs_and_a_sentence(catalogue: list[dict]) -> None:
    lines = tf.format_brief(catalogue).splitlines()[1:]
    assert len(lines) == len(catalogue)
    for tool, line in zip(catalogue, lines, strict=True):
        name, _, rest = line.partition("(")
        assert name == tool["name"]
        inputs, _, sentence = rest.partition("): ")
        assert [i.rstrip("*") for i in inputs.split(", ") if i] == list(
            tool["inputSchema"].get("properties", {})
        )
        assert sentence.strip() and len(sentence) <= tf.BRIEF_SENTENCE_MAX + 12


def test_required_inputs_are_starred_and_the_ros_tag_is_not_the_sentence(
    catalogue: list[dict],
) -> None:
    by_name = {t["name"]: tf.brief_line(t) for t in catalogue}
    assert by_name["get_topic_info"].startswith("get_topic_info(topic*): (ROS 2) Describe")
    assert by_name["graph_diff"].startswith("graph_diff(baseline, bag_path): (ROS 2) ")
    assert "only; on a DDS-only" not in " ".join(by_name.values())


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Peek at it. Then more.", "Peek at it."),
        ("List nodes, e.g. `/a`. Then more.", "List nodes, e.g. `/a`."),
        ("Does one thing", "Does one thing"),
        ("", ""),
        ("ROS 2 graph only. Describe one node.", "(ROS 2) Describe one node."),
        (
            "ROS 2 graph only; on a DDS-only setup use `x`. Collect messages. More.",
            "(ROS 2) Collect messages.",
        ),
        ("Line one\n  continues here. Second sentence.", "Line one continues here."),
    ],
)
def test_first_sentence(text: str, expected: str) -> None:
    assert tf.first_sentence(text) == expected


def test_a_long_sentence_is_cut_at_the_first_colon_or_hard_cut() -> None:
    colon = "Report the environment state as a `HealthReport`: " + "field, " * 40 + "end."
    assert tf.first_sentence(colon) == "Report the environment state as a `HealthReport`."
    flat = "word " * 80 + "end."
    cut = tf.first_sentence(flat, limit=60)
    assert len(cut) <= 60 and cut.endswith("...")


def test_input_names_handle_missing_schemas() -> None:
    assert tf.input_names(None) == []
    assert tf.input_names({"properties": {"a": {}, "b": {}}, "required": ["b"]}) == ["a", "b*"]


def test_tf_argument_parsing() -> None:
    assert tf.parse_args(["8801", "list"]) == ("127.0.0.1", 8801, {"list": True}, "")
    assert tf.parse_args(["8801", "list", "--brief"])[3] == "brief"
    assert tf.parse_args(["--host", "10.0.0.2", "8801", "list", "--tool", "graph_diff"]) == (
        "10.0.0.2",
        8801,
        {"list": True},
        "graph_diff",
    )
    assert tf.parse_args(["8801", "list_nodes"])[2] == {"tool": "list_nodes", "args": {}}
    assert tf.parse_args(["8801", "get_node_info", '{"node": "/a"}'])[2] == {
        "tool": "get_node_info",
        "args": {"node": "/a"},
    }
    for bad in (["8801"], ["8801", "list", "--wat"], ["--host"]):
        with pytest.raises(SystemExit):
            tf.parse_args(bad)


# ---- broker.py ---------------------------------------------------------------------------


def test_the_broker_listens_on_loopback_unless_told_otherwise() -> None:
    parser = broker.build_parser()
    assert parser.parse_args(["--domain", "0", "--port", "1"]).host == "127.0.0.1"
    wide = parser.parse_args(["--domain", "0", "--port", "1", "--host", "0.0.0.0"])
    assert wide.host == "0.0.0.0"


def test_server_cmd_defaults_to_this_python_and_splits_like_a_shell() -> None:
    assert broker.parse_server_cmd(None) == [sys.executable, "-m", "topicforge"]
    assert broker.parse_server_cmd("/opt/tfenv/bin/topicforge") == ["/opt/tfenv/bin/topicforge"]
    assert broker.parse_server_cmd("uvx --from topicforge topicforge")[:2] == ["uvx", "--from"]
    assert broker.parse_server_cmd('"tool with space" --x')[0] == "tool with space"
    with pytest.raises(ValueError, match="empty"):
        broker.parse_server_cmd("   ")


def test_server_environment_sets_mode_backend_domain_and_lets_extras_win() -> None:
    env = broker.build_server_env(
        {"PATH": "/bin"}, domain=7, extra={"TOPICFORGE_LOG_LEVEL": "INFO"}
    )
    assert env["PATH"] == "/bin"
    assert env["TOPICFORGE_MODE"] == "live"
    assert env["TOPICFORGE_DDS_BACKEND"] == "cyclone"
    assert env["TOPICFORGE_DDS_DOMAIN_ID"] == "7"
    assert env["TOPICFORGE_LOG_LEVEL"] == "INFO"
    mock = broker.build_server_env({}, domain=0, mode="mock", dds_backend="mock")
    assert (mock["TOPICFORGE_MODE"], mock["TOPICFORGE_DDS_BACKEND"]) == ("mock", "mock")


def test_env_pairs() -> None:
    assert broker.parse_env_pairs(["A=1", "B=x=y"]) == {"A": "1", "B": "x=y"}
    for bad in ("A", "=1"):
        with pytest.raises(ValueError, match="KEY=VALUE"):
            broker.parse_env_pairs([bad])


class _FakeSession:
    """Just enough of an MCP client session for `broker.answer_request`."""

    def __init__(self, *, error: bool = False, structured: bool = True) -> None:
        self.error, self.structured = error, structured

    async def list_tools(self):
        tool = SimpleNamespace(name="t", description="d", input_schema={"type": "object"})
        return SimpleNamespace(tools=[tool])

    async def get_prompt(self, name, arguments):
        message = SimpleNamespace(content=SimpleNamespace(text=f"prompt {name} {arguments}"))
        return SimpleNamespace(messages=[message])

    async def call_tool(self, name, arguments):
        return SimpleNamespace(
            is_error=self.error,
            structured_content={"ok": name} if self.structured else None,
            content=[SimpleNamespace(text="boom" if self.error else "plain")],
        )


def test_the_broker_answers_list_prompt_and_tool_requests() -> None:
    listed = asyncio.run(broker.answer_request(_FakeSession(), {"list": True}))
    assert listed == [{"name": "t", "description": "d", "inputSchema": {"type": "object"}}]
    prompt = asyncio.run(broker.answer_request(_FakeSession(), {"prompt": "p", "args": {"a": "b"}}))
    assert prompt == {"text": "prompt p {'a': 'b'}"}
    ok = asyncio.run(broker.answer_request(_FakeSession(), {"tool": "x", "args": {}}))
    assert ok == {"ok": "x"}
    text = asyncio.run(broker.answer_request(_FakeSession(structured=False), {"tool": "x"}))
    assert text == ["plain"]
    err = asyncio.run(broker.answer_request(_FakeSession(error=True), {"tool": "x"}))
    assert err == {"isError": True, "content": ["boom"]}


def test_the_calls_log_has_one_line_per_request_and_feeds_the_summary(tmp_path: Path) -> None:
    log = tmp_path / "calls.jsonl"
    broker.log_call(None, {"list": True}, [])  # no log file: nothing happens
    broker.log_call(log, {"list": True}, [{"name": "t"}])
    broker.log_call(log, {"tool": "graph_diff", "args": {}}, {"changes": []})
    broker.log_call(log, {"tool": "get_node_info", "args": {"node": "/a"}}, {"isError": True})
    broker.log_call(log, {"prompt": "inspect-ros2-robot"}, {"text": "x"})
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    assert [e["kind"] for e in entries] == ["list", "tool", "tool", "prompt"]
    assert entries[2]["is_error"] is True and entries[2]["args"] == {"node": "/a"}
    summary = scenarios.summarize_calls(log.read_text().splitlines())
    assert summary["tool_calls"] == 2
    assert summary["list_calls"] == 1
    assert summary["errors"] == 1
    assert summary["sequence"] == ["graph_diff", "get_node_info"]


# ---- bench faults ------------------------------------------------------------------------


def test_every_fault_has_a_companion_entry_a_question_a_settle_time_and_a_truth_section() -> None:
    truth = (EVAL_DIR / "TRUTH_ROS2.md").read_text(encoding="utf-8")
    for fault in faults.FAULTS:
        assert fault in faults.COMPANIONS
        assert fault in scenarios.QUESTIONS and fault in scenarios.SETTLE_S
        assert f"\n## {fault}\n" in truth
    assert set(faults.COMPANIONS) == set(scenarios.QUESTIONS) == set(scenarios.SETTLE_S)
    for companions in faults.COMPANIONS.values():
        for companion in companions:
            assert (BENCH_DIR / companion.script).is_file()
            assert companion.when in ("start", "after_baseline")


def test_fault_arguments_are_split_from_the_ros_arguments() -> None:
    assert faults.parse_fault_args([]) == (None, [])
    assert faults.parse_fault_args(["--fault", "tf_stale"]) == ("tf_stale", [])
    ros = ["--ros-args", "-r", "__ns:=/x"]
    assert faults.parse_fault_args(["--fault", "healthy", *ros]) == ("healthy", ros)
    assert faults.parse_fault_args(ros) == (None, ros)
    with pytest.raises(SystemExit):
        faults.parse_fault_args(["--fault", "nonsense"])


def test_profiles_set_exactly_one_fault_each() -> None:
    flags = ("map_odom_flaky", "urdf_drift", "edge_best_effort")
    expected = {
        faults.TF_STALE: "map_odom_flaky",
        faults.DECLARED_DRIFT: "urdf_drift",
        faults.QOS_MISMATCH: "edge_best_effort",
    }
    for fault in faults.FAULTS:
        profile = faults.profile_for(fault)
        assert profile.fault == fault
        on = [f for f in flags if getattr(profile, f)]
        assert on == ([expected[fault]] if fault in expected else [])
    with pytest.raises(ValueError, match="unknown fault"):
        faults.profile_for("nonsense")


def _frames() -> set[str]:
    """The TF frames of the clean robot, as `inspect_tf` would list them."""
    edges = [*ground_truth.TF_STATIC_EDGES, *faults.steady_edges(), faults.MAP_ODOM_EDGE]
    return {name for edge in edges for name in edge}


def test_the_clean_urdf_declares_nothing_that_is_not_published() -> None:
    model = parse_urdf(faults.urdf_text(drift=False))
    assert declared_frames_missing(model, _frames()) == []
    assert frames_not_in_urdf(model, _frames()) == ["map", "odom"]


def test_the_drift_urdf_adds_one_revolute_link_nothing_publishes() -> None:
    model = parse_urdf(faults.urdf_text(drift=True))
    assert declared_frames_missing(model, _frames()) == ["arm_link"]
    assert [j.type for j in model.joints if j.child == "arm_link"] == ["revolute"]


def test_the_ci_bench_urdf_is_untouched_by_the_clean_one() -> None:
    assert "arm_link" in ground_truth.urdf_text()
    assert "eval_robot" not in ground_truth.urdf_text()
    assert "tool" not in parse_urdf(ground_truth.urdf_text()).links


def test_the_clean_robot_has_no_second_parent_and_one_root() -> None:
    parents: dict[str, set[str]] = {}
    for parent, child in [
        *ground_truth.TF_STATIC_EDGES,
        *faults.steady_edges(),
        faults.MAP_ODOM_EDGE,
    ]:
        parents.setdefault(child, set()).add(parent)
    assert all(len(p) == 1 for p in parents.values())
    assert set(_frames()) - set(parents) == {"map"}


def test_the_publisher_keeps_the_ci_bench_when_no_fault_is_given() -> None:
    """Static check (rclpy is not importable here): every clean-robot change sits behind a profile."""
    source = (BENCH_DIR / "publisher.py").read_text(encoding="utf-8")
    assert "if profile is None:\n            self._setup_clock_topics(qos)" in source
    assert (
        "model.write_truth()" in source
        and "if profile is None:\n        model.write_truth()" in source
    )
    assert "model.urdf_text() if profile is None" in source
    assert "model.TF_STEADY_EDGES if profile is None" in source


# ---- the runner --------------------------------------------------------------------------


def test_the_parameter_file_of_the_drift_question_exists_and_is_named_in_it() -> None:
    assert scenarios.PARAMS_FILE.is_file()
    assert str(scenarios.PARAMS_FILE) in scenarios.question_for(faults.DECLARED_DRIFT)
    assert "{" not in scenarios.question_for(faults.DECLARED_DRIFT)


def test_questions_do_not_name_the_cause() -> None:
    giveaways = ("best_effort", "reliable", "arm_link", "extra_gain", "executor", "/bench_v2")
    for fault in faults.FAULTS:
        text = scenarios.question_for(fault).lower()
        assert not any(word in text for word in giveaways), fault


def test_the_agent_prompt_carries_the_port_the_question_and_optionally_the_mcp_prompt() -> None:
    plain = scenarios.agent_prompt(8801, "Why?", None, None)
    assert "python tf.py 8801 list --brief" in plain and "Why?" in plain
    assert "MCP prompt" not in plain
    with_prompt = scenarios.agent_prompt(8801, "Why?", "inspect-ros2-robot", "1. `health_check`.")
    assert "--- inspect-ros2-robot ---" in with_prompt and "1. `health_check`." in with_prompt
    assert with_prompt.isascii() and plain.isascii()


def test_the_calls_summary_counts_from_the_ready_time_and_apart_from_listings() -> None:
    def line(t: float, kind: str, tool: str | None, error: bool = False) -> str:
        return json.dumps({"t": t, "kind": kind, "tool": tool, "bytes": 10, "is_error": error})

    lines = [
        line(1, "tool", "list_participants"),  # the runner's own call, before READY
        line(10, "list", None),
        line(11, "tool", "health_check"),
        line(12, "tool", "inspect_tf", error=True),
        line(13, "tool", "health_check"),
        "not json",
        "",
    ]
    summary = scenarios.summarize_calls(lines, since=5)
    assert summary["tool_calls"] == 3
    assert summary["list_calls"] == 1
    assert summary["by_tool"] == {"health_check": 2, "inspect_tf": 1}
    assert summary["errors"] == 1
    assert summary["answer_bytes"] == 40
    assert scenarios.summarize_calls([])["tool_calls"] == 0


def test_flapper_starts_are_read_from_the_supervisor_log() -> None:
    assert scenarios.count_flapper_starts("") == 0
    assert scenarios.count_flapper_starts("start 1\nstart 2\nnoise\nstart 3\n") == 3
    timed = "start 1 100.0\nstart 2 110.0\nstart 3 120.0\n"
    assert scenarios.count_flapper_starts(timed) == 3
    assert scenarios.count_flapper_starts(timed, before=115.0) == 2
    assert scenarios.count_flapper_starts("start 1\n", before=115.0) == 0


def test_first_call_time_skips_listings_and_earlier_calls() -> None:
    lines = [
        '{"t": 5, "kind": "tool", "tool": "health_check"}',
        '{"t": 11, "kind": "list", "tool": null}',
        "not json",
        '{"t": 12, "kind": "tool", "tool": "list_nodes"}',
    ]
    assert scenarios.first_call_time(lines, since=10) == 12.0
    assert scenarios.first_call_time(lines[:2], since=10) is None


def test_print_only_needs_no_ros(capsys: pytest.CaptureFixture[str]) -> None:
    assert scenarios.main(["graph_v1_v2", "--print-only"]) == 0
    out = capsys.readouterr().out
    assert "graph_change.py" in out and "baseline" in out and "--- question ---" in out
    assert scenarios.main(["healthy", "--variant", "prompt", "--print-only"]) == 0
    assert "prompt inspect-ros2-robot" in capsys.readouterr().out


def test_the_runner_defaults_to_loopback_and_one_port() -> None:
    args = scenarios.build_parser().parse_args(["tf_stale"])
    assert (args.host, args.port, args.variant) == ("127.0.0.1", 8801, "tools")
    assert args.prompt == "inspect-ros2-robot"
