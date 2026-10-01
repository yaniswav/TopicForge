"""TopicForge multi-vendor demo: drive the MCP server exactly as an agent would.

One command, Windows or Linux:

    python scripts/integration/driver/demo_client.py

What it does:
  1. starts the demo participants (Rust / Dust and Python / Cyclone);
  2. starts TopicForge as a real MCP server over stdio and talks to it through
     the official MCP client, the same path Claude Desktop or Claude Code uses;
  3. runs three scenarios: who is on the bus, which reader/writer pairs can
     never talk, and what happens when a participant stops;
  4. stops every process it started, even on error or Ctrl+C.

Prerequisites: the Cyclone binding (`pip install "topicforge[dds]"`) and the
Rust participant built once with `cargo build --release` in
`scripts/integration/publishers/dust_publisher`.

TopicForge never publishes: it only reads the DDS discovery topics.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO = Path(__file__).resolve().parents[3]
PUBLISHERS = REPO / "scripts" / "integration" / "publishers"
DUST_EXE = (
    PUBLISHERS
    / "dust_publisher"
    / "target"
    / "release"
    / ("dust_publisher.exe" if os.name == "nt" else "dust_publisher")
)
CYCLONE_NODE = PUBLISHERS / "cyclone_publisher.py"
DOMAIN = os.environ.get("TOPICFORGE_DDS_DOMAIN_ID", "0")
LEASE_WAIT_S = 30  # Cyclone's default lease is 10 s; leave margin for discovery


# --------------------------------------------------------------- processes


def _start(name: str, argv: list[str]) -> subprocess.Popen[bytes]:
    kwargs: dict[str, Any] = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(argv, **kwargs)
    print(f"  started {name:<16} pid {proc.pid}")
    return proc


def _stop_tree(proc: subprocess.Popen[bytes]) -> None:
    """Stop a process and its children.

    On Windows a venv's python.exe is a launcher that spawns the real
    interpreter, so killing only the launcher leaves an orphan on the bus.
    """
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=5)


# ---------------------------------------------------------------- MCP calls


async def _call(session: ClientSession, tool: str, **args: Any) -> Any:
    result = await session.call_tool(tool, args)
    if result.isError:
        text = " ".join(getattr(c, "text", "") for c in result.content)
        raise RuntimeError(f"{tool} failed: {text}")
    if result.structuredContent is not None:
        data = result.structuredContent
        return data.get("result", data) if isinstance(data, dict) else data
    return [json.loads(c.text) for c in result.content if getattr(c, "text", None)]


def _participants_table(parts: list[dict[str, Any]]) -> None:
    for p in parts:
        print(f"    {p.get('vendor', '?'):<10} {p.get('status', ''):<8} {p.get('guid')}")


async def _scenario(cyclone_node: subprocess.Popen[bytes]) -> int:
    env = {
        **os.environ,
        "TOPICFORGE_MODE": "live",
        "TOPICFORGE_DDS_BACKEND": "cyclone",
        "TOPICFORGE_DDS_DOMAIN_ID": DOMAIN,
        "TOPICFORGE_LOG_LEVEL": "WARNING",
    }
    server = StdioServerParameters(command=sys.executable, args=["-m", "topicforge"], env=env)
    failures = 0
    async with stdio_client(server) as (read, write), ClientSession(read, write) as session:
        await session.initialize()

        health = await _call(session, "health_check")
        print(
            f"\n[health_check] mode={health.get('mode')} "
            f"dds_backend={health.get('dds_backend')} version={health.get('server_version')}"
        )

        print("\n[1] list_participants: who is on the bus?")
        parts = await _call(session, "list_participants", domain_id=int(DOMAIN))
        _participants_table(parts)
        if len(parts) < 3:
            print("    EXPECTED at least 3 (TopicForge, Python node, Rust node)")
            failures += 1

        print("\n[2] detect_qos_mismatches: which reader/writer pairs can never talk?")
        mismatches = await _call(session, "detect_qos_mismatches")
        for m in mismatches:
            print(
                f"    {m['topic']}: {', '.join(m['incompatible_policies'])} "
                f"({m['severity']}) reader {m['reader_guid']} <- writer {m['writer_guid']}"
            )
        if not any(
            m["topic"] == "DemoLidarScan" and "Reliability" in m["incompatible_policies"]
            for m in mismatches
        ):
            print("    EXPECTED a Reliability mismatch on DemoLidarScan")
            failures += 1

        print("\n[3] stopping the Python / Cyclone node, then watching the bus")
        _stop_tree(cyclone_node)
        started = time.monotonic()
        left = False
        while time.monotonic() - started < LEASE_WAIT_S:
            await asyncio.sleep(3)
            parts = await _call(session, "list_participants", domain_id=int(DOMAIN))
            if any(p.get("status") == "left" for p in parts):
                left = True
                print(f"    detected after {time.monotonic() - started:.0f} s:")
                _participants_table(parts)
                break
        if not left:
            print(f"    EXPECTED a participant to leave within {LEASE_WAIT_S} s")
            failures += 1

        events = await _call(
            session, "participant_events", domain_id=int(DOMAIN), lookback_seconds=600
        )
        print("\n[4] participant_events: the timeline an agent would read")
        for e in events:
            print(f"    {e['event_type']:<10} {e.get('vendor', '?'):<10} {e['guid']}")

    print(f"\nresult: {'PASS' if failures == 0 else f'{failures} check(s) failed'}")
    return failures


def main() -> int:
    if not DUST_EXE.exists():
        print(f"error: build the Rust participant first: {DUST_EXE.parent.parent}", file=sys.stderr)
        print("       cargo build --release", file=sys.stderr)
        return 2
    print("starting demo participants")
    procs = [
        _start("rust/dust", [str(DUST_EXE), "--domain", DOMAIN]),
        _start("python/cyclone", [sys.executable, str(CYCLONE_NODE), "--domain", DOMAIN]),
    ]
    try:
        time.sleep(5)  # let SPDP announcements cross the bus
        return 1 if asyncio.run(_scenario(procs[1])) else 0
    finally:
        for proc in procs:
            _stop_tree(proc)
        print("all demo processes stopped")


if __name__ == "__main__":
    raise SystemExit(main())
