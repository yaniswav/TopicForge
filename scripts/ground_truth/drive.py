#!/usr/bin/env python3
"""Drive TopicForge through its real MCP stdio interface and save every raw tool result.

    python drive.py --out RESULTS_DIR [--server-cmd "topicforge"] [--bag BAG_DIR]
                    [--calls calls.json ...] [--mode live] [--dds-backend cyclone]
                    [--domain 0] [--env KEY=VALUE ...]

Needs the `mcp` package (a TopicForge dependency): run it with the Python of the
environment TopicForge is installed in, or pass `--venv DIR` and it re-executes itself with
that venv's interpreter (and starts `DIR/bin/topicforge`).

A calls file is a JSON list. Each entry is one of:

    {"list_tools": true}
    {"name": "02_list_topics", "tool": "list_topics", "args": {}, "timeout": 120}
    {"name": "03_topic_info_{slug}", "tool": "get_topic_info", "for_each": "topics",
     "args": {"topic": "{item}"}}

`for_each` is `topics` (names from the `02_list_topics` result) or `nodes` (full names from
`02n_list_nodes`); `{item}` and `{slug}` (the item without its leading slash, `/` -> `_`) are
replaced in the name and the arguments. `min_publishers` and `exclude` filter the topics.
`needs_bag` entries are skipped without `--bag` (and `{BAG}` is replaced by it);
`requires_bag_topic` skips the entry when the `13_analyze_bag` result does not list that topic.
Entries run in order inside ONE MCP session, so put graph calls before sampling calls.

Each result goes to `<out>/<name>.json` as `{isError, content, parsed, structuredContent?,
_call: {tool, args, wall_s, t_wall}}` (`{exception, _call}` when the call raised or timed
out). `_initialize.json` holds the server's identity and `topicforge_stderr.log` the server's
stderr (appended per session). Exit code 0 when the session ran, 2 when it could not start.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def slug(item: str) -> str:
    """File-name-safe form of a topic or node name."""
    return item.strip("/").replace("/", "_") or "root"


def tf_version(command: list[str]) -> str:
    """TopicForge version of the interpreter behind the server command, else `unknown`."""
    try:
        py = (
            command[0] if command[0].lower().endswith(("python", "python3", "python.exe")) else None
        )
        if py is None:
            probe = Path(command[0]).resolve().parent
            py = next(
                (str(p) for p in (probe / "python", probe / "python.exe") if p.exists()), None
            )
        py = py or sys.executable
        code = "import importlib.metadata as m;print(m.version('topicforge'))"
        return subprocess.check_output([py, "-c", code], text=True, timeout=30).strip()
    except Exception:
        return "unknown"


def flat(result: Any) -> list[Any]:
    """Parsed JSON objects of a saved result; items of a top-level list are flattened."""
    out: list[Any] = []
    for part in (result or {}).get("parsed") or []:
        if isinstance(part, list):
            out += part
        elif part is not None:
            out.append(part)
    return out


def names_from(result: dict[str, Any] | None, key: str, name_key: str) -> list[dict[str, Any]]:
    """Entries of a listing: the `key` array of a 0.7 envelope, else the flattened 0.6.x list."""
    items = flat(result)
    if len(items) == 1 and isinstance(items[0], dict) and isinstance(items[0].get(key), list):
        return items[0][key]
    return [i for i in items if isinstance(i, dict) and name_key in i]


def substitute(obj: Any, mapping: dict[str, str]) -> Any:
    """Replace `{key}` placeholders in every string of a JSON value."""
    if isinstance(obj, str):
        for key, value in mapping.items():
            obj = obj.replace("{" + key + "}", value)
        return obj
    if isinstance(obj, list):
        return [substitute(x, mapping) for x in obj]
    if isinstance(obj, dict):
        return {k: substitute(v, mapping) for k, v in obj.items()}
    return obj


def expand(call: dict[str, Any], saved: dict[str, dict[str, Any]], bag: str | None) -> list[dict]:
    """Expand one calls-file entry into zero or more concrete calls."""
    if call.get("needs_bag") and not bag:
        return []
    topic = call.get("requires_bag_topic")
    if topic:
        bag_topics = {
            t.get("name") for o in flat(saved.get("13_analyze_bag")) for t in o.get("topics", [])
        }
        if topic not in bag_topics:
            return []
    source = call.get("for_each")
    if not source:
        return [substitute(call, {"BAG": bag or ""})]
    if source == "topics":
        entries = names_from(saved.get("02_list_topics"), "topics", "name")
        items = [
            e["name"]
            for e in entries
            if e.get("publisher_count", 0) >= call.get("min_publishers", 0)
            and e["name"] not in call.get("exclude", [])
        ]
    elif source == "nodes":
        entries = names_from(saved.get("02n_list_nodes"), "nodes", "full_name")
        items = [e["full_name"] for e in entries if e["full_name"] not in call.get("exclude", [])]
    else:
        raise SystemExit(f"unknown for_each source: {source}")
    out = []
    for item in items:
        concrete = substitute(call, {"item": item, "slug": slug(item), "BAG": bag or ""})
        concrete.pop("for_each", None)
        out.append(concrete)
    return out


def dump(result: Any) -> dict[str, Any]:
    """Plain JSON form of an MCP tool result (mcp 1.x and 2.x attribute names)."""

    def attr(*names: str) -> Any:
        for name in names:
            if hasattr(result, name):
                return getattr(result, name)
        return None

    content = [getattr(c, "text", repr(c)) for c in attr("content") or []]
    out: dict[str, Any] = {"isError": bool(attr("is_error", "isError")), "content": content}
    structured = attr("structured_content", "structuredContent")
    if structured is not None:
        out["structuredContent"] = structured
    parsed = []
    for text in content:
        try:
            parsed.append(json.loads(text))
        except ValueError:
            parsed.append(None)
    if not any(p is not None for p in parsed) and isinstance(structured, dict):
        parsed = [structured]
    out["parsed"] = parsed
    return out


def open_session(params: Any, errlog: Any) -> Any:
    """Async context manager yielding an initialized client session, on mcp 1.x or 2.x."""
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    class _Session:
        async def __aenter__(self) -> Any:
            self._tp = stdio_client(params, errlog=errlog)
            read, write = await self._tp.__aenter__()
            self._cs = ClientSession(read, write)
            await self._cs.__aenter__()
            self.init = await self._cs.initialize()
            return self

        async def __aexit__(self, *exc: Any) -> None:
            await self._cs.__aexit__(*exc)
            await self._tp.__aexit__(*exc)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._cs, name)

    return _Session()


async def run(args: argparse.Namespace, calls: list[dict], out: Path, errlog: Any) -> None:
    """Run every call in one MCP session and save the results."""
    from mcp import StdioServerParameters

    env = dict(os.environ)
    env.update(
        {
            "TOPICFORGE_MODE": args.mode,
            "TOPICFORGE_DDS_BACKEND": args.dds_backend,
            "TOPICFORGE_DDS_DOMAIN_ID": str(args.domain),
        }
    )
    env.update(dict(kv.split("=", 1) for kv in args.env))
    command = args.server_cmd_list
    params = StdioServerParameters(command=command[0], args=command[1:], env=env)
    saved: dict[str, dict[str, Any]] = {}

    def save(name: str, obj: Any) -> None:
        (out / f"{name}.json").write_text(json.dumps(obj, indent=1, default=str), encoding="utf-8")

    async with open_session(params, errlog) as session:
        info = session.init.server_info if hasattr(session.init, "server_info") else None
        info = info or getattr(session.init, "serverInfo", None)
        save(
            "_initialize",
            {
                "serverInfo": info.model_dump() if info is not None else None,
                "topicforge_version": (getattr(info, "version", None) or tf_version(command)),
                "server_command": command,
            },
        )
        for entry in calls:
            for call in expand(entry, saved, args.bag) if not entry.get("list_tools") else [entry]:
                if call.get("list_tools"):
                    listed = await session.list_tools()
                    save("_tools", [t.model_dump() for t in listed.tools])
                    print("tools:", [t.name for t in listed.tools], flush=True)
                    continue
                t0 = time.time()
                try:
                    res = await asyncio.wait_for(
                        session.call_tool(call["tool"], call.get("args", {})),
                        call.get("timeout", 120),
                    )
                    data = dump(res)
                except Exception as exc:
                    data = {"exception": repr(exc)}
                data["_call"] = {
                    "tool": call["tool"],
                    "args": call.get("args", {}),
                    "wall_s": round(time.time() - t0, 2),
                    "t_wall": time.time(),
                }
                saved[call["name"]] = data
                save(call["name"], data)
                bad = data.get("isError") or "exception" in data
                print(call["name"], "ERR" if bad else "ok", data["_call"]["wall_s"], flush=True)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--out", required=True, help="results directory (created)")
    ap.add_argument("--server-cmd", default=None, help="command that starts TopicForge on stdio")
    ap.add_argument("--venv", default=None, help="venv with topicforge+mcp (POSIX layout)")
    ap.add_argument("--bag", default=None, help="bag directory as the server sees it ({BAG})")
    ap.add_argument("--calls", nargs="+", default=[str(HERE / "calls.json")])
    ap.add_argument("--mode", default="live", help="TOPICFORGE_MODE (default live)")
    ap.add_argument("--dds-backend", default="cyclone", help="TOPICFORGE_DDS_BACKEND")
    ap.add_argument("--domain", type=int, default=0, help="TOPICFORGE_DDS_DOMAIN_ID")
    ap.add_argument("--env", action="append", default=[], help="extra KEY=VALUE for the server")
    args = ap.parse_args(argv)
    if args.venv:
        args.venv = os.path.abspath(os.path.expanduser(args.venv))
        if args.server_cmd is None:
            args.server_cmd = os.path.join(args.venv, "bin", "topicforge")
    if args.server_cmd:
        tokens = shlex.split(args.server_cmd, posix=os.name != "nt")
        args.server_cmd_list = [t[1:-1] if t[:1] == t[-1:] == '"' else t for t in tokens]
    else:
        found = shutil.which("topicforge")
        args.server_cmd_list = [found] if found else [sys.executable, "-m", "topicforge"]
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.venv and os.path.realpath(sys.prefix) != os.path.realpath(args.venv):
        py = os.path.join(args.venv, "bin", "python")
        os.execv(py, [py, os.path.abspath(__file__), *(argv if argv is not None else sys.argv[1:])])
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    calls: list[dict] = []
    for name in args.calls:
        calls += json.loads(Path(name).read_text(encoding="utf-8"))
    with open(out / "topicforge_stderr.log", "a", encoding="utf-8") as errlog:
        stamp = datetime.datetime.now().isoformat()
        errlog.write(f"# --- session {stamp} cmd={args.server_cmd_list} calls={args.calls}\n")
        errlog.flush()
        try:
            asyncio.run(run(args, calls, out, errlog))
        except Exception as exc:
            print(f"drive: session failed: {exc!r}", file=sys.stderr)
            return 2
    print("results in", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
