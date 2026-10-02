# Troubleshooting TopicForge

Common errors and what to do about them. Each section is titled with a substring of the error text, so you can search for it. If your situation is not covered, open an issue at [github.com/yaniswav/TopicForge/issues](https://github.com/yaniswav/TopicForge/issues).

## "topicforge: configuration error: ..." at startup

The server prints one line to stderr and exits with code 2, so your MCP client reports that it failed to start. One of the `TOPICFORGE_*` variables holds a value the settings loader rejects; it is deliberately strict so a typo cannot silently change behaviour. The common cases:

- `Invalid TOPICFORGE_TELEMETRY=...`: only `on`, `1`, `true`, `yes`, `enabled` turn telemetry on, and only unset, `off`, `0`, `false`, `no`, `disabled` turn it off.
- `TOPICFORGE_DDS_BACKEND='rti' was removed ...` (also `opensplice`, `coredx`, `intercom`): the retired Pro tier values. Use `cyclone`, `fast` or `auto`; a Cyclone participant still discovers RTI, CoreDX and OpenSplice participants through standard RTPS discovery.
- `Invalid TOPICFORGE_MODE`, `TOPICFORGE_LOG_LEVEL`, `TOPICFORGE_DDS_DOMAIN_ID` (an integer in `0..232`).

Fix the variable in the `env` block of your MCP client configuration (or in the shell that spawns the server) and restart the client. Desktop clients show the stderr line in their MCP log.

## `health_check` says `mode: "mock"` but I asked for `live`

`mode` is the mode of the adapter actually built, `requested_mode` is what you asked for. `live` or `auto` ending on `mock` means neither `ros2` nor a DDS backend came up, and the server fell back to fixtures; the server log carries the matching warning. Check from the **same shell** that spawns TopicForge (desktop MCP clients do not inherit your PATH or venv activation):

```bash
which ros2          # Linux / WSL: /opt/ros/<distro>/bin/ros2
where.exe ros2      # Windows: the ros2 launcher, normally ros2.exe
```

If `ros2` is missing, source the setup file in the parent shell before launching the MCP client, or point at the binary with `TOPICFORGE_ROS2_BIN=/opt/ros/humble/bin/ros2`. TopicForge resolves the executable with `shutil.which` and runs it by absolute path, never through a shell. If you selected a DDS backend explicitly (`cyclone`, `fast`), the server does not fall back to mock without `ros2`: it serves the DDS tools through a DDS-only adapter, see the next section.

## "DDS observability only"

You called a ROS2 graph or bag tool (`list_topics`, `get_topic_info`, `sample_messages`, `analyze_bag`, `peek_bag_samples`) but only a DDS adapter is active in this process. The composite adapter, which serves both halves, only engages when both the `ros2` CLI and the chosen DDS backend come up.

1. Confirm `ros2 --help` works in the shell that spawns TopicForge (source `/opt/ros/<distro>/setup.bash`, or `call C:\dev\ros2_humble\local_setup.bat` on native Windows).
2. Re-run with `TOPICFORGE_MODE=live` and your existing `TOPICFORGE_DDS_BACKEND`.
3. Check `health_check`: `ros_backend` should be `"ros2_cli"` and `dds_backend` your vendor.

If you want a DDS-only deployment, the message is expected: you use the seven DDS tools and the two bag tools raise it. For offline work use `TOPICFORGE_MODE=mock`. The opposite error, "DDS module is not active", means `TOPICFORGE_DDS_BACKEND` is `mock` (the default) while a live adapter serves; set it to `cyclone`.

## "rosbags"

`peek_bag_samples` needs the optional `rosbags` library: `pip install topicforge[bags]`, then retry, no env change needed. `analyze_bag` does not raise this error, since in live mode it parses `ros2 bag info` and never touches `rosbags`; the error text mentions an `analyze_bag` fallback that does not exist. `peek_bag_samples` is served only by the ROS2 CLI adapter and the mock. Without `ros2` and without a DDS backend you are on the mock and get fixture samples, not the content of your file: check `health_check` first.

## "CycloneDDS participant discovery failed"

The message carries the underlying exception type and text. In order:

1. **Domain mismatch.** TopicForge joins `TOPICFORGE_DDS_DOMAIN_ID` (default `0`); your publishers must be on the same domain (`echo $ROS_DOMAIN_ID`).
2. **Firewall or multicast.** RTPS discovery uses multicast by default; corporate Wi-Fi and firewalls often block it and discovery then times out silently. Allow multicast on the interface, or configure unicast discovery through `CYCLONEDDS_URI`.
3. **`CYCLONEDDS_URI` misconfigured.** It must point to a readable XML file; `unset CYCLONEDDS_URI` to rule it out.

A `Timeout` points at 1 or 2; a `FileNotFoundError` or `OSError` points at 3.

## "Fast DDS DomainParticipant creation returned None"

Almost always an ABI mismatch between the Python `fastdds` binding and the native Fast DDS libraries on the host. Ignore the "pin and reinstall `fastdds`" part of the message: the binding is not on PyPI and you should not `pip install fastdds`, because whatever a registry returns under that name is not eProsima's package. Instead:

1. Rebuild [eProsima/Fast-DDS-python](https://github.com/eProsima/Fast-DDS-python) against the same Fast DDS native libraries installed on the host and install it into the TopicForge environment.
2. If you set `FastDDS_DEFAULT_PROFILES_FILE`, unset it: a broken profile XML causes the same symptom.
3. If several native installs exist, make sure `LD_LIBRARY_PATH` (or `PATH` on Windows) points at the one the binding was built against.

The adapter was written against the 2.6.x binding and has never run against a bus. If the binding cannot be imported at all, the server does not fail: it logs a warning and falls back to the ROS2 CLI alone, or to mock.

## Argument errors

- `domain_id must be in 0..232`: DDS spec range; `0` is the ROS2 default.
- `lookback_seconds must be in 1..86400` (`participant_events`): default 300. The lifecycle buffer keeps at most 200 events, newest first, regardless of window.
- `window_seconds must be in 1..3600` (`topic_metrics`): default 60. The buffer holds 1000 samples per topic, drop-oldest, so frequency is computed from buffered samples, not a true rolling window.
- `DDS topic name is malformed`: DDS names match `^[A-Za-z_/][A-Za-z0-9_/:]*$` (builtin names like `DCPSParticipant` and `::` separators are allowed; whitespace and dashes are not). ROS2 names start with `/` and use `_`, so `my-topic` becomes `/my_topic`.
- `sample_messages` and `peek_*` silently clamp `count` to 50; the `count` field of the result is what was actually returned.

## Other

- **`topicforge: command not found`**: the entry point is not on PATH. Re-activate the venv, or use `python -m topicforge`.
- **`sample_messages` returns no samples**: live mode runs `ros2 topic echo --once` with a 3 second timeout; a topic with no active publisher returns nothing. Check `ros2 topic info -v <topic>`.
- **`analyze_bag` says the path does not exist**: the path is resolved in the shell where TopicForge runs. Under WSL use `/mnt/c/demos/run.mcap`, not `C:\demos\run.mcap`. In mock mode only `.mcap`, `.db3`, `.bag` or extensionless paths are accepted.
- **Claude Desktop shows no tools**: check Help -> View Logs -> MCP, confirm `topicforge --version` runs from the same environment (or use the absolute path of the binary in the config), and validate the config with `python -m json.tool claude_desktop_config.json`, since a JSON error silently drops the whole file.
- **First live call is slow**: `ros2 topic list -t` initializes the DDS middleware, a 1-2 second warm-up.
- **A `peek_dds_samples` result with `_decode_status="raw"` and empty `_raw_bytes_hex`** is the user-topic placeholder: the topic is on the bus, the payload is not decoded.

See also: [README](../README.md), [`DDS_QUICKSTART.md`](DDS_QUICKSTART.md), [`TESTING.md`](TESTING.md).
