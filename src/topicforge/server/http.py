"""Optional HTTP transport (Streamable HTTP), loopback by default.

stdio stays the default and the recommended way. This transport exists for the
cases where the MCP client and the robot cannot share a process tree: a client
on Windows talking to TopicForge inside WSL2, or an SSH tunnel to a robot
(`ssh -L 8765:127.0.0.1:8765 robot`).

Threat model. Without a token there is no authentication, so the server binds
`LOOPBACK_HOST` and only the machine it runs on can reach it; DNS-rebinding
protection is on, so a web page open in a local browser cannot reach it through
a hostile DNS name (the `Host` header must be a loopback name with the served
port, and a browser `Origin` must be a loopback origin).

With `TOPICFORGE_HTTP_TOKEN` set (at least `MIN_TOKEN_CHARS` characters, read
from the environment only, never from a flag that `ps` would show), every
request must carry `Authorization: Bearer <token>`, compared in constant time,
and only then may `--host` name another address. The token then guards the
endpoint instead of the bind address, so the DNS-rebinding allow-list is lifted
for a non-loopback bind (a rebound page does not know the token). The transport
is plain HTTP: the token and the answers cross the network in clear. An SSH
tunnel, or a TLS reverse proxy in front, is still the safer way to reach a robot.
"""

from __future__ import annotations

import hmac
import logging
from typing import Any

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

log = logging.getLogger(__name__)

# The default bind address; any other needs a token (see the module docstring).
LOOPBACK_HOST = "127.0.0.1"
LOOPBACK_NAMES = frozenset({LOOPBACK_HOST, "localhost", "::1"})
DEFAULT_HTTP_PORT = 8765
HTTP_PATH = "/mcp"
TOKEN_ENV = "TOPICFORGE_HTTP_TOKEN"
MIN_TOKEN_CHARS = 32


def is_loopback(host: str) -> bool:
    """True for the names and addresses that only the local machine can reach."""
    return host in LOOPBACK_NAMES


def validate_token(token: str) -> str:
    """`token` when usable as a bearer token, else a `ValueError` saying why.

    At least `MIN_TOKEN_CHARS` printable ASCII characters and no space, so a guess is
    out of reach and the header cannot be split (e.g. `openssl rand -hex 32`).
    """
    if len(token) < MIN_TOKEN_CHARS:
        raise ValueError(f"{TOKEN_ENV} must be at least {MIN_TOKEN_CHARS} characters")
    if not all(33 <= ord(c) <= 126 for c in token):
        raise ValueError(f"{TOKEN_ENV} must be printable ASCII without spaces")
    return token


class BearerTokenMiddleware:
    """ASGI middleware: refuse every HTTP request without the expected bearer token (401)."""

    def __init__(self, app: Any, token: str) -> None:
        self._app = app
        self._expected = f"Bearer {validate_token(token)}".encode("ascii")

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and not self._authorized(scope):
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"text/plain; charset=utf-8"),
                        (b"www-authenticate", b"Bearer"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": b"bearer token required\n"})
            return
        await self._app(scope, receive, send)

    def _authorized(self, scope: Any) -> bool:
        values = [v for k, v in scope.get("headers", []) if k == b"authorization"]
        # One header exactly: a second one could be read differently by a proxy in front.
        return len(values) == 1 and hmac.compare_digest(values[0], self._expected)


def transport_security(port: int, host: str = LOOPBACK_HOST) -> TransportSecuritySettings:
    """DNS-rebinding protection: only loopback `Host` and `Origin` values for `port`.

    For a non-loopback `host` (only reachable with a token) the allow-list is lifted:
    the token, not the `Host` header, is what keeps a foreign page out.
    """
    if not is_loopback(host):
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    names = (LOOPBACK_HOST, "localhost", "[::1]")
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[f"{name}:{port}" for name in names],
        allowed_origins=[f"http://{name}:{port}" for name in names],
    )


def build_http_server(
    app: MCPServer, port: int, host: str = LOOPBACK_HOST, token: str | None = None
) -> uvicorn.Server:
    """A uvicorn server for `app` bound to `host:port`; `port` must be the real one.

    The DNS-rebinding allow-list names the port, so port 0 would not work. A
    non-loopback `host` without a `token` raises `ValueError`.
    """
    if token is None and not is_loopback(host):
        raise ValueError(f"binding {host} needs {TOKEN_ENV}: without it, only loopback is allowed")
    asgi: Any = app.streamable_http_app(
        streamable_http_path=HTTP_PATH,
        host=host,
        transport_security=transport_security(port, host),
    )
    if token is not None:
        asgi = BearerTokenMiddleware(asgi, token)
    config = uvicorn.Config(asgi, host=host, port=port, log_level="warning")
    return uvicorn.Server(config)


def serve_http(
    app: MCPServer, port: int, host: str = LOOPBACK_HOST, token: str | None = None
) -> None:
    """Serve `app` over Streamable HTTP on `http://<host>:<port>/mcp` until interrupted."""
    if not 1 <= port <= 65535:
        raise ValueError(f"port must be in 1..65535, got {port}")
    server = build_http_server(app, port, host, token)
    reach = "loopback only" if is_loopback(host) else "reachable from the network"
    guard = "bearer token required" if token is not None else "no token"
    log.info(
        "serving MCP over HTTP on http://%s:%d%s (%s, %s)", host, port, HTTP_PATH, reach, guard
    )
    if not is_loopback(host):
        log.warning(
            "plain HTTP on %s: the token and every answer cross the network in clear; "
            "prefer an SSH tunnel or a TLS reverse proxy",
            host,
        )
    server.run()


__all__ = [
    "DEFAULT_HTTP_PORT",
    "HTTP_PATH",
    "LOOPBACK_HOST",
    "MIN_TOKEN_CHARS",
    "TOKEN_ENV",
    "BearerTokenMiddleware",
    "build_http_server",
    "is_loopback",
    "serve_http",
    "transport_security",
    "validate_token",
]
