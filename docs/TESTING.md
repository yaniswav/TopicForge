# Trying TopicForge against a real ROS2 environment

How to get a working ROS2 environment to point TopicForge at, and how to wire it into an MCP client. For the mock mode, the tool table and the configuration reference, see the [README](../README.md); for errors, see [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md). If a step here does not work, [open an issue](https://github.com/yaniswav/TopicForge/issues).

| You want to... | Time | Path |
| --- | --- | --- |
| Try the twelve tools without installing ROS2 | 5 min | [Mock mode](#mock-mode) |
| Live mode on Windows | 45 min | [WSL2 + Humble](#wsl2--ros2-humble-windows-recommended) |
| Live mode on Ubuntu | 20 min | [Linux native](#linux-native) |
| Throwaway environment | 15 min | [Docker](#docker) |
| Native Windows ROS2, no virtualization | 1-2 h | [Windows native](#windows-native-advanced) |

## Mock mode

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install topicforge
python -m topicforge --version                      # -> topicforge 0.5.5
TOPICFORGE_MODE=mock python -m topicforge           # blocks on stdio; MCP clients spawn it
```

The mock graph has five topics modeling a small differential robot with LIDAR + RGB camera, deterministic across runs. To call the tools, configure a client as in [Connect an MCP client](#connect-an-mcp-client) with `"env": { "TOPICFORGE_MODE": "mock" }`.

## WSL2 + ROS2 Humble (Windows, recommended)

From an elevated PowerShell, `wsl --install -d Ubuntu-22.04`, reboot, set a Linux user. Then, inside the WSL shell:

```bash
sudo apt update && sudo apt install -y software-properties-common curl
sudo add-apt-repository universe -y
sudo curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
  -o /usr/share/keyrings/ros-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu $(. /etc/os-release && echo $UBUNTU_CODENAME) main" \
  | sudo tee /etc/apt/sources.list.d/ros2.list > /dev/null
sudo apt update
sudo apt install -y ros-humble-ros-base ros-humble-demo-nodes-cpp ros-humble-rosbag2-storage-mcap
echo "source /opt/ros/humble/setup.bash" >> ~/.bashrc && source ~/.bashrc

sudo apt install -y python3-pip python3-venv
python3 -m venv ~/topicforge-venv && source ~/topicforge-venv/bin/activate
pip install topicforge          # Ubuntu 22.04 ships Python 3.10, which is enough
```

Then run live mode with three terminals. In the first, `ros2 run demo_nodes_cpp talker` publishes `/chatter` at about 1 Hz. In the second, `ros2 topic list` should show `/chatter`. In the third, source ROS2, activate the venv and run `TOPICFORGE_MODE=live python -m topicforge`. The startup line reads `topicforge 0.5.5 ready (mode=live, requested_mode=live, adapter=ros2_cli, telemetry=off)`; `mode=mock` means `ros2` was not found and the server fell back to fixtures.

## Linux native

Same as the WSL steps from the apt repository onward. Tested on Ubuntu 22.04 (Humble) and 24.04 (Jazzy); for Jazzy replace `ros-humble-*` with `ros-jazzy-*` and source `/opt/ros/jazzy/setup.bash`.

## Docker

Run both the publisher and TopicForge inside the container, to avoid DDS multicast across the Docker network boundary.

```bash
docker run -it --rm osrf/ros:humble-desktop bash
# inside:
apt update && apt install -y python3-pip ros-humble-demo-nodes-cpp
pip install topicforge
ros2 run demo_nodes_cpp talker &
TOPICFORGE_MODE=live python3 -m topicforge
```

Connecting an MCP client from the host to a server inside a container is more friction than it is worth; use WSL or native Linux for that.

## Windows native (advanced)

The [ROS2 Humble binary install for Windows](https://docs.ros.org/en/humble/Installation/Windows-Install-Binary.html) works but is heavy (Visual Studio Build Tools, pinned Python 3.10, OpenSSL, `call C:\dev\ros2_humble\local_setup.bat` in every shell). In a shell where `setup.bat` is sourced:

```powershell
$env:TOPICFORGE_MODE = "live"
python -m topicforge
```

TopicForge resolves the `ros2` launcher with `shutil.which` (normally `ros2.exe`) and runs it by absolute path, never through a shell. `TOPICFORGE_ROS2_BIN` overrides the name or path.

## Test scenarios

Discover the graph. Ask "What topics are currently being published, and what message types do they carry?" TopicForge calls `list_topics`. Live mode shows `/chatter` plus `/rosout` and `/parameter_events`; mock mode shows `/cmd_vel`, `/odom`, `/scan`, `/tf` and `/camera/image_raw`.

Inspect and sample. Ask "Show me the latest message on /chatter." TopicForge calls `get_topic_info` then `sample_messages`. The payload exposes fields as positional CSV columns (`col_0`, `col_1`, ...) plus `_raw_text`, the verbatim row from `ros2 topic echo --csv --once`. `timestamp_ns` is `0` for headerless types such as `std_msgs/String`.

Record and analyze a bag.

```bash
ros2 bag record /chatter --output ~/demo_run --max-bag-duration 30
```

Then ask "Analyze the bag at /home/<you>/demo_run." `analyze_bag` returns duration, message count and per-topic stats; anomalies are mock-only. To read actual messages, install `topicforge[bags]` and ask for samples, which calls `peek_bag_samples`. A fourth scenario, DDS discovery and QoS diagnosis against a real bus, is in [`examples/dds/README.md`](../examples/dds/README.md).

## Connect an MCP client

TopicForge speaks MCP over stdio; any compliant client can spawn it with a command and env vars.

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

That is the Claude Desktop shape (`claude_desktop_config.json`); restart the app and the twelve tools appear under the hammer icon. For Claude Code run `claude mcp add topicforge -- topicforge`. Cursor, Continue and Cline accept the same stdio config. If the `topicforge` script is not on PATH, use `"command": "python", "args": ["-m", "topicforge"]`, or the absolute path of the binary inside your venv: desktop clients do not inherit your shell's PATH or venv activation.
