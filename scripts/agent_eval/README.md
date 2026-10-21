# Agent evaluation

Measures what matters for an MCP server: can an AI agent, given only
TopicForge's tools and a user's question, find the real cause of a problem on
a live DDS bus? The agent has no access to the code, the scenario or the
expected answer.

Pieces:

- `scenarios.py`: builds a bus per scenario from the example role nodes
  (`examples/dds/nodes/`), starts a broker first so TopicForge watches from
  the beginning, then injects failures on a schedule (crash, restart loop).
  `TRUTH.md` holds the expected diagnosis of every scenario.
- `broker.py`: keeps one TopicForge MCP server alive over stdio and relays
  tool calls from a local TCP port, so lifecycle history survives between
  the agent's calls. It listens on 127.0.0.1; `--host 0.0.0.0` is for a
  container whose published port is bound to loopback. `--server-cmd` starts
  any TopicForge command instead of `python -m topicforge` (in the bench image:
  `/opt/tfenv/bin/topicforge`), `--mode`, `--dds-backend` and `--env KEY=VALUE`
  set its environment (`--mode mock --dds-backend mock` needs no ROS 2), and
  `--calls-log FILE` writes one JSON line per request.
- `tf.py`: what the agent runs. `python tf.py <port> list` shows the tools
  exactly as an MCP client sees them (about 35 KB on the mock server);
  `list --brief` is one line per tool (name, inputs, first sentence; about
  2.3 KB); `list --tool <name>` shows one tool in full;
  `python tf.py <port> <tool> '<json>'` calls one. `--host H` before the port
  reaches a broker on another machine.
- `ros2_scenarios.py` and `TRUTH_ROS2.md`: the ROS 2 scenarios of the 0.8.0
  campaign, run on the bench robot with `publisher.py --fault NAME`
  (`tests/integration/ros2/faults.py`). One command per scenario, see below.

Run with the demo venv (`examples/dds/README.md`, section Setup):

```
python scripts/agent_eval/scenarios.py big_bus        # leave it running
python scripts/agent_eval/tf.py 8786 list             # port = 8700 + domain
```

Then give an agent the scenario's question, the `tf.py` command, and the rule
that it may run nothing else. Score its answer against `TRUTH.md`, and collect
its feedback on the tools: the call sequence, what misled it, how often it had
to parse free text or join GUIDs by hand. Stop a scenario by creating
`stop_<scenario>` in this directory.

`run_external.py` runs one scenario against an external agent CLI (Codex, Gemini)
headless and saves the transcript; results are in `docs/EVAL.md`.

`RESULTS_2026-10-02.md` records the three rounds that shaped 0.5.5, run with LLM
agents on the author's 16 test scenarios (the author wrote them and knows the
answers). On 0.5.4 the agents reached 10 of 11 correct diagnoses, mostly by
parsing raw text by hand, and reported a restart loop as a single restart. On
the 0.5.5 branch they reached 16 of 16, with no false alarm on a healthy bus.

## ROS 2 scenarios (0.8.0)

On Linux or WSL with ROS 2 sourced (or in the bench image), one scenario at a time:

```
source /opt/ros/humble/setup.bash
python scripts/agent_eval/ros2_scenarios.py <scenario> [--variant tools|prompt] --out eval_runs
```

Scenarios: `healthy`, `blocked_executor`, `tf_stale`, `qos_mismatch`, `restart_loop`,
`declared_drift`, `graph_v1_v2`. The runner starts the broker, the robot and the fault, then
prints `READY`, the question, and the paths of the agent's prompt (`prompt.txt`) and working
folder (`work/`, only `tf.py` and a README). Create the `stop` file in the run folder when the
agent is done: the runner writes `summary.json` (the agent's tool calls, counted by the broker,
and the true restart count for `restart_loop`). `--variant prompt` adds the text of
`inspect-ros2-robot` (or `--prompt diagnose-nav2`) to the instructions. `--print-only` shows the
plan and the question without ROS 2. The Docker command, the procedure and the table of results
are in `docs/EVAL.md`, "0.8.0 campaign (v2)"; the expected diagnoses are in `TRUTH_ROS2.md`.
