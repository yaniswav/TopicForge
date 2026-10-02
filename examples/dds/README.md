# TopicForge DDS examples

Runnable examples on a **real DDS bus**. Each one starts a few small DDS
programs that play the roles of a robot (`lidar_driver`, `nav_planner`...),
from different DDS vendors, then questions TopicForge through the official
MCP client, the same way Claude Desktop or Claude Code would. Each example
checks TopicForge's answer and prints PASS or FAIL.

TopicForge only observes. It never publishes: the role programs are ordinary
DDS applications, and TopicForge reads the standard DDS discovery topics.

## Learning path

Start with the concepts, one per example:

| # | Example | You learn | TopicForge tool |
|---|---|---|---|
| 01 | [Who is on the bus?](01_who_is_on_the_bus/) | discovery, names, vendors | `list_participants` |
| 02 | [Why can't they talk?](02_why_cant_they_talk/) | QoS request/offered, Reliability | `detect_qos_mismatches` |
| 03 | [A node crashed](03_a_node_crashed/) | leases, crash detection | `participant_events` |
| 04 | [A late joiner misses the data](04_late_joiner_misses_data/) | Durability | `detect_qos_mismatches` |

Then real incidents, simple ones, each a few programs:

| # | Example | The incident | What it teaches |
|---|---|---|---|
| 10 | [The LIDAR went silent after a driver swap](10_lidar_silent_after_driver_swap/) | new supplier, renamed topic, old driver still running | a short mismatch list is not a healthy bus |
| 11 | [Who talks to whom?](11_who_talks_to_whom/) | robot assembled from two suppliers | the wiring on the wire, foreign vendors included |
| 12 | [The safety monitor dropped out](12_safety_monitor_dropout/) | estop loses its only writer | consequences of a crash, limits of lease-based detection |
| 13 | [The deadline is not offered](13_deadline_not_offered/) | reader requires 100 ms, writer promises nothing | Deadline request/offered |
| 14 | [A node in a restart loop](14_restart_loop/) | supervisor restarts a crashing node | GUIDs, names, the event timeline |

## Setup

Python 3.10 to 3.13 (the Cyclone DDS wheels stop at 3.13), from the
repository root:

```
python -m venv .venv-demo
.venv-demo/Scripts/activate          # Linux: source .venv-demo/bin/activate
pip install -e ".[dds]" dust-dds==0.16.0
```

That installs TopicForge, the Cyclone DDS binding, and the Dust DDS binding.
No compiler, no native SDK. Windows and Linux both work.

## Run

```
python examples/dds/01_who_is_on_the_bus/run.py         # one example
python examples/dds/run_all.py                          # all of them, with a summary
```

Exit code 0 means PASS, 1 FAIL, 2 "cannot run here" (a binding or license is
missing; the message says which).

## Ask your own agent

`--hold` starts the programs and keeps them running instead of checking:

```
python examples/dds/02_why_cant_they_talk/run.py --hold
```

Point your MCP client at TopicForge in live DDS mode, for example in Claude
Desktop's `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "topicforge": {
      "command": "C:/path/to/TopicForge/.venv-demo/Scripts/python.exe",
      "args": ["-m", "topicforge"],
      "env": {
        "TOPICFORGE_MODE": "live",
        "TOPICFORGE_DDS_BACKEND": "cyclone",
        "TOPICFORGE_DDS_DOMAIN_ID": "0"
      }
    }
  }
}
```

Then ask the question from the example's README.

## How the examples are built

- `harness.py`: starts and stops the programs (no orphan processes, no
  console windows on Windows), talks to TopicForge over MCP, prints the
  checks.
- `nodes/`: one role program per vendor (`cyclone_node.py`, `dust_node.py`,
  `rti_node.py`), all with the same command line. An endpoint is
  `TOPIC:TYPE[:options]`, for example:

  ```
  python nodes/cyclone_node.py --name nav_planner --read scan:LidarScan:reliable --write cmd_vel:Twist
  ```

  Options: `reliable`, `best_effort`, `volatile`, `transient_local`,
  `keep_all`, `keep_last=N`, `deadline=MS`. Types: `LidarScan`, `Odom`,
  `Imu`, `Twist`, `Status`, `Heartbeat` (see `nodes/spec.py`).

## Known limits

- Dust DDS participants show as vendor `unknown` and without a name: Dust
  does not announce a name, and does not put its vendor id where the Cyclone
  binding can read it. RTI Connext participants also show as `unknown`.
- The Dust DDS Python binding (0.16) can only publish integer fields, and its
  RELIABLE writes time out against a Cyclone reader. Its endpoints still
  appear on the bus with their QoS, which is all these examples need.
- RTI Connext examples need your own RTI license (`RTI_LICENSE_FILE`). They
  are skipped without one and never run in public CI.
- TopicForge checks Reliability, Durability and Deadline compatibility, and
  flags History as risky. Liveliness, Ownership and Partition are not checked.
  It pairs readers and writers by topic name, assuming the same type and the
  default partition.
- It sees the QoS each side declares, never whether a running program meets
  it, and it notices a departure only when asked (it polls).
- TopicForge does not decode the data on your topics yet, so it cannot tell
  you a publish rate or a value.

For every vendor and language combination (C, C++, Rust, Python), see the
interop bench in `scripts/integration/`.
