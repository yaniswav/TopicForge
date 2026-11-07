"""Decode cell of the bench: live samples against `ros2 topic echo`, on the running RMW.

Runs inside the bench image with ROS 2 sourced (`run_bench.py --decode`), or anywhere with
`TOPICFORGE_DECODE_SERVER` naming the server command. It starts `decode_pub.py`, one
`ros2 topic echo --full-length` per topic and an independent DDS observer, then drives
TopicForge over MCP stdio with `TOPICFORGE_DDS_DECODE=on`:

- `String`, `LaserScan` (best effort) and `Odometry` samples equal the echo, field by field,
  matched by the index each message carries;
- a real publish rate, a silent topic inside its bound, a latched sample, a heavy type refused;
- no ghost reader on the writers, no `topicforge_reader` in any listing or event, and the
  observer sees those participants create one reader each and no writer;
- `diagnose` reports the same findings before and after the reads, and no restart;
- with the flag off, the same calls answer as before the flag existed.

These are the claims of docs/CONTRACT.md section 9.27. Prints one line per check and exits 1
if any fails.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
READER = "topicforge_reader"
COMPARED = {
    "/decode/string": "std_msgs/msg/String",
    "/decode/scan": "sensor_msgs/msg/LaserScan",
    "/decode/odom": "nav_msgs/msg/Odometry",
}
RATE_HZ = 10.0
ROUNDS = 3
SILENT_MAX_S = 9.0
DISCOVERY_S = 6.0
GHOST_MAX_S = 15.0


def _check(results: list[tuple[str, bool]], name: str, ok: bool, detail: Any) -> None:
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}", flush=True)


def message_id(doc: dict[str, Any]) -> int:
    """The index a `decode_pub` message carries."""
    if isinstance(doc.get("data"), str):
        return int(re.search(r"(\d+)$", doc["data"]).group(1))
    return int(doc["header"]["stamp"]["sec"])


def echo_docs(path: Path) -> dict[int, dict[str, Any]]:
    """The messages of one `ros2 topic echo` file, by index."""
    from ruamel.yaml import YAML

    text = path.read_text(encoding="utf-8")
    # A document cut by the end of the process would not parse.
    text = text[: text.rfind("---\n") + 4] if "---\n" in text else ""
    return {message_id(d): d for d in YAML(typ="safe").load_all(text) if d}


def _same_float(echo: float, got: Any) -> bool:
    if isinstance(got, str):
        return (math.isnan(echo) and got == "nan") or str(echo) == got
    return isinstance(got, (int, float)) and not isinstance(got, bool) and echo == float(got)


def differences(echo: Any, got: Any, path: str = "") -> list[str]:
    """Every leaf where `got` is not `echo`; arrays and keys must match exactly."""
    if isinstance(echo, dict):
        if not isinstance(got, dict) or set(echo) != set(got):
            return [
                f"{path}: keys {sorted(echo)} vs {got if not isinstance(got, dict) else sorted(got)}"
            ]
        return [d for key in echo for d in differences(echo[key], got[key], f"{path}.{key}")]
    if isinstance(echo, list):
        if not isinstance(got, list) or len(echo) != len(got):
            return [
                f"{path}: {len(echo)} elements vs {got if not isinstance(got, list) else len(got)}"
            ]
        return [
            d
            for i, (a, b) in enumerate(zip(echo, got, strict=True))
            for d in differences(a, b, f"{path}[{i}]")
        ]
    if isinstance(echo, float):
        return [] if _same_float(echo, got) else [f"{path}: {echo!r} vs {got!r}"]
    return [] if echo == got else [f"{path}: {echo!r} vs {got!r}"]


def visible_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """A decoded payload without TopicForge's own `_decode_*` keys."""
    return {k: v for k, v in payload.items() if not k.startswith("_")}


class Observer(threading.Thread):
    """A DDS participant of this script: what `topicforge_reader` announces, seen from outside."""

    def __init__(self, domain: int) -> None:
        super().__init__(daemon=True)
        self.domain = domain
        self.stopping = threading.Event()
        self.readers: set[str] = set()
        self.subscriptions: list[str] = []
        self.publications: list[str] = []

    def run(self) -> None:
        from cyclonedds.builtin import (
            BuiltinDataReader,
            BuiltinTopicDcpsParticipant,
            BuiltinTopicDcpsPublication,
            BuiltinTopicDcpsSubscription,
        )
        from cyclonedds.core import Policy, Qos
        from cyclonedds.domain import DomainParticipant

        dp = DomainParticipant(self.domain, qos=Qos(Policy.EntityName("decode_check_observer")))
        parts = BuiltinDataReader(dp, BuiltinTopicDcpsParticipant)
        pubs = BuiltinDataReader(dp, BuiltinTopicDcpsPublication)
        subs = BuiltinDataReader(dp, BuiltinTopicDcpsSubscription)
        seen_pubs: list[Any] = []
        seen_subs: list[Any] = []
        while not self.stopping.wait(0.05):
            for sample in parts.take(N=256):
                qos = getattr(sample, "qos", None)
                if qos is not None and any(getattr(p, "name", None) == READER for p in qos):
                    self.readers.add(str(sample.key))
            seen_pubs += [s for s in pubs.take(N=1024) if hasattr(s, "topic_name")]
            seen_subs += [s for s in subs.take(N=1024) if hasattr(s, "topic_name")]
            self.publications = [
                s.topic_name for s in seen_pubs if str(s.participant_key) in self.readers
            ]
            self.subscriptions = [
                s.topic_name for s in seen_subs if str(s.participant_key) in self.readers
            ]


