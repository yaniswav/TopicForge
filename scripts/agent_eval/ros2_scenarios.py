"""ROS 2 scenarios for the blind agent evaluation: one command per scenario.

    source /opt/ros/<distro>/setup.bash
    python scripts/agent_eval/ros2_scenarios.py tf_stale --out eval_runs

Run it on a Linux or WSL host with ROS 2 sourced and TopicForge installed (`pip install
-e ".[bags]"`), or inside the bench image (see `docs/EVAL.md`, "0.8.0 campaign (v2)"). For
one scenario it

1. starts a broker (`broker.py`: one persistent TopicForge, live mode, Cyclone DDS backend),
   so the server watches from the beginning;
2. starts the bench robot with the fault (`tests/integration/ros2/publisher.py --fault NAME`)
   and the processes that fault needs (`faults.COMPANIONS`);
3. waits for the fault to be in place (`SETTLE_S`; `graph_v1_v2` takes its baseline snapshot
   first, then changes the graph);
4. writes the agent's workspace (`<out>/<scenario>-<variant>/work/`: `tf.py` and a README),
   the agent's prompt (`prompt.txt`) and prints both paths after a `READY` line;
5. waits for `<out>/<scenario>-<variant>/stop` (or Ctrl-C, or `--max-minutes`), then stops
   everything and writes `summary.json`: how many TopicForge calls the agent made (counted by
   the broker, not reported by the agent) and, for `restart_loop`, how many times the node
   really restarted.

The agent is any process that can run `python tf.py <port> ...` in the workspace folder and
nothing else. `--variant prompt` adds the text of an MCP prompt (`inspect-ros2-robot` by default,
`--prompt diagnose-nav2` for the other) to the agent's instructions: same tools, same question,
and the number of calls tells how far the prompt alone gets. The expected answers are in
`TRUTH_ROS2.md`; score against them, not against this file.

`--print-only` prints the question and the plan without starting anything (no ROS 2 needed).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BENCH = REPO / "tests" / "integration" / "ros2"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(BENCH))
import faults  # noqa: E402
import tf  # noqa: E402

DEFAULT_PORT = 8801
PROMPTS = ("inspect-ros2-robot", "diagnose-nav2")
VARIANTS = ("tools", "prompt")
# Seconds between "the robot is up" and "the agent starts": past the point where the fault
# can be seen (restart_loop needs several restarts; tf_stale a few tf captures).
SETTLE_S = {
    faults.HEALTHY: 20,
    faults.BLOCKED_EXECUTOR: 15,
    faults.TF_STALE: 15,
    faults.QOS_MISMATCH: 15,
    faults.RESTART_LOOP: 45,
    faults.DECLARED_DRIFT: 15,
    faults.GRAPH_V1_V2: 8,  # after the change; the baseline is taken 15 s after the start
}
BASELINE_AFTER_S = 15

PARAMS_FILE = BENCH / faults.DRIFT_PARAMS_FILE
QUESTIONS = {
    faults.HEALTHY: (
        "Please do a health check of this ROS 2 robot before we ship it. Is anything "
        "misconfigured, missing or failing? Report real problems only, and say clearly if "
        "everything is fine."
    ),
    faults.BLOCKED_EXECUTOR: (
        "Our launch tooling hangs every time it tries to read the parameters of the node "
        "/bench_blocked, although the node is up. Find out what is going on with it, and say "
        "whether anything else on the robot is wrong."
    ),
    faults.TF_STALE: (
        "Navigation keeps failing with transform errors between the map frame and the robot's "
        "base_link frame. Find out why, and which component is responsible."
    ),
    faults.QOS_MISMATCH: (
        "The program /edge_viewer never receives any data on /scan_edge, although the laser "
        "publisher is running. Find out why, and what should be changed."
    ),
    faults.RESTART_LOOP: (
        "The robot behaves erratically: it stutters and sometimes loses its path. Look at the "
        "ROS 2 graph and tell me whether any component is unhealthy, what exactly is happening "
        "to it, and how many times."
    ),
    faults.DECLARED_DRIFT: (
        "Before we ship I need to know whether the running robot matches what we declared. Its "
        "parameter file is {params_file} (a path on the robot's computer) and its URDF is "
        "published on /robot_description. List every difference between what is declared and "
        "what is running, for the node /bench_robot and for the frames."
    ),
    faults.GRAPH_V1_V2: (
        "TopicForge took a snapshot of this robot's ROS graph while it ran our launch file v1. "
        "The robot has since been restarted with launch file v2. Report what changed in the "
        "graph between v1 and v2, and nothing else: do not list things that did not change."
    ),
}

README = """tf.py: a command line client for TopicForge, a read-only ROS 2 and DDS
inspection server that is already running for you.

  python tf.py {port} list --brief               one line per tool: name(inputs): first sentence
  python tf.py {port} list --tool <name>         the full description and input schema of one tool
  python tf.py {port} <tool> '<json arguments>'  call one tool, e.g.
  python tf.py {port} list_nodes '{{}}'

