"""Run one blind-evaluation scenario against an external agent CLI (Codex, Gemini).

    python run_external.py --cli codex --scenario crash_live --out RUNS_DIR \
        --bus-python /path/to/venv/python

Starts the scenario bus and its broker (scenarios.py), waits for the failures
to be injected, then runs the CLI headless in a scratch folder that holds only
tf.py and a short usage note. The CLI gets the user's question and nothing
else: no repository, no TRUTH.md, no scenario code. The transcript and final
answer land in <out>/<cli>/<scenario>/run<N>/. Everything is stopped at the
end, whatever happens.

The CLI runs with a private home (credentials copied in, user settings left
out) so the user's own MCP servers, plugins and instruction files are not
loaded; the real configuration files are never modified. Run this script with
any Python 3.10+; only the bus needs the venv with cyclonedds and dust-dds.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "examples" / "dds"))
from harness import kill_tree  # noqa: E402

DOMAINS = {"crash_live": 84, "restart_loop": 85, "partition_split": 87, "healthy_bus": 89}
# Seconds after the bus is up before the agent starts: past the last injected failure.
SETTLE = {"crash_live": 35, "restart_loop": 40, "partition_split": 5, "healthy_bus": 5}

QUESTIONS = {
    "crash_live": (
        "Our mobile robot is still driving, but the safety team is worried that "
        "something on the robot's DDS bus stopped working a little while ago. "
        "Investigate. Tell me what, if anything, is wrong, which component is "
        "involved, roughly when it happened and what is affected."
    ),
    "restart_loop": (
        "The robot behaves erratically: it stutters and sometimes loses its "
        "path. Look at the DDS bus and tell me whether any component is "
        "unhealthy, what exactly is happening to it, and how many times."
    ),
    "partition_split": (
        "The program named front_viewer never receives any camera images on "
        "topic image, although the camera is running. Find out why."
    ),
    "healthy_bus": (
        "Please do a health check of this robot's DDS bus before we ship it. "
        "Is anything misconfigured, missing or failing? Report real problems "
        "only, and say clearly if everything is fine."
    ),
}

README = """tf.py: a command line client for TopicForge, a read-only DDS/ROS 2
inspection server that is already running for you.

  python tf.py {port} list                       show the tools and their descriptions
  python tf.py {port} <tool> '<json arguments>'  call one tool, e.g.
  python tf.py {port} list_participants '{{}}'

Output is JSON. Use only this command; do not run anything else.
"""

PROMPT = """You are helping a robotics engineer. A read-only inspection server for
the robot's DDS bus (TopicForge) is running. Your only way to look at the bus
is the command line client described in README.md in the current folder:

  python tf.py {port} list
  python tf.py {port} <tool> '<json arguments>'

Rules: run no other shell command and read no other file than README.md; do
not look for the server's sources or for anything outside this folder. Start
with `python tf.py {port} list` to learn the tools.

The engineer's question:

{question}

