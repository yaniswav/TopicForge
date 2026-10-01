# TopicForge: Tutorial

TopicForge is a read-only Model Context Protocol (MCP) server that gives an AI agent grounded, structured visibility into a ROS2 robotics stack and the raw DDS layer beneath it (topics, participants, QoS, recorded bags), without ever being able to publish, call a service, or command a robot.

**Who this is for.** ROS2 developers, robotics ML/CV engineers, and anyone who wants their AI assistant to answer "what's actually happening on my robot's graph right now" instead of guessing from training data.

**The read-only guarantee, in one sentence.** There is no write path anywhere in TopicForge's architecture: not a locked-down permission you could misconfigure, but code that was never written, so there is nothing to flip and nothing to exploit into a write.

This tutorial covers installing TopicForge, using its eleven tools, and wiring it into a recurring monitoring workflow. For OS-by-OS environment setup (WSL2, native Linux, Docker, native Windows), see [`TESTING.md`](TESTING.md).

---

## Quickstart

Requires Python 3.10+. This section needs no ROS2 install: the mock adapter serves deterministic fixtures for a small demo robot, so you can try every tool cold.

```bash
pip install topicforge
```

Add it to your MCP client. For Claude Desktop, edit `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "topicforge": {
      "command": "topicforge",
      "env": { "TOPICFORGE_MODE": "mock" }
    }
  }
}
```

Restart Claude Desktop. All eleven tools appear under the hammer icon. Ask something like:

> What topics are being published right now, and what message types do they carry?

The agent calls `list_topics`. In mock mode you'll see the five fixture topics of the demo robot (`/cmd_vel`, `/odom`, `/scan`, `/tf`, `/camera/image_raw`), deterministic across runs, so you can build a mental model of the tool surface before pointing it at a real graph.

When you're ready for a real ROS2 environment, switch `TOPICFORGE_MODE` to `live` or `auto`: see [Advanced options](#advanced-options) below, and [`TESTING.md`](TESTING.md) for full setup paths per OS.

**Windows note.** File paths in this document are shown in POSIX style (`/tmp/demo.mcap`) because that's the form MCP clients pass internally, but TopicForge itself runs natively on Windows. Path resolution goes through `pathlib`, so both `C:\demos\run.mcap` and `C:/demos/run.mcap` work when you pass a bag path yourself.

---

## The eleven tools

| Tool | What it does | When to use it |
| --- | --- | --- |
| `health_check` | Reports the mode of the adapter actually serving requests (`mode`: `live`/`mock`, next to `requested_mode`), whether `ros2` is on PATH, the active DDS backend, and other environment state. Always succeeds. | Call first when something looks wrong: every other tool can raise an error, this one won't. |
| `list_topics` | Lists every topic on the current ROS2 graph. | Discover what's currently being published before drilling into anything specific. |
| `get_topic_info` | Structured detail for one topic: message type, publisher/subscriber counts, QoS reliability. | Check a topic's shape and who's connected to it before subscribing or debugging. |
| `sample_messages` | Peeks recent messages on a ROS2 topic. | See real payload content without shelling out to `ros2 topic echo` yourself. |
| `analyze_bag` | Summarizes a `.mcap` / `.db3` / `.bag` recording: duration, message count, per-topic stats. | Get a quick overview of a recorded run before deciding whether to dig deeper. |
| `list_participants` | Lists DDS participants observed on a domain, at the raw DDS layer beneath ROS. | Diagnose why a participant isn't visible to the ROS graph, or inspect a non-ROS DDS stack. |
| `detect_qos_mismatches` | Finds incompatible QoS pairs between DDS readers and writers. | A subscriber isn't receiving despite an apparently healthy publisher: this tells you why. |
| `peek_dds_samples` | Peeks raw DDS samples. The three DCPS builtin discovery topics come back structured; a user topic is reported as present on the bus, with a placeholder sample and no decoded payload. | Inspect the discovery layer itself, or confirm that a non-ROS DDS topic is announced. |
| `participant_events` | Timeline of participant discovered/lost events over a lookback window. | Investigate churn: nodes restarting, dropping off, or new ones joining. |
| `topic_metrics` | Frequency, latency percentiles and sequence-gap fields for a topic over a time window. Data exists only for the builtin discovery topics, and the observed frequency reflects how often `peek_dds_samples` is called. | Watch discovery-layer activity. It does not measure an application topic's publish rate. |
| `peek_bag_samples` | Peeks recent samples from a recorded bag file (MCAP, `.db3`, or legacy ROS1 `.bag`). Needs `pip install topicforge[bags]`. | Inspect payload content from a past recording without replaying the whole bag. |