def _server_params(env: dict[str, str], decode: str) -> Any:
    from mcp import StdioServerParameters

    command = shlex.split(env.get("TOPICFORGE_DECODE_SERVER", "/opt/tfenv/bin/topicforge"))
    server_env = {
        **env,
        "TOPICFORGE_MODE": "live",
        "TOPICFORGE_DDS_BACKEND": "cyclone",
        "TOPICFORGE_DDS_DOMAIN_ID": env.get("ROS_DOMAIN_ID", "0"),
        "TOPICFORGE_DDS_DECODE": decode,
        "TOPICFORGE_LOG_LEVEL": "WARNING",
    }
    return StdioServerParameters(command=command[0], args=command[1:], env=server_env)


def _findings(result: dict[str, Any]) -> list[tuple[str, str]]:
    return sorted(
        (f.get("severity", ""), f.get("check", ""), f.get("subject") or "", f.get("text", "")[:90])
        for f in result.get("findings", [])
    )


async def _ghosts_gone(status: Path, wanted: dict[str, int]) -> tuple[bool, float, dict]:
    """Wait for the writers' counts to reach `wanted`; `GHOST_MAX_S` covers a Cyclone lease."""
    started = time.monotonic()
    while True:
        counts = json.loads(status.read_text(encoding="utf-8"))
        took = time.monotonic() - started
        if counts == wanted or took >= GHOST_MAX_S:
            return counts == wanted, took, counts
        await asyncio.sleep(0.5)


async def _peek_rounds(call: Any) -> dict[str, list[dict[str, Any]]]:
    """`ROUNDS` peeks per compared topic; a short pause lets the writers' discovery settle."""
    out: dict[str, list[dict[str, Any]]] = {t: [] for t in COMPARED}
    for _ in range(ROUNDS):
        for topic in COMPARED:
            result = await call("peek_dds_samples", {"topic": topic, "count": 5})
            out[topic] += result.get("samples", [])
        await asyncio.sleep(0.5)
    return out


async def _rate(call: Any) -> dict[str, Any]:
    """A rate read; once more if a wall-clock jump made the first unusable."""
    first = await call("topic_metrics", {"topic": "/decode/scan", "window_s": 5})
    if first.get("observed_frequency_hz") is not None:
        return first
    return await call("topic_metrics", {"topic": "/decode/scan", "window_s": 5})


async def _flag_on(results: list[tuple[str, bool]], env: dict[str, str], status: Path) -> dict:
    from mcp import Client

    async with Client(_server_params(env, "on"), read_timeout_seconds=90) as client:

        async def call(name: str, args: dict[str, Any]) -> dict[str, Any]:
            return (await client.call_tool(name, args)).structured_content or {}

        health = await call("health_check", {})
        _check(results, "health_check says decoding is on", health.get("dds_decode") is True, "")
        await asyncio.sleep(DISCOVERY_S)
        before = _findings(await call("diagnose", {"budget_s": 30}))
        peeks = await _peek_rounds(call)

        rate = await _rate(call)
        hz = rate.get("observed_frequency_hz")
        _check(
            results,
            "topic_metrics is the real publish rate",
            rate.get("frequency_basis") == "source_timestamps"
            and rate.get("status") == "ok"
            and hz is not None
            and abs(hz - RATE_HZ) <= 1.0,
            f"{hz} Hz over {rate.get('samples_observed')} samples, {rate.get('note')}",
        )

        started = time.monotonic()
        silent = await call("peek_dds_samples", {"topic": "/decode/silent", "count": 1})
        took = time.monotonic() - started
        _check(
            results,
            "a silent topic ends inside its bound",
            silent.get("count") == 0 and took <= SILENT_MAX_S,
            f"{took:.1f} s, {silent.get('note')}",
        )
        quiet = await call("topic_metrics", {"topic": "/decode/silent", "window_s": 2})
        _check(
            results,
            "a silent topic has no rate",
            quiet.get("status") == "no_samples_yet",
            quiet.get("note"),
        )

        latched = await call("peek_dds_samples", {"topic": "/decode/latched", "count": 1})
        values = [s["payload"].get("data") for s in latched.get("samples", [])]
        _check(results, "a latched sample is delivered", values == ["decode latched 1"], values)

        image = await call("peek_dds_samples", {"topic": "/decode/image", "count": 1})
        _check(
            results,
            "a heavy type is refused",
            image.get("count") == 0 and "too heavy" in (image.get("note") or ""),
            image.get("note"),
        )

        wanted = {
            **dict.fromkeys(COMPARED, 1),
            "/decode/silent": 0,
            "/decode/latched": 0,
            "/decode/image": 0,
        }
        gone, took, counts = await _ghosts_gone(status, wanted)
        _check(
            results,
            "no ghost reader on the writers",
            gone,
            f"{took:.1f} s after the reads: {counts}",
        )

        parts = await call("list_participants", {})
        named = [p.get("name") for p in parts.get("participants", [])]
        events = await call("participant_events", {"lookback_s": 3600})
        endpoints = await call(
            "list_endpoints", {"include_observer": True, "include_departed": True}
        )
        seen = [
            *named,
            *(e.get("name") for e in events.get("events", [])),
            *(e.get("participant_name") for e in endpoints.get("endpoints", [])),
        ]
        _check(
            results,
            "no listing or event names the reader",
            READER not in seen,
            f"{len(seen)} names",
        )
        restarts = {
            **(parts.get("restarts_by_name") or {}),
            **(events.get("restarts_by_name") or {}),
        }
        _check(
            results,
            "no restart counted",
            all(v.get("lives", 1) <= 1 for v in restarts.values()),
            restarts,
        )

        after = _findings(await call("diagnose", {"budget_s": 30}))
        serious = [f for f in after if f[0] != "info"]
        _check(
            results,
            "diagnose unchanged by the reads",
            serious == [f for f in before if f[0] != "info"]
            and not any("restart" in f[3].lower() for f in after),
            f"{before} -> {after}",
        )
        return peeks


