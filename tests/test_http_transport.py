"""The optional local HTTP transport: loopback bind, DNS-rebinding protection, CLI rules."""

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
from topicforge.server.http import HTTP_PATH, LOOPBACK_HOST, build_http_server, transport_security

_SETTINGS = Settings(mode="mock", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind((LOOPBACK_HOST, 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def served() -> Iterator[tuple[int, object]]:
    port = _free_port()
    server = build_http_server(build_app(_SETTINGS), port)
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
    assert len(names) == 14 and "health_check" in names


def _post(port: int, host_header: str) -> int:
    conn = http.client.HTTPConnection(LOOPBACK_HOST, port, timeout=10)
    try:
        conn.request(
            "POST",
            HTTP_PATH,
            body="{}",
            headers={
                "Host": host_header,
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )
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


def test_there_is_no_flag_to_change_the_host() -> None:
    parser = _build_arg_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--transport", "streamable-http", "--host", "0.0.0.0"])


def test_stdio_is_the_default_transport() -> None:
    assert _build_arg_parser().parse_args([]).transport == "stdio"


def test_port_without_the_http_transport_is_refused() -> None:
    with pytest.raises(SystemExit):
        main(["--port", "9000"])


@pytest.mark.parametrize("port", ["0", "70000"])
def test_out_of_range_ports_are_refused(port: str) -> None:
    with pytest.raises(SystemExit):
        main(["--transport", "streamable-http", "--port", port])
