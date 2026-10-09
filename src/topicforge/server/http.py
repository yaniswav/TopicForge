"""Optional local HTTP transport (Streamable HTTP), loopback only.

stdio stays the default and the recommended way. This transport exists for the
cases where the MCP client and the robot cannot share a process tree: a client
on Windows talking to TopicForge inside WSL2, or an SSH tunnel to a robot
(`ssh -L 8765:127.0.0.1:8765 robot`).

Threat model: there is no authentication, so the server must only be reachable
from the machine it runs on. The bind address is the constant `LOOPBACK_HOST`
and no flag changes it; DNS-rebinding protection is on, so a web page open in a
local browser cannot reach it through a hostile DNS name (the `Host` header
must be a loopback name with the served port, and a browser `Origin` must be a
loopback origin). Anything that needs the network belongs behind a tunnel.
"""

from __future__ import annotations

import logging
from typing import Any

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

log = logging.getLogger(__name__)

# The only address the HTTP transport binds. Deliberately not configurable.
LOOPBACK_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8765
HTTP_PATH = "/mcp"


def transport_security(port: int) -> TransportSecuritySettings:
    """DNS-rebinding protection: only loopback `Host` and `Origin` values for `port`."""
    names = (LOOPBACK_HOST, "localhost", "[::1]")
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[f"{name}:{port}" for name in names],
        allowed_origins=[f"http://{name}:{port}" for name in names],
    )


def build_http_server(app: MCPServer, port: int) -> uvicorn.Server:
    """A uvicorn server for `app` bound to `LOOPBACK_HOST:port`; `port` must be the real one.

    The DNS-rebinding allow-list names the port, so port 0 would not work.
    """
    asgi: Any = app.streamable_http_app(
        streamable_http_path=HTTP_PATH,
        host=LOOPBACK_HOST,
        transport_security=transport_security(port),
    )
    config = uvicorn.Config(asgi, host=LOOPBACK_HOST, port=port, log_level="warning")
    return uvicorn.Server(config)


def serve_http(app: MCPServer, port: int) -> None:
    """Serve `app` over Streamable HTTP on `http://127.0.0.1:<port>/mcp` until interrupted."""
    if not 1 <= port <= 65535:
        raise ValueError(f"port must be in 1..65535, got {port}")
    server = build_http_server(app, port)
    log.info(
        "serving MCP over HTTP on http://%s:%d%s (loopback only)", LOOPBACK_HOST, port, HTTP_PATH
    )
    server.run()


__all__ = [
    "DEFAULT_HTTP_PORT",
    "HTTP_PATH",
    "LOOPBACK_HOST",
    "build_http_server",
    "serve_http",
    "transport_security",
]