Finish with (1) your diagnosis, with the evidence you relied on and your
confidence, then (2) a short feedback section on TopicForge's tools: what was
confusing, which field names, units or descriptions misled you, and whether
any output was too large.
"""

CODEX_CONFIG = 'model_reasoning_effort = "high"\n'


def find_exe(name: str) -> str:
    """Resolve a CLI executable (npm shims are .cmd on Windows)."""
    path = shutil.which(name)
    if path is None:
        raise SystemExit(f"{name} not found on PATH")
    return path


def start_bus(scenario: str, bus_python: str, log: Path) -> subprocess.Popen:
    """Launch scenarios.py and wait until it reports the bus up."""
    stop = HERE / f"stop_{scenario}"
    stop.unlink(missing_ok=True)
    flags = (
        subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        if sys.platform == "win32"
        else 0
    )
    out = open(log, "wb")  # noqa: SIM115 - closed with the process at teardown
    proc = subprocess.Popen(
        [bus_python, "-u", str(HERE / "scenarios.py"), scenario],
        stdout=out,
        stderr=subprocess.STDOUT,
        cwd=HERE,
        creationflags=flags,
    )
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if f"[{scenario}] up" in log.read_text(errors="replace"):
            return proc
        if proc.poll() is not None:
            raise SystemExit(f"scenario exited early, see {log}")
        time.sleep(1)
    kill_tree(proc)
    raise SystemExit(f"scenario did not come up, see {log}")


def stop_bus(scenario: str, proc: subprocess.Popen) -> None:
    """Ask scenarios.py to stop, then kill whatever is left of its tree."""
    (HERE / f"stop_{scenario}").touch()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=40)
    kill_tree(proc)
    (HERE / f"stop_{scenario}").unlink(missing_ok=True)


def codex_command(home: Path, model: str | None, work: Path, final: Path) -> tuple[list[str], dict]:
    """Command and env for `codex exec` with a private CODEX_HOME.

    The Windows workspace-write sandbox rejects every command in a private
    CODEX_HOME ("blocked by policy"), so the sandbox is off and blindness rests
    on the working folder, the prompt rules and an audit of the commands run.
    """
    src = Path.home() / ".codex" / "auth.json"
    shutil.copy(src, home / "auth.json")
    # No model line unless asked: the CLI's own default for this account applies.
    config = CODEX_CONFIG + (f'model = "{model}"\n' if model else "")
    (home / "config.toml").write_text(config, encoding="ascii")
    cmd = [
        find_exe("codex"), "exec", "--json", "--skip-git-repo-check", "--ephemeral",
        "--dangerously-bypass-approvals-and-sandbox", "-C", str(work), "-o", str(final), "-",
    ]  # fmt: skip
    return cmd, {"CODEX_HOME": str(home)}


def gemini_command(home: Path, model: str | None) -> tuple[list[str], dict]:
    """Command and env for `gemini -p` with a private GEMINI_CLI_HOME."""
    dst = home / ".gemini"
    dst.mkdir()
    for name in ("oauth_creds.json", "google_accounts.json", "settings.json", "installation_id"):
        src = Path.home() / ".gemini" / name
        if src.exists():
            shutil.copy(src, dst / name)
    cmd = [
        find_exe("gemini"), "--skip-trust", "--approval-mode", "yolo",
        "-o", "stream-json", "-p", "Follow the instructions given on stdin.",
    ]  # fmt: skip
    if model:
        cmd += ["-m", model]
    return cmd, {"GEMINI_CLI_HOME": str(home)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cli", choices=("codex", "gemini"), required=True)
    ap.add_argument("--scenario", choices=sorted(DOMAINS), required=True)
    ap.add_argument("--out", type=Path, required=True, help="runs directory (dated)")
    ap.add_argument(
        "--bus-python", required=True, help="venv python with cyclonedds, dust-dds, topicforge"
    )
    ap.add_argument("--run", type=int, default=1)
    ap.add_argument("--model", default=None, help="model name passed to the CLI")
    ap.add_argument("--timeout", type=int, default=1200)
    a = ap.parse_args()

    port = 8700 + DOMAINS[a.scenario]
    run_dir = a.out / a.cli / a.scenario / f"run{a.run}"
    run_dir.mkdir(parents=True, exist_ok=True)
    question = QUESTIONS[a.scenario]
    prompt = PROMPT.format(port=port, question=question)
    (run_dir / "prompt.txt").write_text(prompt, encoding="ascii")

    bus = start_bus(a.scenario, a.bus_python, run_dir / "bus.log")
    tmp_root = a.out.parent / "_tmp"  # not the system temp dir: Codex refuses it as a home
    tmp_root.mkdir(exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="work_", dir=tmp_root))
    home = Path(tempfile.mkdtemp(prefix="home_", dir=tmp_root))
    status = "ok"
    t0 = time.monotonic()
    try:
        shutil.copy(HERE / "tf.py", scratch / "tf.py")
        (scratch / "README.md").write_text(README.format(port=port), encoding="ascii")
        time.sleep(SETTLE[a.scenario])
        final = run_dir / "final_message.txt"
        if a.cli == "codex":
            cmd, env_add = codex_command(home, a.model, scratch, final)
        else:
            cmd, env_add = gemini_command(home, a.model)
        env = {**os.environ, **env_add}
        with (
            open(run_dir / "transcript.jsonl", "wb") as tx,
            open(run_dir / "stderr.txt", "wb") as er,
        ):
            try:
                proc = subprocess.run(
                    cmd, input=prompt.encode(), stdout=tx, stderr=er,
                    cwd=scratch, env=env, timeout=a.timeout, check=False,
                )  # fmt: skip
                status = f"exit {proc.returncode}"
            except subprocess.TimeoutExpired:
                status = "timeout"
        if a.cli == "gemini":  # no -o flag: rebuild the final answer from the stream
            final.write_text(gemini_final(run_dir / "transcript.jsonl"), encoding="utf-8")
    finally:
        stop_bus(a.scenario, bus)
        shutil.rmtree(scratch, ignore_errors=True)
        shutil.rmtree(home, ignore_errors=True)
    meta = {"cli": a.cli, "scenario": a.scenario, "port": port, "status": status,
            "question": question, "seconds": round(time.monotonic() - t0)}  # fmt: skip
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=1), encoding="ascii")
    print(json.dumps(meta))
    return 0


def gemini_final(path: Path) -> str:
    """Concatenate the assistant message chunks of a stream-json transcript."""
    parts: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "message" and ev.get("role") == "assistant":
            parts.append(str(ev.get("content", "")))
    return "".join(parts)


if __name__ == "__main__":
    raise SystemExit(main())
