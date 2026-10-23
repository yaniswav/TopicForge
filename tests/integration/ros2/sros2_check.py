"""SROS2 cell of the bench: what TopicForge reports about a secured node.

Runs inside the bench image with ROS 2 sourced (`run_bench.py --sros2`). It creates a
keystore, starts one node with `ROS_SECURITY_ENABLE=true` and the Enforce strategy, then
drives TopicForge over MCP stdio twice:

1. as an observer without an identity (no security variables): the secured participant
   is still seen through SPDP, but with `endpoint_count` 0, and `list_participants`
   carries the "announce no endpoint" note once it has been known for over 10 s;
2. with the robot's security variables: `health_check` reports them
   (`ros_security_enabled`, `ros_security_strategy`, `ros_security_keystore_set`, the note).

These are the claims of docs/CONTRACT.md section 9.16. Prints one line per check and
exits 1 if any fails.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ENCLAVE = "/secure_talker"
NODE = "secure_talker"
TOPIC = "/secure_chatter"
# `endpointless_note` waits 10 s after a participant is first seen; leave margin.
ENDPOINTLESS_WAIT_S = 14.0

TALKER = f"""
import rclpy
from std_msgs.msg import String
rclpy.init()
node = rclpy.create_node("{NODE}")
pub = node.create_publisher(String, "{TOPIC}", 10)
node.create_timer(0.2, lambda: pub.publish(String(data="secret")))
rclpy.spin(node)
"""


def _plain_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith("ROS_SECURITY_")}


def _secure_env(keystore: Path) -> dict[str, str]:
    return {
        **_plain_env(),
        "ROS_SECURITY_ENABLE": "true",
        "ROS_SECURITY_STRATEGY": "Enforce",
        "ROS_SECURITY_KEYSTORE": str(keystore),
    }


def _make_keystore(root: Path) -> Path:
    keystore = root / "keystore"
    for cmd in (
        ["ros2", "security", "create_keystore", str(keystore)],
        ["ros2", "security", "create_enclave", str(keystore), ENCLAVE],
    ):
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    return keystore


async def _session(env: dict[str, str], calls: list[tuple[str, dict[str, Any]]]) -> list[Any]:
    from mcp import Client, StdioServerParameters

    server_env = {
        **env,
        "TOPICFORGE_MODE": "live",
        "TOPICFORGE_DDS_BACKEND": "cyclone",
        "TOPICFORGE_LOG_LEVEL": "WARNING",
    }
    params = StdioServerParameters(command="/opt/tfenv/bin/topicforge", env=server_env)
    out = []
    async with Client(params, read_timeout_seconds=60) as client:
        for name, args in calls:
            if name == "sleep":
                await asyncio.sleep(float(args["s"]))
                continue
            out.append((await client.call_tool(name, args)).structured_content or {})
    return out


def _check(results: list[tuple[str, bool, str]], name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}", flush=True)


def main() -> int:
    results: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory() as tmp:
        keystore = _make_keystore(Path(tmp))
        talker = subprocess.Popen(
            ["python3", "-c", TALKER, "--ros-args", "--enclave", ENCLAVE],
            env=_secure_env(keystore),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            time.sleep(3)
            if talker.poll() is not None:
                print(talker.stderr.read() if talker.stderr else "", file=sys.stderr)
                _check(results, "secured node starts", False, f"exit {talker.returncode}")
                return 1
            observer = asyncio.run(
                _session(
                    _plain_env(),
                    [("sleep", {"s": ENDPOINTLESS_WAIT_S}), ("list_participants", {})],
                )
            )[0]
            parts = [p for p in observer.get("participants", []) if not p.get("is_observer")]
            zero = [p for p in parts if p.get("endpoint_count") == 0]
            _check(
                results,
                "secured participant seen with endpoint_count 0",
                bool(zero),
                f"{len(zero)} of {len(parts)} participants at 0 "
                f"(counts {[p.get('endpoint_count') for p in parts]})",
            )
            note = observer.get("note") or ""
            _check(
                results,
                "endpointless note",
                "announce no endpoint" in note,
                note or "no note",
            )
            health = asyncio.run(_session(_secure_env(keystore), [("health_check", {})]))[0]
            _check(
                results,
                "health_check reads the security variables",
                health.get("ros_security_enabled") is True
                and health.get("ros_security_strategy") == "Enforce"
                and health.get("ros_security_keystore_set") is True
                and "without an identity" in (health.get("ros_security_note") or ""),
                str({k: health.get(k) for k in health if k.startswith("ros_security")}),
            )
            plain = asyncio.run(_session(_plain_env(), [("health_check", {})]))[0]
            _check(
                results,
                "health_check without the variables",
                plain.get("ros_security_enabled") is False
                and plain.get("ros_security_note") is None,
                str({k: plain.get(k) for k in plain if k.startswith("ros_security")}),
            )
        finally:
            talker.terminate()
            talker.wait(10)
    failed = [name for name, ok, _ in results if not ok]
    print(f"sros2: {len(results) - len(failed)} passed, {len(failed)} failed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