---

## Advanced options

### Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `TOPICFORGE_MODE` | `auto` | Selects `mock`, `live`, or `auto` (see below). |
| `TOPICFORGE_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, or `ERROR`. |
| `TOPICFORGE_ROS2_BIN` | `ros2` | Overrides the resolved `ros2` executable: useful when it isn't on PATH. |
| `TOPICFORGE_TELEMETRY` | off | Opt-in anonymous telemetry. On-values: `on`, `1`, `true`, `yes`, `enabled`; off-values: unset, `off`, `0`, `false`, `no`, `disabled`. Anything else aborts startup with a configuration error. See [Privacy](#privacy). |
| `TOPICFORGE_DDS_BACKEND` | `mock` | Selects the DDS backend: `mock`, `cyclone`, `fast`, or `auto`. `opendds` and `dust` are also accepted but are permanent stubs. `rti`, `opensplice`, `coredx` and `intercom` are rejected (removed in 0.5.3). See [Choosing a DDS backend](#choosing-a-dds-backend). |
| `TOPICFORGE_DDS_DOMAIN_ID` | `0` | DDS domain id to observe, `0`-`232`. |

Add any of these to the same `env` object shown in the Quickstart config block.

### The three modes

- **`mock`**: deterministic fixtures, no ROS2 or DDS SDK required. Use for development, demos, CI, or evaluating the tool surface before touching a real graph.
- **`live`**: talks to a real environment: the `ros2` CLI for the ROS2 graph and bag tools (`list_topics`, `get_topic_info`, `sample_messages`, `analyze_bag`, `peek_bag_samples`), and the configured DDS backend for the five DDS tools (`list_participants`, `detect_qos_mismatches`, `peek_dds_samples`, `participant_events`, `topic_metrics`). Use once ROS2 and/or a DDS backend are actually reachable from the shell that spawns TopicForge. If neither comes up, the server serves the mock fixtures and `health_check` shows `mode: "mock"` next to `requested_mode: "live"`.
- **`auto`** (default): resolves to `live` if the `ros2` executable is found on PATH, otherwise falls back to `mock`. An explicit `TOPICFORGE_DDS_BACKEND` (`cyclone`, `fast` or `auto`) overrides that fallback: it is honoured with or without `ros2`, so a host with a DDS binding but no ROS2 gets a DDS-only adapter instead of fixtures. Only `TOPICFORGE_MODE=mock` forces fixtures unconditionally. The DDS backend's own `auto` resolution is described below.

### Extras

Plain `pip install topicforge` gives you the ROS2 CLI tools plus mock fixtures for all eleven. To talk to a real DDS bus or to read bag contents, install one of:

```bash
pip install topicforge[dds-cyclone]   # Eclipse CycloneDDS
pip install topicforge[dds]           # the same thing today ([dds] resolves to [dds-cyclone])
pip install topicforge[all]           # equivalent to [dds]
pip install topicforge[bags]          # rosbags library: required for peek_bag_samples (no fallback)
```

`analyze_bag` does not use `rosbags`: in live mode it parses the output of `ros2 bag info`, with or without the `[bags]` extra. Only `peek_bag_samples` reads the bag file itself.

There is no Fast DDS extra. The `fastdds` Python binding is not published on PyPI, so 0.5.3 removed `[dds-fast]` (along with `[dds-opendds]`, `[dds-dust]` and `[dds-all-oss]`, whose packages do not resolve either). The Fast DDS adapter still works if you build eProsima's Python binding from source and install it next to TopicForge; see [`DDS_QUICKSTART.md`](DDS_QUICKSTART.md) section 2.b.

### Choosing a DDS backend

Set `TOPICFORGE_DDS_BACKEND` explicitly (`cyclone` or `fast`) if you know which binding you have, or set `auto` and let TopicForge probe: `auto` resolves to Fast DDS if its binding is importable, otherwise CycloneDDS if importable, otherwise `mock`. With only the PyPI extra installed, that is Cyclone. The default, when the variable is unset, is `mock`, which leaves the DDS tools to the mock adapter only.

An explicit backend is honoured with or without `ros2` on PATH. If the binding is missing or the participant cannot start, the server logs a warning that names the cause and falls back to the ROS2 CLI alone, or to mock when that is unavailable too. `opendds` and `dust` are recognized identifiers but permanent stubs that never serve. `rti`, `opensplice`, `coredx` and `intercom` were accepted until 0.5.2 for the retired Pro tier and now stop the server at startup with a configuration error; you do not need them to see an RTI bus, since a Cyclone participant discovers RTI participants through standard RTPS discovery (see [`pro.md`](pro.md)).

Keep in mind that no DDS adapter has been validated against a live multi-vendor bus yet; see [`DDS_QUICKSTART.md`](DDS_QUICKSTART.md).

### Domain id

`TOPICFORGE_DDS_DOMAIN_ID` (default `0`, matching the default used by most ROS2 setups and by `cyclonedds` itself) sets the domain a live DDS adapter joins **at startup**: it does not change per call. `list_participants`, `participant_events`, and `topic_metrics` also accept a `domain_id` parameter, but on the real Cyclone/Fast adapters this parameter is accepted for protocol uniformity only; the adapter still reports what it sees on the domain it joined at construction time, not a different domain picked per call. To observe a different domain, restart the server with a different `TOPICFORGE_DDS_DOMAIN_ID`.

---

## Scheduled-task prompts

TopicForge's tools are read-only, which makes them safe to run unattended on a schedule: a cron-triggered agent session, a scheduled task in whatever orchestrator you use, or a recurring reminder in your MCP client. The prompts below are generic: swap in your own topic names, domain ids, and bag paths. They're written as instructions to hand an agent, not as commands to run yourself.

**Catches:** silent reader/writer QoS incompatibilities that block delivery: introduced by a new node, a config change, or a vendor default drifting from the rest of the bus.

```
Run detect_qos_mismatches across the whole bus (no topic filter). For every
result with severity "incompatible", explain in plain language which QoS
policy is blocking delivery, which side (reader or writer) is the outlier,
and the smallest concrete change that would fix it (e.g. "relax the reader
to BEST_EFFORT" or "raise the writer to TRANSIENT_LOCAL durability"). List
any "risky" (non-blocking) mismatches separately as a lower-priority note.
If nothing is found, say so in one line: do not pad the report.
```

**Catches:** environment drift before you trust the stack in the field: wrong mode silently active, `ros2` not actually on PATH, DDS backend quietly falling back to mock.

```
Before I start today's run, call health_check and confirm: mode is "live"
(not silently "mock" while requested_mode says otherwise), ros2_available
is true, and dds_backend matches what I expect. Then call list_topics and
list_participants(domain_id=0), and tell me if the topic count or
participant count looks abnormally low compared to a normal startup.
Flag anything that looks like a partial or degraded environment before
I rely on it.
```

**Catches:** flapping nodes (crash-restart loops), unexpected disconnects mid-run, or a new participant joining the bus that nobody deployed on purpose.

```
Call participant_events(domain_id=0, lookback_seconds=3600) and summarize
the last hour of DDS participant activity. Group by guid: which
participants were discovered then lost more than once (possible crash
loop), which are new, and which have been stable the whole window. Call
out anything under a hostname or vendor you don't recognize.
```

**Catches:** an expected topic that is no longer announced on the bus (its publisher crashed or never started), or one that has writers but no readers, or the reverse.

```
These topics should exist on domain 0: <topic-1>, <topic-2>, <topic-3>.
Call peek_dds_samples(topic="DCPSPublication", count=50) and
peek_dds_samples(topic="DCPSSubscription", count=50), and read the
topic_name field of each sample. For every expected topic, say whether
at least one writer and at least one reader announce it. Flag any topic
with no writer, with no reader, or missing from both lists. If a call
returns exactly 50 samples, say the list may be truncated.
```

This prompt uses the discovery layer on purpose. `peek_dds_samples` does not decode user-topic payloads, and `topic_metrics` has no data for user topics, so TopicForge cannot tell you whether a topic is meeting its publish rate or whether its sequence numbers have gaps. It can tell you who announces what.

**Catches:** recorded test or field runs that only get inspected reactively after something breaks, instead of on a regular cadence: letting anomalies (duration mismatches, missing topics, gaps) surface while they're still cheap to investigate.

```
Analyze the bag at <path-to-bag>. Report duration, total message count,
and per-topic message counts. Then peek 5 samples from <topic-of-interest>
in that bag and tell me if the payload shape looks consistent with a
normal run. Flag anything anomalous: missing topics you'd expect to see,
a duration much shorter or longer than usual, or a topic with a
suspiciously low message count for its expected rate.
```

---

## Privacy

TopicForge is built so the question "what does this send off my machine?" has a short, verifiable answer.

### Telemetry

Telemetry is **off by default**. It's opt-in via `TOPICFORGE_TELEMETRY=on` (accepted on-values: `on`, `1`, `true`, `yes`, `enabled`). An unset variable, or `off`, `0`, `false`, `no`, `disabled`, keeps it off. Any other value is not silently treated as off: the server refuses to start and prints a configuration error, so a typo can't leave you with the wrong setting.

When enabled, each tool call emits exactly six fields: `tool_name`, `latency_ms`, `mode`, `version`, `session_id`, `success`. `session_id` is a random id generated per server process: it isn't tied to your identity or any persistent identifier, and it's never written to disk.

When disabled (the default), this isn't "we choose not to send it," it's "the code path doesn't exist." The instrumentation wrapper returns the tool handler unmodified: no event object is built, no transport is constructed, no network code executes. This no-op behavior is covered by a dedicated test in the codebase.

### What never leaves your machine

Regardless of the telemetry setting, TopicForge never transmits topic names, message payloads, bag file paths or contents, hostnames, or any other environment variable. The service and adapter layers that see this data have no code path into the telemetry module, by construction, not just by convention.

### Bags are read locally

`analyze_bag` and `peek_bag_samples` read bag files on the filesystem where TopicForge runs. `analyze_bag` runs `ros2 bag info` on the path in live mode; `peek_bag_samples` parses the file in-process with the `rosbags` library (`pip install topicforge[bags]`). Nothing about a bag's content or path is uploaded anywhere.

### Read-only, restated

The same architectural property that keeps TopicForge from commanding your robot also bounds what it can leak: there is no write path anywhere in the codebase (not to the bus, not to a remote endpoint) other than the six-field telemetry event described above, and that event stays off unless you explicitly turn it on.

---

## Where to go next

- [`DDS_QUICKSTART.md`](DDS_QUICKSTART.md): a deeper tour of the DDS module: mock vs. live walkthroughs per backend, the QoS mismatch scenario end-to-end, the composite-adapter routing table, and what `peek_dds_samples` and `topic_metrics` do and do not cover.
- [`TESTING.md`](TESTING.md): five paths to a working ROS2 environment (WSL2, native Linux, Docker, native Windows), plus MCP client wiring for Claude Desktop, Claude Code, and others.
- [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md): the codebase's polished error messages, what they mean, and how to fix them.
- [`dds-interop-matrix.md`](dds-interop-matrix.md): why TopicForge sees participants from any OMG-DDS-RTPS-conformant vendor, not just the one it's bound to.