Output is JSON. Use only this command; do not run anything else.
"""

PROMPT = """You are helping a robotics engineer. A read-only inspection server for the robot's
ROS 2 graph and DDS bus (TopicForge) is running. Your only way to look at the robot is the
command line client described in README.md in the current folder:

  python tf.py {port} list --brief
  python tf.py {port} <tool> '<json arguments>'

Rules: run no other shell command and read no other file than README.md; do not look for the
server's sources or for anything outside this folder. Start with `python tf.py {port} list
--brief` to learn the tools.
{prompt_block}
The engineer's question:

{question}

Finish with (1) your diagnosis, with the evidence you relied on (tool, field, value) and your
confidence, then (2) a short feedback section on TopicForge's tools: what was confusing, which
field names, units or descriptions misled you, and whether any output was too large.
"""

PROMPT_BLOCK = """
The server publishes a written procedure for this kind of question (an MCP prompt named
`{name}`). Its text follows; use it as your guide.

--- {name} ---
{text}
--- end of {name} ---
"""


# ------------------------------------------------------------------ pure parts


def question_for(fault: str, params_file: Path | str = PARAMS_FILE) -> str:
    """The user question of `fault`, with the parameter file path filled in."""
    return QUESTIONS[fault].format(params_file=params_file)


def agent_prompt(port: int, question: str, prompt_name: str | None, prompt_text: str | None) -> str:
    """The instructions handed to the agent; `prompt_name` None is the tools-only variant."""
    block = ""
    if prompt_name is not None:
        block = PROMPT_BLOCK.format(name=prompt_name, text=(prompt_text or "").strip())
    return PROMPT.format(port=port, question=question, prompt_block=block)


def summarize_calls(lines: Iterable[str], since: float = 0.0) -> dict[str, Any]:
    """Count the agent's requests in a broker calls log (JSON lines), from time `since` on.

    `tool_calls` is the number that matters for the "<= 6 calls with the prompt alone" criterion:
    every request that is not a catalogue listing. `list_calls` counts the listings apart.
    """
    entries = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("t", 0) >= since:
            entries.append(entry)
    tools = [e for e in entries if e.get("kind") == "tool"]
    return {
        "tool_calls": len(tools),
        "list_calls": sum(1 for e in entries if e.get("kind") == "list"),
        "by_tool": dict(sorted(Counter(str(e.get("tool")) for e in tools).items())),
        "errors": sum(1 for e in tools if e.get("is_error")),
        "answer_bytes": sum(int(e.get("bytes", 0)) for e in entries),
        "sequence": [str(e.get("tool")) for e in tools],
    }


def count_flapper_starts(text: str) -> int:
    """Processes the restart-loop supervisor has started, from its `start N` lines."""
    starts = [
        int(p[1])
        for p in (ln.split() for ln in text.splitlines())
        if p[:1] == ["start"] and len(p) == 2 and p[1].isdigit()
    ]
    return max(starts, default=0)


def plan(fault: str, port: int, variant: str, prompt_name: str) -> list[str]:
    """Human-readable steps of one scenario, for `--print-only` and the log."""
    steps = [f"broker on port {port} (live mode, Cyclone backend)"]
    steps.append(f"publisher.py --fault {fault}")
    for c in faults.COMPANIONS[fault]:
        when = "after the baseline snapshot" if c.when == "after_baseline" else "with the publisher"
        steps.append(f"{' '.join([c.script, *c.args])} ({when})")
    if fault == faults.GRAPH_V1_V2:
        steps.append(f"graph_diff baseline {BASELINE_AFTER_S} s in, then the change, then wait")
    steps.append(f"settle {SETTLE_S[fault]} s")
    steps.append(
        "agent: tools only" if variant == "tools" else f"agent: tools + prompt {prompt_name}"
    )
    return steps


# ------------------------------------------------------------------ processes


def spawn(cmd: Sequence[str], log: Path, env: dict[str, str]) -> subprocess.Popen:
    """Start `cmd` in a session of its own, output to `log`."""
    out = open(log, "wb")  # noqa: SIM115 - lives as long as the process
    return subprocess.Popen(
        list(cmd), stdout=out, stderr=subprocess.STDOUT, env=env, cwd=BENCH, start_new_session=True
    )


def stop_all(procs: Sequence[subprocess.Popen]) -> None:
    """SIGTERM every process group, then SIGKILL what is left after a few seconds."""
    for p in procs:
        _signal_group(p, signal.SIGTERM)
    deadline = time.monotonic() + 8
    for p in procs:
        try:
            p.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            _signal_group(p, signal.SIGKILL)


def _signal_group(p: subprocess.Popen, sig: int) -> None:
    if p.poll() is not None:
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(os.getpgid(p.pid), sig)


def wait_for_text(path: Path, needle: str, seconds: float, proc: subprocess.Popen) -> bool:
    """True once `needle` appears in the file `path`; False on timeout or if `proc` died."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if path.exists() and needle in path.read_text(errors="replace"):
            return True
        if proc.poll() is not None:
            return False
        time.sleep(0.5)
    return False


def wait_for_topic(topic: str, seconds: float, env: dict[str, str]) -> bool:
    """True once `ros2 topic list` shows `topic`."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        out = subprocess.run(
            ["ros2", "topic", "list"], capture_output=True, text=True, env=env, check=False
        )
        if topic in out.stdout.split():
            return True
        time.sleep(1)
    return False


def companion_cmd(c: faults.Companion, ros_python: str) -> list[str]:
    """Command line of one companion process."""
    return [ros_python, str(BENCH / c.script), *c.args]


def _interrupt(signum: int, frame: object) -> None:
    """SIGTERM ends the run like Ctrl-C does."""
    raise KeyboardInterrupt


def run(a: argparse.Namespace) -> int:
    fault, port = a.scenario, a.port
    if os.name == "nt":
        raise SystemExit("run this on Linux or WSL with ROS 2 sourced (use --print-only here)")
    if shutil.which("ros2") is None:
        raise SystemExit("ros2 not found on PATH: source /opt/ros/<distro>/setup.bash first")
    run_dir = (a.out / f"{fault}-{a.variant}").resolve()
    work = run_dir / "work"
    shutil.rmtree(run_dir, ignore_errors=True)
    work.mkdir(parents=True)
    stop_file = run_dir / "stop"
    env = {**os.environ, "ROS_DOMAIN_ID": str(a.domain)}
    calls_log = run_dir / "calls.jsonl"
    procs: list[subprocess.Popen] = []
    signal.signal(signal.SIGTERM, _interrupt)
    try:
        broker_cmd = [
            a.broker_python, str(HERE / "broker.py"), "--domain", str(a.domain), "--port", str(port),
            "--host", a.host, "--dds-backend", a.dds_backend, "--calls-log", str(calls_log),
        ]  # fmt: skip
        if a.server_cmd:
            broker_cmd += ["--server-cmd", a.server_cmd]
        broker = spawn(broker_cmd, run_dir / "broker.log", env)
        procs.append(broker)
        if not wait_for_text(run_dir / "broker.log", "broker ready", 90, broker):
            raise SystemExit(f"broker did not start, see {run_dir / 'broker.log'}")
        time.sleep(3)  # let it join the domain before the robot appears

        procs.append(
            spawn(
                [a.ros_python, str(BENCH / "publisher.py"), "--fault", fault],
                run_dir / "publisher.log",
                env,
            )
        )
        if not wait_for_topic("/scan", 60, env):
            raise SystemExit(f"the robot never appeared, see {run_dir / 'publisher.log'}")
        for c in faults.COMPANIONS[fault]:
            if c.when == "start":
                name = f"{Path(c.script).stem}-{'-'.join(c.args)}".strip("-")
                procs.append(spawn(companion_cmd(c, a.ros_python), run_dir / f"{name}.log", env))
        if fault == faults.GRAPH_V1_V2:
            time.sleep(BASELINE_AFTER_S)
            baseline = tf.ask("127.0.0.1", port, {"tool": "graph_diff", "args": {}})
            (run_dir / "baseline.json").write_text(json.dumps(baseline, indent=1))
            for c in faults.COMPANIONS[fault]:
                procs.append(
                    spawn(
                        companion_cmd(c, a.ros_python), run_dir / f"{Path(c.script).stem}.log", env
                    )
                )
        time.sleep(SETTLE_S[fault])
        tf.ask("127.0.0.1", port, {"tool": "list_participants", "args": {}})  # seen everyone

        prompt_text = None
        if a.variant == "prompt":
            prompt_text = tf.ask("127.0.0.1", port, {"prompt": a.prompt, "args": {}})["text"]
        question = question_for(fault, PARAMS_FILE)
        (work / "tf.py").write_bytes((HERE / "tf.py").read_bytes())
        (work / "README.md").write_text(README.format(port=port), encoding="ascii")
        prompt = agent_prompt(
            port, question, a.prompt if a.variant == "prompt" else None, prompt_text
        )
        (run_dir / "prompt.txt").write_text(prompt, encoding="ascii", errors="replace")

        ready_at = time.time()
        flapper_log = run_dir / "fault_nodes-flapper.log"
        starts_at_ready = (
            count_flapper_starts(flapper_log.read_text()) if flapper_log.exists() else 0
        )
        print(f"READY {fault} {a.variant} port={port}", flush=True)
        print(f"agent workspace: {work}", flush=True)
        print(f"agent prompt:    {run_dir / 'prompt.txt'}", flush=True)
        print(
            f"stop with:       touch {stop_file}   (summary: {run_dir / 'summary.json'})",
            flush=True,
        )
        print("--- question ---\n" + question, flush=True)
        deadline = time.monotonic() + a.max_minutes * 60
        with contextlib.suppress(
            KeyboardInterrupt
        ):  # Ctrl-C and SIGTERM end the wait, not the summary
            while not stop_file.exists() and time.monotonic() < deadline:
                time.sleep(1)

        starts_at_stop = (
            count_flapper_starts(flapper_log.read_text()) if flapper_log.exists() else 0
        )
        lines = calls_log.read_text().splitlines() if calls_log.exists() else []
        summary = {
            "scenario": fault, "variant": a.variant,
            "prompt": a.prompt if a.variant == "prompt" else None,
            "question": question,
            **summarize_calls(lines, since=ready_at),
        }  # fmt: skip
        if fault == faults.RESTART_LOOP:
            summary["restarts_at_question"] = max(starts_at_ready - 1, 0)
            summary["restarts_at_stop"] = max(starts_at_stop - 1, 0)
        (run_dir / "summary.json").write_text(json.dumps(summary, indent=1), encoding="ascii")
        print(json.dumps(summary), flush=True)
        return 0
    finally:
        stop_all(procs)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Start one ROS 2 scenario for the agent evaluation.")
    ap.add_argument("scenario", choices=faults.FAULTS)
    ap.add_argument("--variant", choices=VARIANTS, default="tools")
    ap.add_argument("--prompt", choices=PROMPTS, default=PROMPTS[0], help="with --variant prompt")
    ap.add_argument("--out", type=Path, default=Path("eval_runs"))
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--host", default="127.0.0.1", help="0.0.0.0 inside a container")
    ap.add_argument("--domain", type=int, default=int(os.environ.get("ROS_DOMAIN_ID", "0")))
    ap.add_argument("--dds-backend", default="cyclone")
    ap.add_argument("--server-cmd", default=None, help="how the broker starts TopicForge")
    ap.add_argument(
        "--broker-python", default=sys.executable, help="Python with mcp and topicforge"
    )
    ap.add_argument("--ros-python", default="python3", help="Python of the sourced ROS 2 (rclpy)")
    ap.add_argument("--max-minutes", type=float, default=30)
    ap.add_argument("--print-only", action="store_true")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    if a.print_only:
        print("\n".join(f"- {s}" for s in plan(a.scenario, a.port, a.variant, a.prompt)))
        print("\n--- question ---\n" + question_for(a.scenario))
        return 0
    return run(a)


if __name__ == "__main__":
    sys.exit(main())
