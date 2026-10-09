"""TopicForge interop check: every vendor and language this host can run, on one bus.

One command, Windows or Linux:

    python scripts/integration/interop_check.py           # run the check
    python scripts/integration/interop_check.py --list    # what can run here

What it does:
  1. starts every demo participant whose artifact is available on this host
     (see `scripts/integration/DEMO_CONTRACT.md` and the PARTICIPANTS table);
  2. starts TopicForge as a real MCP server over stdio and talks to it through
     the official MCP client, the same path Claude Desktop or Claude Code uses;
  3. checks, against what it actually started: who is on the bus, which
     reader/writer pairs can never talk, and that a stopped participant is
     reported as left once its lease expires;
  4. stops every process it started, even on error or Ctrl+C.

Minimum to run: the Cyclone binding (`pip install "topicforge[dds]"`) and the
Rust participant built once (`cargo build --release` in
`scripts/integration/publishers/dust_publisher`). Every other participant is
optional and joins automatically when present.

TopicForge never publishes: it only reads the DDS discovery topics.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp import Client, StdioServerParameters

REPO = Path(__file__).resolve().parents[2]
PUB = REPO / "scripts" / "integration" / "publishers"
EXE = ".exe" if os.name == "nt" else ""
LEASE_WAIT_S = 40  # Cyclone's default lease is 10 s; leave margin for discovery
SETTLE_S = 6  # time for SPDP announcements to cross the bus before the first query


# ------------------------------------------------------------ participants


@dataclass(frozen=True)
class Participant:
    """One demo program: a vendor, a language, and how to start it here."""

    name: str
    vendor: str  # expected TopicForge vendor tag, or "unknown" when not inferable
    language: str
    argv: Callable[[str], list[str] | None]  # domain -> argv, or None if unavailable
    why_missing: str
    writes: tuple[str, ...] = ()
    reads: tuple[str, ...] = ()
    stoppable: bool = False  # used for the "participant leaves" scenario


def _first_existing(*paths: Path) -> Path | None:
    return next((p for p in paths if p.exists()), None)


def _python_node(script: str, module: str) -> Callable[[str], list[str] | None]:
    def argv(domain: str) -> list[str] | None:
        if importlib.util.find_spec(module) is None:
            return None
        return [sys.executable, str(PUB / script), "--domain", domain]

    return argv


def _binary(*candidates: Path) -> Callable[[str], list[str] | None]:
    def argv(domain: str) -> list[str] | None:
        exe = _first_existing(*candidates)
        return None if exe is None else [str(exe), "--domain", domain]

    return argv


def _cmake_artifacts(directory: str) -> tuple[Path, Path]:
    """Where CMake leaves `<directory>` on single-config and MSVC generators."""
    build = PUB / directory / "build"
    return build / f"{directory}{EXE}", build / "Release" / f"{directory}{EXE}"


def _licensed_rti(
    inner: Callable[[str], list[str] | None],
) -> Callable[[str], list[str] | None]:
    """RTI programs only start where a local license is configured."""

    def argv(domain: str) -> list[str] | None:
        return inner(domain) if os.environ.get("RTI_LICENSE_FILE") else None

    return argv


def _rti(domain: str) -> list[str] | None:
    if importlib.util.find_spec("rti") is None:
        return None
    return _licensed_rti(_python_node("rti_publisher.py", "rti"))(domain)


def _fast_py(domain: str) -> list[str] | None:
    base = PUB / "fast_py"
    if os.name == "nt" or not (base / "build" / "env.sh").exists() or not shutil.which("bash"):
        return None
    return ["bash", str(base / "run.sh"), "--domain", domain]


def _opensplice(domain: str) -> list[str] | None:
    if not os.environ.get("OSPL_HOME"):
        return None
    base = PUB / "opensplice_publisher"
    if os.name == "nt":
        launcher = base / "run_ospl.bat"
        built = _first_existing(base / "build" / "ospl_publisher.exe")
        return (
            None if built is None or not launcher.exists() else [str(launcher), "--domain", domain]
        )
    launcher = base / "run_ospl.sh"
    built = _first_existing(base / "build" / "ospl_publisher")
    if built is None or not launcher.exists() or shutil.which("bash") is None:
        return None
    return ["bash", str(launcher), "--domain", domain]


PARTICIPANTS: tuple[Participant, ...] = (
    Participant(
        name="python/cyclone",
        vendor="cyclone",
        language="Python",
        argv=_python_node("cyclone_publisher.py", "cyclonedds"),
        why_missing='pip install "topicforge[dds]"',
        writes=("DemoOdom",),
        reads=("DemoLidarScan",),
        stoppable=True,
    ),
    Participant(
        name="rust/dust",
        vendor="unknown",  # Dust does not prefix its GUID with its vendor id
        language="Rust",
        argv=_binary(PUB / "dust_publisher" / "target" / "release" / f"dust_publisher{EXE}"),
        why_missing="cargo build --release in publishers/dust_publisher",
        writes=("DemoLidarScan",),
    ),
    Participant(
        name="cpp/fastdds",
        vendor="fast",
        language="C++",
        argv=_binary(
            PUB / "fast_publisher_cpp" / "build" / f"fast_publisher{EXE}",
            PUB / "fast_publisher_cpp" / "build" / "Release" / f"fast_publisher{EXE}",
        ),
        why_missing="build publishers/fast_publisher_cpp (needs Fast DDS 3)",
        writes=("DemoImu",),
        reads=("DemoOdom",),
    ),
    Participant(
        name="python/rti",
        vendor="unknown",  # RTI does not prefix its GUID with its vendor id by default
        language="Python",
        argv=_rti,
        why_missing="pip install rti.connext and set RTI_LICENSE_FILE (local only)",
        writes=("DemoHeartbeat",),
        reads=("DemoImu",),
    ),
    Participant(
        name="c/opensplice",
        vendor="opensplice",
        language="C",
        argv=_opensplice,
        why_missing="EXPERIMENTAL: OSPL_HOME + build publishers/opensplice_publisher",
        writes=("DemoStatus",),
    ),
    # Language participants: each only writes DemoHeartbeat (DEMO_CONTRACT.md).
    Participant(
        name="c/cyclone",
        vendor="cyclone",
        language="C",
        argv=_binary(*_cmake_artifacts("cyclone_c")),
        why_missing="build publishers/cyclone_c (needs Cyclone DDS 11)",
        writes=("DemoHeartbeat",),
    ),
    Participant(
        name="cpp/cyclone",
        vendor="cyclone",
        language="C++",
        argv=_binary(*_cmake_artifacts("cyclone_cpp")),
        why_missing="build publishers/cyclone_cpp (needs Cyclone DDS 11 + cyclonedds-cxx)",
        writes=("DemoHeartbeat",),
    ),
    Participant(
        name="rust/cyclone",
        vendor="cyclone",
        language="Rust",
        argv=_binary(PUB / "cyclone_rust" / "target" / "release" / f"cyclone_rust{EXE}"),
        why_missing="build publishers/cyclone_rust (needs libclang + Cyclone DDS)",
        writes=("DemoHeartbeat",),
    ),
    Participant(
        name="python/dust",
        vendor="unknown",
        language="Python",
        argv=_python_node("dust_py/dust_py_publisher.py", "dust_dds"),
        why_missing="pip install dust-dds==0.16.0",
        writes=("DemoHeartbeat",),
    ),
    Participant(
        name="c/rti",
        vendor="unknown",
        language="C",
        argv=_licensed_rti(_binary(*_cmake_artifacts("rti_c"))),
        why_missing="build publishers/rti_c (Connext 7 + RTI_LICENSE_FILE, local only)",
        writes=("DemoHeartbeat",),
    ),
    Participant(
        name="cpp/rti",
        vendor="unknown",
        language="C++",
        argv=_licensed_rti(_binary(*_cmake_artifacts("rti_cpp"))),
        why_missing="build publishers/rti_cpp (Connext 7 + RTI_LICENSE_FILE, local only)",
        writes=("DemoHeartbeat",),
    ),
    Participant(
        name="python/fastdds",
        vendor="fast",
        language="Python",
        argv=_fast_py,
        why_missing="Linux only: build publishers/fast_py (fastddsgen + SWIG, see README)",
        writes=("DemoHeartbeat",),
    ),
)

# A mismatch is expected when this writer and this reader are both running.
EXPECTED_MISMATCHES: tuple[tuple[str, str, str], ...] = (
    ("DemoLidarScan", "rust/dust", "python/cyclone"),
    ("DemoImu", "cpp/fastdds", "python/rti"),
)


# --------------------------------------------------------------- processes


@dataclass
class Running:
    spec: Participant
    proc: subprocess.Popen[bytes]
    stopped: bool = field(default=False)


def _child_env() -> dict[str, str]:
    """Environment for the participants: native Cyclone libraries made findable.

    The C, C++ and Rust Cyclone programs link `ddsc` dynamically. When
    CYCLONEDDS_HOME points at an install, its library directory is prepended to
    PATH (Windows) or LD_LIBRARY_PATH (Linux). RTI programs expect the user to
    have set up NDDSHOME's library path already, as RTI's own scripts do.
    """
    env = dict(os.environ)
    home = os.environ.get("CYCLONEDDS_HOME")
    if home:
        var, sub = ("PATH", "bin") if os.name == "nt" else ("LD_LIBRARY_PATH", "lib")
        env[var] = os.pathsep.join(p for p in (str(Path(home) / sub), env.get(var, "")) if p)
    return env


def _start(spec: Participant, argv: list[str]) -> Running:
    kwargs: dict[str, Any] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": _child_env(),
    }
    if os.name == "nt":
        # CREATE_NO_WINDOW: console programs would otherwise each pop a window.
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(argv, **kwargs)
    print(f"  started {spec.name:<16} {spec.language:<7} pid {proc.pid}")
    return Running(spec, proc)


def _stop_tree(proc: subprocess.Popen[bytes]) -> None:
    """Stop a process and its children.

    On Windows a venv's python.exe is a launcher that spawns the real
    interpreter, so killing only the launcher would leave an orphan on the bus.
    """
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=5)


# ---------------------------------------------------------------- MCP calls


async def _call(session: Client, tool: str, **args: Any) -> Any:
    result = await session.call_tool(tool, args)
    if result.is_error:
        text = " ".join(getattr(c, "text", "") for c in result.content)
        raise RuntimeError(f"{tool} failed: {text}")
    if result.structured_content is not None:
        return result.structured_content
    return [json.loads(c.text) for c in result.content if getattr(c, "text", None)]


def _print_participants(parts: list[dict[str, Any]]) -> None:
    for p in parts:
        print(f"    {p.get('vendor', '?'):<11} {p.get('status', ''):<7} {p.get('guid')}")


async def _scenario(domain: str, running: list[Running]) -> int:
    env = {
        **os.environ,
        "TOPICFORGE_MODE": "live",
        "TOPICFORGE_DDS_BACKEND": "cyclone",
        "TOPICFORGE_DDS_DOMAIN_ID": domain,
        "TOPICFORGE_LOG_LEVEL": "WARNING",
    }
    server = StdioServerParameters(command=sys.executable, args=["-m", "topicforge"], env=env)
    names = {r.spec.name for r in running}
    failures: list[str] = []

    async with Client(server) as session:
        health = await _call(session, "health_check")
        print(
            f"\n[health_check] mode={health.get('mode')} "
            f"dds_backend={health.get('dds_backend')} version={health.get('server_version')}"
        )

        print("\n[1] list_participants: who is on the bus?")
        parts = (await _call(session, "list_participants", domain_id=int(domain)))["participants"]
        _print_participants(parts)
        expected = len(running) + 1  # every started program plus TopicForge itself
        if len(parts) < expected:
            failures.append(f"expected at least {expected} participants, saw {len(parts)}")
        for r in running:
            if r.spec.vendor != "unknown" and not any(
                p.get("vendor") == r.spec.vendor for p in parts
            ):
                failures.append(f"no participant tagged {r.spec.vendor!r} for {r.spec.name}")

        print("\n[2] detect_qos_mismatches: which reader/writer pairs can never talk?")
        scan = await _call(session, "detect_qos_mismatches")
        mismatches = scan["reports"]
        for m in mismatches:
            print(
                f"    {m['topic']}: {', '.join(m['incompatible_policies'])} ({m['severity']}) "
                f"reader {m['reader_participant_name'] or m['reader_guid']} <- "
                f"writer {m['writer_participant_name'] or m['writer_guid']}"
            )
        for n in scan["not_matched"]:
            print(f"    {n['topic']}: not matched ({n['reason']}): {n['detail']}")
        for topic, writer, reader in EXPECTED_MISMATCHES:
            if {writer, reader} <= names and not any(
                m["topic"] == topic and "Reliability" in m["incompatible_policies"]
                for m in mismatches
            ):
                failures.append(f"expected a Reliability mismatch on {topic}")
        compatible_reported = [m for m in mismatches if m["topic"] == "DemoOdom"]
        if compatible_reported:
            failures.append("DemoOdom is compatible by design but was reported")

        victim = next((r for r in running if r.spec.stoppable), None)
        if victim is not None:
            print(f"\n[3] stopping {victim.spec.name}, then watching the bus")
            _stop_tree(victim.proc)
            victim.stopped = True
            started = time.monotonic()
            left = False
            while time.monotonic() - started < LEASE_WAIT_S:
                await asyncio.sleep(3)
                listing = await _call(session, "list_participants", domain_id=int(domain))
                parts = listing["participants"]
                if any(p.get("status") == "left" for p in parts):
                    left = True
                    print(f"    detected after {time.monotonic() - started:.0f} s:")
                    _print_participants(parts)
                    break
            if not left:
                failures.append(f"no participant reported as left within {LEASE_WAIT_S} s")

        listing = await _call(session, "participant_events", domain_id=int(domain), lookback_s=600)
        events = listing["events"]
        print("\n[4] participant_events: the timeline an agent would read")
        for e in events:
            print(f"    {e['event_type']:<10} {e.get('vendor', '?'):<11} {e['guid']}")

    print()
    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print(
        f"result: PASS ({len(running)} demo participants, {len({r.spec.vendor for r in running})} vendor tags)"
    )
    return 0


def _available(domain: str) -> list[tuple[Participant, list[str] | None]]:
    return [(spec, spec.argv(domain)) for spec in PARTICIPANTS]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TopicForge multi-vendor DDS demo")
    parser.add_argument("--domain", default=os.environ.get("TOPICFORGE_DDS_DOMAIN_ID", "0"))
    parser.add_argument("--list", action="store_true", help="show what can run on this host")
    args = parser.parse_args(argv)

    available = _available(args.domain)
    print("demo participants on this host:")
    for spec, cmd in available:
        state = "ready" if cmd else f"skipped ({spec.why_missing})"
        print(f"  {spec.name:<16} {spec.language:<7} {state}")
    if args.list:
        return 0

    ready = [(s, c) for s, c in available if c]
    names = {s.name for s, _ in ready}
    if not {"python/cyclone", "rust/dust"} <= names:
        print("\nerror: the demo needs at least python/cyclone and rust/dust", file=sys.stderr)
        return 2

    print("\nstarting")
    running: list[Running] = []
    try:
        for spec, cmd in ready:
            assert cmd is not None
            running.append(_start(spec, cmd))
        time.sleep(SETTLE_S)
        return asyncio.run(_scenario(args.domain, running))
    finally:
        for r in running:
            _stop_tree(r.proc)
        print("all demo processes stopped")


if __name__ == "__main__":
    raise SystemExit(main())
