"""Keep one TopicForge MCP server alive and relay tool calls to it over TCP.

    python broker.py --domain 62 --port 8762

One JSON request per line: {"tool": "...", "args": {...}} or {"list": true}.
"""

import argparse
import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", type=int, required=True)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--host", default="127.0.0.1", help="0.0.0.0 inside a container")
    a = ap.parse_args()
    env = {
        **os.environ,
        "TOPICFORGE_MODE": "live",
        "TOPICFORGE_DDS_BACKEND": "cyclone",
        "TOPICFORGE_DDS_DOMAIN_ID": str(a.domain),
        "TOPICFORGE_LOG_LEVEL": "WARNING",
    }
    params = StdioServerParameters(command=sys.executable, args=["-m", "topicforge"], env=env)
    async with stdio_client(params) as (r, w), ClientSession(r, w) as session:
        await session.initialize()
        lock = asyncio.Lock()

        async def handle(reader, writer):
            line = await reader.readline()
            req = json.loads(line)
            async with lock:
                if req.get("list"):
                    tools = await session.list_tools()
                    out = [
                        {"name": t.name, "description": t.description, "inputSchema": t.inputSchema}
                        for t in tools.tools
                    ]
                else:
                    res = await session.call_tool(req["tool"], req.get("args") or {})
                    if res.isError:
                        out = {
                            "isError": True,
                            "content": [getattr(c, "text", "") for c in res.content],
                        }
                    else:
                        out = (
                            res.structuredContent
                            if res.structuredContent is not None
                            else [getattr(c, "text", "") for c in res.content]
                        )
            writer.write((json.dumps(out, default=str) + "\n").encode())
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(handle, a.host, a.port)
        print(f"broker ready domain={a.domain} port={a.port}", flush=True)
        async with server:
            await server.serve_forever()


asyncio.run(main())
