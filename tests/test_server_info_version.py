"""`serverInfo.version` of the MCP handshake equals `health_check.server_version`.

docs/CONTRACT.md section 6: a client that reads the handshake and one that calls
`health_check` must see the same version.
"""

from __future__ import annotations

import asyncio

from mcp import Client

from topicforge import __version__
from topicforge.config import Settings
from topicforge.server import build_app


def test_handshake_version_matches_health_check() -> None:
    app = build_app(
        Settings(mode="mock", log_level="INFO", ros2_executable="ros2", telemetry_enabled=False)
    )

    async def run() -> tuple[str, str, str]:
        async with Client(app) as client:
            health = await client.call_tool("health_check", {})
            assert health.structured_content is not None
            return (
                client.server_info.name,
                client.server_info.version,
                health.structured_content["server_version"],
            )

    name, handshake_version, health_version = asyncio.run(run())
    assert name == "topicforge"
    assert handshake_version == health_version == __version__
