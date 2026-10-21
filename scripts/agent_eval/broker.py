"""Keep one TopicForge MCP server alive and relay tool calls to it over TCP.

    python broker.py --domain 62 --port 8762

One JSON request per line: {"tool": "...", "args": {...}}, {"list": true} or, for the
runner only, {"prompt": "<name>", "args": {...}}.

The server it starts is `python -m topicforge` of the interpreter running the broker, in
live mode with the Cyclone DDS backend. Other setups:

    # inside the ROS 2 bench image, reachable through a published port
    python broker.py --domain 0 --port 8801 --host 0.0.0.0 \\
        --server-cmd /opt/tfenv/bin/topicforge

    # fixtures only, no ROS 2 and no DDS (what `tf.py list --brief` is measured on)
    python broker.py --domain 0 --port 8801 --mode mock --dds-backend mock

The listener binds to 127.0.0.1 unless `--host` says otherwise: the relay has no
authentication, so `0.0.0.0` belongs inside a container whose published port is itself
bound to loopback (`docker run -p 127.0.0.1:8801:8801 ...`).
`--calls-log FILE` appends one JSON line per request (time, tool, size of the answer),
which is how the runner counts an agent's tool calls without trusting the agent.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

LOOPBACK = "127.0.0.1"


def parse_server_cmd(text: str | None) -> list[str]:
    """The command that starts the TopicForge server: `text` split like a shell, or the default."""
    if text is None:
        return [sys.executable, "-m", "topicforge"]
    parts = shlex.split(text, posix=os.name != "nt")
    parts = [p[1:-1] if len(p) > 1 and p[0] == p[-1] and p[0] in "\"'" else p for p in parts]
    if not parts:
        raise ValueError("--server-cmd is empty")
    return parts


def parse_env_pairs(pairs: Sequence[str]) -> dict[str, str]:
    """`KEY=VALUE` strings as a dict; a string without `=` is an error."""
    out: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise ValueError(f"--env wants KEY=VALUE, got {pair!r}")
        out[key] = value
    return out


def build_server_env(
    base: Mapping[str, str],
    *,
    domain: int,
    mode: str = "live",
    dds_backend: str = "cyclone",
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """The environment of the server process (`extra` wins over everything else)."""
    env = {
        **base,
        "TOPICFORGE_MODE": mode,
        "TOPICFORGE_DDS_BACKEND": dds_backend,
        "TOPICFORGE_DDS_DOMAIN_ID": str(domain),
        "TOPICFORGE_LOG_LEVEL": "WARNING",
    }
    env.update(extra or {})
    return env


def build_parser() -> argparse.ArgumentParser:
    """The command line of the broker."""
    ap = argparse.ArgumentParser(description="Relay tool calls to one persistent TopicForge.")
    ap.add_argument("--domain", type=int, required=True, help="DDS domain the server joins")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument(
        "--host",
        default=LOOPBACK,
        help="listen address, loopback by default; 0.0.0.0 in a container",
    )
    ap.add_argument(
        "--server-cmd",
        default=None,
        help="command that starts the server (default: this Python with -m topicforge)",
    )
    ap.add_argument("--mode", default="live", choices=("live", "mock", "auto"))
    ap.add_argument("--dds-backend", default="cyclone", help="mock, cyclone, fast or auto")
    ap.add_argument("--env", action="append", default=[], metavar="KEY=VALUE")
    ap.add_argument("--calls-log", type=Path, default=None, help="append one JSON line per request")
    return ap


def log_call(path: Path | None, req: Mapping[str, Any], answer: Any) -> None:
    """Append one line about a request to `path` (no-op when `path` is None)."""
    if path is None:
        return
    kind = "list" if req.get("list") else "prompt" if "prompt" in req else "tool"
    entry = {
        "t": round(time.time(), 3),
        "kind": kind,
        "tool": req.get("tool") or req.get("prompt"),
        "args": req.get("args") or {},
        "bytes": len(json.dumps(answer, default=str)),
        "is_error": isinstance(answer, dict) and bool(answer.get("isError")),
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


async def answer_request(session: Any, req: Mapping[str, Any]) -> Any:
    """Run one request on the MCP session and return the JSON-able answer."""
    if req.get("list"):
        tools = await session.list_tools()
        return [
            {"name": t.name, "description": t.description, "inputSchema": t.input_schema}
            for t in tools.tools
        ]
    if "prompt" in req:
        prompt = await session.get_prompt(req["prompt"], req.get("args") or {})
        return {
            "text": "\n".join(
                getattr(m.content, "text", "") for m in prompt.messages if m.content is not None
            )
        }
    res = await session.call_tool(req["tool"], req.get("args") or {})
    if res.is_error:
        return {"isError": True, "content": [getattr(c, "text", "") for c in res.content]}
    if res.structured_content is not None:
        return res.structured_content
    return [getattr(c, "text", "") for c in res.content]


async def main(argv: Sequence[str] | None = None) -> None:
    from mcp import Client, StdioServerParameters

    a = build_parser().parse_args(argv)
    cmd = parse_server_cmd(a.server_cmd)
    env = build_server_env(
        os.environ,
        domain=a.domain,
        mode=a.mode,
        dds_backend=a.dds_backend,
        extra=parse_env_pairs(a.env),
    )
    params = StdioServerParameters(command=cmd[0], args=cmd[1:], env=env)
    async with Client(params) as session:
        lock = asyncio.Lock()

        async def handle(reader, writer):
            req = json.loads(await reader.readline())
            async with lock:
                out = await answer_request(session, req)
            log_call(a.calls_log, req, out)
            writer.write((json.dumps(out, default=str) + "\n").encode())
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(handle, a.host, a.port)
        print(f"broker ready domain={a.domain} host={a.host} port={a.port}", flush=True)
        async with server:
            await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
