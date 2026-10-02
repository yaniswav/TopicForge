"""Build the ROS 2 bench image and run the live-adapter tests in Docker.

    python tests/integration/ros2/run_bench.py --distro humble --rmw fastrtps

Extra pytest arguments go after `--`, e.g. `-- -k scan`.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DOCKERFILE = "tests/integration/ros2/Dockerfile"
ENTRYPOINT = "/opt/topicforge/tests/integration/ros2/entrypoint.sh"
RMW = {"fastrtps": "rmw_fastrtps_cpp", "cyclonedds": "rmw_cyclonedds_cpp"}


def _run(cmd: list[str]) -> int:
    print("+ " + " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=REPO, check=False).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the ROS 2 live-adapter bench in Docker.")
    parser.add_argument("--distro", choices=["humble", "jazzy"], default="humble")
    parser.add_argument("--rmw", choices=sorted(RMW), default="fastrtps")
    parser.add_argument("--no-build", action="store_true", help="reuse an existing image")
    parser.add_argument("pytest_args", nargs="*", help="extra pytest arguments (after --)")
    args = parser.parse_args(argv)

    image = f"topicforge-ros2:{args.distro}"
    if not args.no_build:
        rc = _run(
            [
                "docker",
                "build",
                "-f",
                DOCKERFILE,
                "--build-arg",
                f"DISTRO={args.distro}",
                "-t",
                image,
                ".",
            ]
        )
        if rc != 0:
            print("bench: image build failed", file=sys.stderr)
            return rc

    name = f"topicforge-bench-{uuid.uuid4().hex[:8]}"
    try:
        rc = _run(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                name,
                "-e",
                f"RMW_IMPLEMENTATION={RMW[args.rmw]}",
                image,
                "bash",
                ENTRYPOINT,
                *args.pytest_args,
            ]
        )
    finally:
        # --rm covers a normal exit; this covers an interrupted run.
        subprocess.run(["docker", "rm", "-f", name], cwd=REPO, check=False, capture_output=True)
    print(f"bench: {args.distro} / {RMW[args.rmw]}: exit code {rc}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
