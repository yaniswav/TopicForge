"""Shared plumbing for the TopicForge DDS examples.

Every example under `examples/dds/` follows the same shape:

  1. a few small DDS programs play robot roles ("lidar_driver",
     "nav_planner"), each from a given vendor, started by a `Bus`;
  2. TopicForge is started as a real MCP server over stdio and questioned
     through the official MCP client, exactly the way Claude Desktop or
     Claude Code would question it;
  3. the example checks that TopicForge told the truth about the bus, prints
     PASS or FAIL, and stops every program it started.

`python run.py --hold` skips step 2: the programs stay up so that you can ask
your own MCP client the questions in the example's README.

TopicForge never publishes anything. The role programs are ordinary DDS
applications; TopicForge only reads the standard DDS discovery topics.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

HERE = Path(__file__).resolve().parent
NODES = HERE / "nodes"

# Python module each vendor's role program needs, and how to get it.
VENDOR_MODULE = {"cyclone": "cyclonedds", "dust": "dust_dds", "rti": "rti"}
VENDOR_INSTALL = {
    "cyclone": 'pip install "topicforge[dds]"',
    "dust": "pip install dust-dds==0.16.0",
    "rti": "pip install rti.connext, and RTI_LICENSE_FILE pointing at your own license",
}
# The vendor tag TopicForge can show for each vendor. Dust and RTI do not put
# their vendor id in the participant GUID, and the Cyclone Python binding does
# not expose the RTPS header that carries it, so they show as "unknown".
VENDOR_TAG = {"cyclone": "cyclone", "dust": "unknown", "rti": "unknown"}

SETTLE_S = 2.0  # endpoint announcements trail participant announcements a little
DISCOVERY_TIMEOUT_S = 30.0  # upper bound for every program to be seen


class Unavailable(RuntimeError):
    """A role program cannot run on this machine; the message says how to fix it."""


@dataclass(frozen=True)
class Node:
    """One DDS program playing a robot role.

    `writes` and `reads` use the endpoint syntax of `nodes/spec.py`:
    `TOPIC:TYPE[:option,...]`, for example `scan:LidarScan:best_effort`.
    """

    name: str
    vendor: str
    writes: tuple[str, ...] = ()
    reads: tuple[str, ...] = ()
    rate_hz: float = 10.0

    def missing(self) -> str | None:
        """Why this node cannot start here, or None when it can."""
        module = VENDOR_MODULE.get(self.vendor)
        if module is None:
            return f"unknown vendor {self.vendor!r}"
        if importlib.util.find_spec(module) is None:
            return f"{self.vendor} binding missing: {VENDOR_INSTALL[self.vendor]}"
        if self.vendor == "rti" and not os.environ.get("RTI_LICENSE_FILE"):
            return f"RTI needs a license: {VENDOR_INSTALL['rti']}"
        return None

    def argv(self, domain: int) -> list[str]:
        """Command line that starts this node with the current interpreter."""
        cmd = [sys.executable, str(NODES / f"{self.vendor}_node.py")]
        cmd += ["--domain", str(domain), "--name", self.name, "--rate-hz", str(self.rate_hz)]
        for endpoint in self.writes:
            cmd += ["--write", endpoint]
        for endpoint in self.reads:
            cmd += ["--read", endpoint]
        return cmd


# ------------------------------------------------------------------ the bus


def _spawn(argv: list[str], log: IO[bytes]) -> subprocess.Popen[bytes]:
    # stderr goes to a file, not a pipe: nobody drains a pipe while the
    # example runs, and a chatty program would block once it is full.
    kwargs: dict[str, Any] = {"stdout": subprocess.DEVNULL, "stderr": log}
    if os.name == "nt":
        # CREATE_NO_WINDOW: a console program started from here would
        # otherwise pop a window of its own.
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(argv, **kwargs)


def kill_tree(proc: subprocess.Popen[bytes]) -> None:
    """Kill a process and its children, the way a crash would.

    On Windows a venv's python.exe is a launcher that starts the real
    interpreter, so killing the launcher alone would leave an orphan on the bus.
    """
    if proc.poll() is None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=5)


class Bus:
    """The DDS programs of one example. Used as a context manager, it always
    stops everything it started, even on error or Ctrl+C."""

    def __init__(self, domain: int) -> None:
        self.domain = domain
        self._procs: dict[str, subprocess.Popen[bytes]] = {}
        self._logs: contextlib.ExitStack = contextlib.ExitStack()

    def start(self, *nodes: Node) -> None:
        """Start every node, or none of them if one cannot run here."""
        problems = [f"{n.name}: {why}" for n in nodes if (why := n.missing())]
        if problems:
            raise Unavailable("\n".join(problems))
        started: dict[str, IO[bytes]] = {}
        for node in nodes:
            log = self._logs.enter_context(tempfile.TemporaryFile())  # noqa: SIM115 (ExitStack owns it)
            self._procs[node.name] = _spawn(node.argv(self.domain), log)
            started[node.name] = log
            print(f"  started {node.name:<16} ({node.vendor})")
        time.sleep(1.0)
        for name, log in started.items():
            if self._procs[name].poll() is not None:
                log.seek(0)
                err = log.read().decode(errors="replace").strip()
                raise RuntimeError(f"{name} exited at startup: {err or 'no message'}")

    def crash(self, name: str) -> None:
        """Kill one node abruptly: no goodbye message reaches the bus."""
        kill_tree(self._procs.pop(name))
        print(f"  killed {name}")

    def close(self) -> None:
        for proc in self._procs.values():
            kill_tree(proc)
        self._procs.clear()
        self._logs.close()

    def __enter__(self) -> Bus:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# ----------------------------------------------------------- the MCP client


class TopicForge:
    """TopicForge started as an MCP server over stdio, questioned like an agent would."""

    def __init__(self, domain: int) -> None:
        self.domain = domain
        self._stack = contextlib.AsyncExitStack()
        self._session: Any = None

    async def __aenter__(self) -> TopicForge:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        env = {**os.environ, **server_env(self.domain), "TOPICFORGE_LOG_LEVEL": "WARNING"}
        params = StdioServerParameters(command=sys.executable, args=["-m", "topicforge"], env=env)
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._stack.aclose()

    async def ask(self, tool: str, **arguments: Any) -> Any:
        """Call one TopicForge tool and return its structured result."""
        result = await self._session.call_tool(tool, arguments)
        if result.isError:
            text = " ".join(getattr(c, "text", "") for c in result.content)
            raise RuntimeError(f"{tool} failed: {text}")
        data = result.structuredContent
        if data is not None:
            return data.get("result", data) if isinstance(data, dict) else data
        return [json.loads(c.text) for c in result.content if getattr(c, "text", None)]

    async def participants(self) -> list[dict[str, Any]]:
        return await self.ask("list_participants", domain_id=self.domain)

    async def mismatches(self) -> list[dict[str, Any]]:
        return await self.ask("detect_qos_mismatches")

    async def endpoints(self, builtin_topic: str) -> list[dict[str, Any]]:
        """Writers (`DCPSPublication`) or readers (`DCPSSubscription`) announced on the bus."""
        result = await self.ask("peek_dds_samples", topic=builtin_topic, count=200)
        return [s["payload"] for s in result.get("samples", [])]

    async def wiring(self) -> dict[str, dict[str, list[tuple[str, str]]]]:
        """Who writes and who reads each topic, as seen on the wire.

        Returns {topic: {"writers": [(participant, type)], "readers": [...]}}.
        """
        parts = await self.participants()
        table: dict[str, dict[str, list[tuple[str, str]]]] = {}
        for builtin, role in (("DCPSPublication", "writers"), ("DCPSSubscription", "readers")):
            for ep in await self.endpoints(builtin):
                topic = ep.get("topic_name")
                # DCPS* are the discovery topics themselves: TopicForge's own
                # readers, not part of the robot.
                if not topic or topic.startswith("DCPS"):
                    continue
                entry = table.setdefault(topic, {"writers": [], "readers": []})
                entry[role].append((owner(ep.get("guid"), parts), ep.get("type_name") or "?"))
        return table

    async def events(self, lookback_seconds: int = 600) -> list[dict[str, Any]]:
        return await self.ask(
            "participant_events", domain_id=self.domain, lookback_seconds=lookback_seconds
        )


def server_env(domain: int) -> dict[str, str]:
    """Environment that points TopicForge at a live Cyclone-observed domain."""
    return {
        "TOPICFORGE_MODE": "live",
        "TOPICFORGE_DDS_BACKEND": "cyclone",
        "TOPICFORGE_DDS_DOMAIN_ID": str(domain),
    }


# ---------------------------------------------------------- output, checks


def step(number: int, question: str, tool: str) -> None:
    """Announce one question the agent asks, and the tool that answers it."""
    print(f"\n[{number}] {question}\n    -> {tool}")


def show_participants(parts: Sequence[dict[str, Any]]) -> None:
    for p in parts:
        name = p.get("name") or "(no name)"
        host = p.get("hostname") or "?"
        print(
            f"    {name:<18} vendor={p.get('vendor', '?'):<8} {p.get('status', ''):<7} host={host}"
        )


def owner(guid: str | None, parts: Sequence[dict[str, Any]]) -> str:
    """Name of the participant that owns a reader or writer GUID.

    The first 12 bytes of an endpoint GUID are its participant's GUID prefix,
    so the two dotted forms share their first three groups.
    """
    if not guid:
        return "?"
    prefix = guid.rsplit(".", 1)[0]
    for p in parts:
        if str(p.get("guid", "")).rsplit(".", 1)[0] == prefix:
            return p.get("name") or p["guid"]
    return guid


def show_mismatches(mismatches: Sequence[dict[str, Any]], parts: Sequence[dict[str, Any]]) -> None:
    if not mismatches:
        print("    none")
    for m in mismatches:
        policies = ", ".join(m["incompatible_policies"])
        print(
            f"    {m['topic']}: {policies} ({m['severity']}): writer "
            f"{owner(m.get('writer_guid'), parts)} -> reader {owner(m.get('reader_guid'), parts)}"
        )


def mismatch_on(
    mismatches: Sequence[dict[str, Any]], topic: str, policy: str
) -> dict[str, Any] | None:
    """The reported mismatch on `topic` that involves `policy`, if any."""
    return next(
        (m for m in mismatches if m["topic"] == topic and policy in m["incompatible_policies"]),
        None,
    )


def show_wiring(table: dict[str, dict[str, list[tuple[str, str]]]]) -> None:
    for topic in sorted(table):
        entry = table[topic]

        def fmt(side: list[tuple[str, str]]) -> str:
            return ", ".join(f"{who} ({type_name})" for who, type_name in side) or "NOBODY"

        print(f"    {topic:<12} writers: {fmt(entry['writers'])}")
        print(f"    {'':<12} readers: {fmt(entry['readers'])}")


def show_events(events: Sequence[dict[str, Any]]) -> None:
    for e in events:
        name = e.get("name") or e["guid"]
        print(f"    {e['event_type']:<10} {name}")


def by_name(parts: Sequence[dict[str, Any]], name: str) -> dict[str, Any] | None:
    """The participant announcing `name`, if TopicForge reports one."""
    return next((p for p in parts if p.get("name") == name), None)


async def wait_for(
    probe: Callable[[], Awaitable[bool]], timeout_s: float, every_s: float = 2.0
) -> float | None:
    """Poll `probe` until it is true; return the elapsed seconds, or None on timeout."""
    started = time.monotonic()
    while time.monotonic() - started < timeout_s:
        if await probe():
            return time.monotonic() - started
        await asyncio.sleep(every_s)
    return None


class Checks:
    """What the example expects TopicForge to report. Each check prints one line."""

    def __init__(self) -> None:
        self.failures: list[str] = []

    def expect(self, condition: bool, what: str) -> bool:
        print(f"    {'ok  ' if condition else 'FAIL'} {what}")
        if not condition:
            self.failures.append(what)
        return condition

    def report(self) -> int:
        print()
        if self.failures:
            print(f"result: FAIL ({len(self.failures)} check(s) failed)")
            return 1
        print("result: PASS")
        return 0


# ------------------------------------------------------------- entry point

Scenario = Callable[[TopicForge, Bus, Checks], Awaitable[None]]


def run_example(
    title: str,
    nodes: Sequence[Node],
    scenario: Scenario,
    *,
    prompt: str,
    argv: Sequence[str] | None = None,
) -> int:
    """Start the nodes, run the scenario against TopicForge, stop everything.

    Exit code: 0 PASS, 1 FAIL, 2 cannot run on this machine.
    """
    parser = argparse.ArgumentParser(description=title)
    parser.add_argument("--domain", type=int, default=0, help="DDS domain id (default 0)")
    parser.add_argument(
        "--hold",
        action="store_true",
        help="start the programs and keep them running for your own MCP client",
    )
    args = parser.parse_args(argv)

    print(f"{title}\n\nstarting the robot programs on DDS domain {args.domain}")
    with Bus(args.domain) as bus:
        try:
            bus.start(*nodes)
        except Unavailable as exc:
            print(f"\ncannot run here:\n{exc}", file=sys.stderr)
            return 2
        if args.hold:
            return _hold(args.domain, prompt)
        checks = Checks()
        asyncio.run(_drive(args.domain, nodes, scenario, bus, checks))
        return checks.report()


async def _drive(
    domain: int, nodes: Sequence[Node], scenario: Scenario, bus: Bus, checks: Checks
) -> None:
    async with TopicForge(domain) as tf:
        # Poll until every program is visible rather than trusting a fixed
        # delay: a slow CI runner needs longer, a fast laptop much less.
        named = {n.name for n in nodes if n.vendor == "cyclone"}

        async def everyone_seen() -> bool:
            parts = await tf.participants()
            names = {p.get("name") for p in parts if p.get("status") == "active"}
            return len(parts) >= len(nodes) + 1 and named <= names

        if await wait_for(everyone_seen, DISCOVERY_TIMEOUT_S, every_s=1.0) is None:
            print(f"    warning: not every program was seen within {DISCOVERY_TIMEOUT_S:.0f} s")
        await asyncio.sleep(SETTLE_S)
        await scenario(tf, bus, checks)


def _hold(domain: int, prompt: str) -> int:
    env = server_env(domain)
    print("\nThe programs are running. Start TopicForge in your MCP client with:")
    for key, value in env.items():
        print(f"    {key}={value}")
    print(f"\nthen ask it:\n    {prompt}\n\nCtrl+C stops every program.")
    with contextlib.suppress(KeyboardInterrupt):
        while True:
            time.sleep(1)
    return 0