async def _flag_off(results: list[tuple[str, bool]], env: dict[str, str]) -> None:
    from mcp import Client

    async with Client(_server_params(env, "off"), read_timeout_seconds=60) as client:

        async def call(name: str, args: dict[str, Any]) -> dict[str, Any]:
            return (await client.call_tool(name, args)).structured_content or {}

        health = await call("health_check", {})
        await asyncio.sleep(DISCOVERY_S)
        peek = await call("peek_dds_samples", {"topic": "/decode/string", "count": 1})
        metrics = await call("topic_metrics", {"topic": "/decode/string", "window_s": 2})
        _check(
            results,
            "flag off answers as before",
            health.get("dds_decode") is False
            and peek.get("count") == 0
            and "payload decoding is disabled" in (peek.get("note") or "")
            and metrics.get("status") == "unsupported_user_topic"
            and metrics.get("frequency_basis") is None,
            f"{peek.get('note')} / {metrics.get('status')}",
        )


def _compare(results: list[tuple[str, bool]], peeks: dict, folder: Path) -> None:
    for topic, ros_type in COMPARED.items():
        echo = echo_docs(folder / f"{topic.strip('/').replace('/', '_')}.yaml")
        samples = peeks[topic]
        bad: list[str] = []
        equal = unmatched = 0
        for sample in samples:
            fields = visible_fields(sample["payload"])
            if (
                sample["payload"].get("_decode_status") != "full"
                or sample["message_type"] != ros_type
            ):
                bad.append(f"{sample['message_type']} {sample['payload'].get('_decode_status')}")
                continue
            doc = echo.get(message_id(fields))
            if doc is None:
                unmatched += 1
                continue
            found = differences(doc, fields)
            bad += found
            equal += not found
        _check(
            results,
            f"{ros_type} equals ros2 topic echo",
            equal > 0 and not bad and unmatched == 0,
            f"{equal} of {len(samples)} samples equal, {len(echo)} echoed, "
            f"{unmatched} unmatched, {bad[:4]}",
        )


def main() -> int:
    results: list[tuple[str, bool]] = []
    env = dict(os.environ)
    domain = int(env.get("ROS_DOMAIN_ID", "0"))
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        status = folder / "counts.json"
        procs = [
            subprocess.Popen(
                ["python3", str(HERE / "decode_pub.py"), str(status)],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        ]
        echoes = []
        files = []
        for topic in COMPARED:
            out = (folder / f"{topic.strip('/').replace('/', '_')}.yaml").open(
                "w", encoding="utf-8"
            )
            files.append(out)
            echoes.append(
                subprocess.Popen(
                    ["ros2", "topic", "echo", "--full-length", topic],
                    env=env,
                    stdout=out,
                    stderr=subprocess.DEVNULL,
                )
            )
        observer = Observer(domain)
        observer.start()
        try:
            time.sleep(DISCOVERY_S)
            peeks = asyncio.run(_flag_on(results, env, status))
            observer.stopping.set()
            observer.join(5)
            _check(
                results,
                "the reader participant creates no writer",
                bool(observer.readers) and not observer.publications,
                f"{len(observer.readers)} participants, {len(observer.subscriptions)} readers "
                f"on {sorted({str(t) for t in observer.subscriptions})}, writers {observer.publications}",
            )
            asyncio.run(_flag_off(results, env))
            for echo in echoes:
                echo.send_signal(signal.SIGINT)
            for echo in echoes:
                echo.wait(10)
            for out in files:
                out.close()
            _compare(results, peeks, folder)
        finally:
            for proc in [*echoes, *procs]:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
    failed = [name for name, ok in results if not ok]
    print(f"decode: {len(results) - len(failed)} passed, {len(failed)} failed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
