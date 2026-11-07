"""Hold one sampler helper open on a silent topic, so `list_endpoints` can read its writers.

Started by `calls_sampler_writers.json` through `drive.py`, which gives a spawned process no
stdin: this wrapper holds the helper's stdin pipe. When `drive.py` stops the wrapper, the pipe
closes and the helper leaves cleanly; its own deadline bounds it anyway.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HELPER = (
    Path(__file__).resolve().parents[3] / "src/topicforge/adapters/ros2_live/_sampler_helper.py"
)

proc = subprocess.Popen(
    [
        sys.executable,
        str(HELPER),
        "--type",
        "std_msgs/msg/String",
        "--deadline",
        "60",
        "/sampler_hold",
    ],
    stdin=subprocess.PIPE,
    stdout=subprocess.DEVNULL,
)
sys.exit(proc.wait())
