# Testing TopicForge

A practical guide to running TopicForge against real ROS2 traffic, or
against the deterministic mock fixtures when you don't have ROS2 installed.
This document is the single source of truth for "how do I actually try
this thing"; the top-level `README.md` only covers the basics.

If you find a step that doesn't work, please [open an issue](https://github.com/yaniswav/TopicForge/issues)
— that's the highest-signal feedback for the current release.

---

## Pick your path

| You want to... | Time | Path |
| --- | --- | --- |
| See the five MCP tools work end-to-end without installing ROS2 | 5 min | [Path 1 — Mock mode](#path-1--mock-mode-no-ros2-required) |
| Validate live mode against real ROS2 traffic on Windows | 45 min | [Path 2 — WSL2 + Humble](#path-2--wsl2--ros2-humble-windows-recommended) |
| Same as Path 2, but you're already on Ubuntu/Debian | 20 min | [Path 3 — Linux native](#path-3--linux-native) |
| Reproducible throwaway environment | 15 min | [Path 4 — Docker](#path-4--docker-throwaway) |
| Native Windows ROS2 install (no virtualization) | 1–2 h | [Path 5 — Windows native (advanced)](#path-5--windows-native-advanced) |

Once any path is set up, jump to [Test scenarios](#test-scenarios) to
exercise the five tools, then [Connect an MCP client](#connect-an-mcp-client)
to use TopicForge from Claude Desktop, Claude Code, Cursor, etc.

---

## Path 1 — Mock mode (no ROS2 required)

The fastest way to confirm the server starts, registers all five tools,
and serves typed payloads to an MCP client.

```bash
# Python 3.11+ required
python -m venv .venv
source .venv/bin/activate          # Linux / macOS / WSL
# .venv\Scripts\Activate.ps1       # Windows PowerShell

pip install topicforge

# Sanity check
python -m topicforge --version     # → topicforge 0.1.1
python -m topicforge --help

# Run the server (it blocks on stdio — that's normal, MCP clients spawn it)
# Linux / macOS / WSL:
TOPICFORGE_MODE=mock python -m topicforge

# Windows PowerShell:
$env:TOPICFORGE_MODE = "mock"; python -m topicforge
```

The server prints one INFO line to stderr and then waits for MCP traffic
on stdin. Press `Ctrl+C` to stop.

To actually call the tools, jump to [Connect an MCP client](#connect-an-mcp-client)
and use `"env": { "TOPICFORGE_MODE": "mock" }` in the config.

The mock graph contains 5 topics modeling a small differential robot with
LIDAR + RGB camera. Outputs are deterministic across runs.

---

## Path 2 — WSL2 + ROS2 Humble (Windows, recommended)

This is what most Windows ROS2 developers do in practice. The investment
pays off beyond TopicForge — you get a proper ROS2 environment for any
future robotics work.

### 2.1 Install WSL2 with Ubuntu 22.04

From an elevated PowerShell:

```powershell
wsl --install -d Ubuntu-22.04
```

Reboot when prompted. On first launch, set your Linux user and password.

### 2.2 Install ROS2 Humble

All remaining commands run inside the WSL Ubuntu shell.

```bash
sudo apt update && sudo apt install -y software-properties-common curl
sudo add-apt-repository universe -y

# Add the ROS2 apt repo
sudo curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
  -o /usr/share/keyrings/ros-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu $(. /etc/os-release && echo $UBUNTU_CODENAME) main" \
  | sudo tee /etc/apt/sources.list.d/ros2.list > /dev/null

# Install the base distro + the demo nodes + MCAP storage
sudo apt update
sudo apt install -y \
  ros-humble-ros-base \
  ros-humble-demo-nodes-cpp \
  ros-humble-rosbag2-storage-mcap

# Source on every shell
echo "source /opt/ros/humble/setup.bash" >> ~/.bashrc
source ~/.bashrc

# Verify
ros2 --help                        # → usage info
ros2 doctor                        # → some warnings are OK on WSL
```

### 2.3 Install TopicForge inside WSL

```bash
sudo apt install -y python3-pip python3-venv
python3 -m venv ~/topicforge-venv
source ~/topicforge-venv/bin/activate
pip install topicforge
topicforge --version               # → topicforge 0.1.1
```

### 2.4 Run live mode end-to-end

You'll need three WSL terminals.

**Terminal A — publish something on the graph:**

```bash
source /opt/ros/humble/setup.bash
ros2 run demo_nodes_cpp talker     # publishes /chatter at ~1 Hz
```

**Terminal B — confirm ROS2 sees the publisher:**

```bash
source /opt/ros/humble/setup.bash
ros2 topic list                    # should include /chatter
ros2 topic echo --once /chatter    # one message, then exits
```

**Terminal C — run TopicForge in live mode:**

```bash
source /opt/ros/humble/setup.bash
source ~/topicforge-venv/bin/activate
TOPICFORGE_MODE=live python -m topicforge
```

TopicForge will log `topicforge 0.1.1 ready (mode=live, adapter=live)`.
From here, an MCP client connected over stdio can call `list_topics`,
`sample_messages /chatter`, etc., against the real graph.

---

## Path 3 — Linux native

Same as Path 2.2 onward, skipping WSL setup. Tested on Ubuntu 22.04 (Humble),
Ubuntu 24.04 (Jazzy). Adjust the distro name in the apt repo line accordingly.

For Jazzy: replace `ros-humble-*` packages with `ros-jazzy-*` and source
`/opt/ros/jazzy/setup.bash`.

---

## Path 4 — Docker (throwaway)

Fastest way to a clean ROS2 environment if you don't want to install
anything natively. Both the publisher and TopicForge run inside the
container so you don't have to fight DDS multicast across the Docker
network boundary.

```bash
docker run -it --rm --name ros2-topicforge \
  -p 0.0.0.0:0:0 \
  osrf/ros:humble-desktop bash

# Inside the container:
apt update && apt install -y python3-pip ros-humble-demo-nodes-cpp
pip install topicforge

# Start a publisher in the background
ros2 run demo_nodes_cpp talker &

# Run TopicForge live
TOPICFORGE_MODE=live python3 -m topicforge
```

To connect an MCP client from the host: easier to just run TopicForge
on the host (Path 2 or 3). Cross-container stdio is more friction than
it's worth for a demo.

---

## Path 5 — Windows native (advanced)

The official [ROS2 Humble binary install for
Windows](https://docs.ros.org/en/humble/Installation/Windows-Install-Binary.html)
works, but the install path is heavy (Visual Studio Build Tools, pinned
Python 3.10, OpenSSL, manual `call C:\dev\ros2_humble\local_setup.bat`
in every shell). Use it only if you need to demo ROS2 on a Windows
machine without virtualization.

Once installed, in a shell where `setup.bat` is sourced:

```powershell
$env:TOPICFORGE_MODE = "live"
python -m topicforge
```

TopicForge resolves `ros2.cmd` / `ros2.bat` via `shutil.which`, so no
extra config is needed beyond having the ROS2 install on PATH.

---

## Test scenarios

These three scenarios are what to demo in a 90-second screencast and
what an early adopter will try first.

### Scenario A — Discover the graph

In your MCP client:

> "What topics are currently being published, and what message types do they carry?"

Expected: TopicForge calls `list_topics`, returns a `list[TopicInfo]`.
In live mode you'll see `/chatter` plus the usual ROS2 system topics
(`/rosout`, `/parameter_events`). In mock mode you'll see the five
fixture topics (`/cmd_vel`, `/odom`, `/scan`, `/tf`, `/camera/image_raw`).

### Scenario B — Inspect a topic and sample a message

> "Show me the latest message on /chatter."

Expected: `get_topic_info /chatter` then `sample_messages /chatter`.
The `samples[0].payload` will contain the parsed top-level keys from
`ros2 topic echo --once` plus a `_raw_text` field with the verbatim
CLI output. In live mode `samples[i].timestamp_ns` is always `0` — the
CLI does not expose receive times. A future `rclpy`-backed adapter
will fix that.

### Scenario C — Record and analyze a bag

In a WSL or Linux shell:

```bash
# Record /chatter for 30 seconds
ros2 bag record /chatter --output ~/demo_run --max-bag-duration 30
# Wait 30s, then Ctrl+C
```

Then in your MCP client:

> "Analyze the bag at /home/<you>/demo_run. How many messages, at what
> rate, and is anything anomalous?"

Expected: `analyze_bag` returns a `BagAnalysis` with `duration_seconds`,
`message_count`, per-topic stats, and (mock mode only) a list of canned
anomalies. Live mode parses `ros2 bag info` output and currently does
not detect anomalies — that's mock-only until a real anomaly detector
ships in v0.2.

---

## Connect an MCP client

TopicForge speaks the MCP stdio protocol. Any compliant client can spawn
it. The minimum config is the same shape everywhere: a command + env vars.

### Claude Desktop

Add to `claude_desktop_config.json` (location varies by OS — Claude
Desktop's docs cover it):

```json
{
  "mcpServers": {
    "topicforge": {
      "command": "topicforge",
      "env": { "TOPICFORGE_MODE": "auto" }
    }
  }
}
```

Restart Claude Desktop. The five tools appear under the hammer icon.

### Claude Code

```bash
claude mcp add topicforge -- topicforge
```

Or add to `~/.claude/mcp_servers.json`:

```json
{
  "topicforge": {
    "command": "topicforge",
    "env": { "TOPICFORGE_MODE": "auto" }
  }
}
```

### Cursor / Continue / Cline / others

Any MCP-compliant client accepts the same stdio config. Refer to your
client's MCP documentation; the command stays `topicforge` (or
`python -m topicforge` if the `topicforge` script isn't on PATH).

---

## Troubleshooting

### `topicforge: command not found`

The `[project.scripts]` entry point isn't on PATH. Either re-activate
the venv that has TopicForge installed, or use `python -m topicforge`
instead.

### `health_check` returns `ros2_available: false` in auto mode

Auto mode resolves to `mock` because `shutil.which("ros2")` returned
nothing. Confirm `ros2` is on PATH in the shell that spawned TopicForge:

```bash
which ros2          # Linux / WSL
where.exe ros2      # Windows
```

If ROS2 is installed but not on PATH, source the setup file in the
parent shell **before** launching the MCP client. On Windows native,
`ros2.cmd` is what `shutil.which` resolves — TopicForge handles it.

You can also override the binary explicitly:

```bash
TOPICFORGE_ROS2_BIN=/opt/ros/humble/bin/ros2 python -m topicforge
```

### `sample_messages` returns an empty `samples` list

In live mode, `sample_messages` calls `ros2 topic echo --once` with a
3-second timeout. If no publisher is currently active on the topic,
nothing comes back. Confirm with `ros2 topic info -v <topic>` that
`Publisher count > 0` before retrying.

### `analyze_bag` fails with `Bag path does not exist`

The path is resolved in the shell where TopicForge runs. On WSL, a
path like `C:\demos\run.mcap` is not valid — use `/mnt/c/demos/run.mcap`.
On Windows native, both `C:\demos\run.mcap` and `C:/demos/run.mcap`
work (TopicForge uses `pathlib`).

### `analyze_bag` complains in mock mode about extensions

Mock mode accepts only `.mcap`, `.db3`, `.bag`, or extensionless paths
(assumed to be `rosbag2_*` directories). This mirrors what the live
adapter would refuse. Use a path with one of those suffixes.

### Claude Desktop doesn't show the tools

1. Check the Claude Desktop logs (Help → View Logs → MCP).
2. Confirm `topicforge --version` runs from the same shell that spawned
   Claude Desktop. PATH and venv activation are not inherited across
   GUI launchers — you may need to point the config at the absolute
   path of the `topicforge` binary inside your venv.
3. JSON syntax errors silently drop the whole config. Validate with
   `cat claude_desktop_config.json | python -m json.tool`.

### Slow startup

The first call to a `list_topics` in live mode shells out to
`ros2 topic list -t`, which initializes the DDS middleware. Expect a
1–2 s warm-up. Subsequent calls are fast.

---

## What's intentionally missing

- **No write path**. Publishing or commanding robots is out of scope by
  design. TopicForge is read-only and there is no roadmap to change that
  without explicit per-tool opt-in and auth.
- **No `rclpy`-backed adapter yet**. Live mode uses the `ros2` CLI, which
  is portable across distros but loses the per-message timestamp and
  caps `sample_messages` at the `--once` semantic. A native `rclpy`
  adapter is the headline item for v0.2.
- **No native `.mcap` reader**. `analyze_bag` parses `ros2 bag info`
  text output. Fine for summarization, not fine for deep inspection of
  large bags. v0.2 will integrate a direct MCAP reader.

---

## Feedback

If you got here, you already invested 15 minutes in evaluating
TopicForge. The two highest-value things you can do next:

1. Try it against your own ROS2 graph and tell me which tool's output
   was useful, useless, or wrong on your stack — via [GitHub
   Issues](https://github.com/yaniswav/TopicForge/issues).
2. Tell me what other read-only introspection your AI agent would
   actually need to be useful — TF tree health, QoS diff, message-
   field schema, anything.
