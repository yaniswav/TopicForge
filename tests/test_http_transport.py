"""The optional HTTP transport: loopback bind, DNS-rebinding protection, token, CLI rules."""

from __future__ import annotations

import asyncio
import http.client
import socket
import threading
import time
from collections.abc import Iterator

import pytest
from mcp import Client

from topicforge.__main__ import _build_arg_parser, main
from topicforge.config import Settings
from topicforge.server import build_app
from topicforge.server.http import (
    HTTP_PATH,
    LOOPBACK_HOST,
    TOKEN_ENV,
    build_http_server,
    transport_security,
    validate_token,
)

_SETTINGS = Settings(mode="mock", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind((LOOPBACK_HOST, 0))
        return int(sock.getsockname()[1])


_TOKEN = "t" * 16 + "0123456789abcdef"


def _serve(token: str | None) -> Iterator[tuple[int, object]]:
    port = _free_port()
    server = build_http_server(build_app(_SETTINGS), port, token=token)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started, "the HTTP server did not start"
    try:
        yield port, server
    finally:
        server.should_exit = True
        thread.join(15)


@pytest.fixture
def served() -> Iterator[tuple[int, object]]:
    yield from _serve(None)


@pytest.fixture
def served_with_token() -> Iterator[tuple[int, object]]:
    yield from _serve(_TOKEN)


def test_lists_the_tools_over_http_and_binds_to_loopback_only(
    served: tuple[int, object],
) -> None:
    port, server = served
    bound = {sock.getsockname()[0] for srv in server.servers for sock in srv.sockets}  # type: ignore[attr-defined]
    assert bound == {LOOPBACK_HOST}

    async def run() -> list[str]:
        async with Client(f"http://{LOOPBACK_HOST}:{port}{HTTP_PATH}") as client:
            return [tool.name for tool in (await client.list_tools()).tools]

    names = asyncio.run(run())
    assert len(names) == 16 and "health_check" in names


def _post(port: int, host_header: str, authorization: str | None = None) -> int:
    conn = http.client.HTTPConnection(LOOPBACK_HOST, port, timeout=10)
    headers = {
        "Host": host_header,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if authorization is not None:
        headers["Authorization"] = authorization
    try:
        conn.request("POST", HTTP_PATH, body="{}", headers=headers)
        return conn.getresponse().status
    finally:
        conn.close()


def test_a_foreign_host_header_is_refused(served: tuple[int, object]) -> None:
    """DNS rebinding: a browser tricked into resolving evil.example to 127.0.0.1."""
    port, _ = served
    assert _post(port, f"evil.example:{port}") == 421


def test_the_allow_list_names_only_loopback() -> None:
    settings = transport_security(8765)
    assert settings.enable_dns_rebinding_protection is True
    assert all(
        h.rsplit(":", 1)[0] in (LOOPBACK_HOST, "localhost", "[::1]") for h in settings.allowed_hosts
    )
    assert all("://" in o and o.endswith(":8765") for o in settings.allowed_origins)


def test_another_host_without_a_token_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    with pytest.raises(SystemExit):
        main(["--transport", "streamable-http", "--host", "0.0.0.0"])
    with pytest.raises(ValueError, match=TOKEN_ENV):
        build_http_server(build_app(_SETTINGS), 8765, host="0.0.0.0")


def test_a_short_token_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, "short")
    with pytest.raises(SystemExit):
        main(["--transport", "streamable-http"])
    with pytest.raises(ValueError, match="32"):
        validate_token("x" * 31)
    with pytest.raises(ValueError, match="printable"):
        validate_token("x" * 31 + " ")


def test_host_without_the_http_transport_is_refused() -> None:
    with pytest.raises(SystemExit):
        main(["--host", "127.0.0.1"])


def test_requests_without_the_right_token_get_401(served_with_token: tuple[int, object]) -> None:
    port, _ = served_with_token
    host = f"{LOOPBACK_HOST}:{port}"
    assert _post(port, host) == 401
    assert _post(port, host, "Bearer " + "x" * 32) == 401
    assert _post(port, host, _TOKEN) == 401  # the scheme is required
    assert _post(port, host, "Bearer " + _TOKEN) != 401


def test_an_mcp_client_with_the_token_lists_the_tools(
    served_with_token: tuple[int, object],
) -> None:
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

    port, _ = served_with_token

    async def run() -> list[str]:
        http = create_mcp_http_client(headers={"Authorization": f"Bearer {_TOKEN}"})
        url = f"http://{LOOPBACK_HOST}:{port}{HTTP_PATH}"
        async with http, Client(streamable_http_client(url, http_client=http)) as client:
            return [tool.name for tool in (await client.list_tools()).tools]

    assert len(asyncio.run(run())) == 16


def test_a_token_still_keeps_dns_rebinding_protection_on_loopback(
    served_with_token: tuple[int, object],
) -> None:
    port, _ = served_with_token
    assert _post(port, f"evil.example:{port}", "Bearer " + _TOKEN) == 421


def test_a_non_loopback_bind_lifts_the_host_allow_list() -> None:
    """The token guards a network bind: the Host header names the robot's own address."""
    assert transport_security(8765, "0.0.0.0").enable_dns_rebinding_protection is False
    assert transport_security(8765, "localhost").enable_dns_rebinding_protection is True


def test_stdio_is_the_default_transport() -> None:
    assert _build_arg_parser().parse_args([]).transport == "stdio"


def test_port_without_the_http_transport_is_refused() -> None:
    with pytest.raises(SystemExit):
        main(["--port", "9000"])


@pytest.mark.parametrize("port", ["0", "70000"])
def test_out_of_range_ports_are_refused(port: str) -> None:
    with pytest.raises(SystemExit):
        main(["--transport", "streamable-http", "--port", port])
