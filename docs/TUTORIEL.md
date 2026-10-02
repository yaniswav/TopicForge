# TopicForge: Tutorial

TopicForge is a read-only Model Context Protocol (MCP) server that gives an AI agent grounded, structured visibility into a ROS2 robotics stack and the raw DDS layer beneath it (topics, participants, QoS, recorded bags), without ever being able to publish, call a service, or command a robot.

This is for ROS2 developers, robotics ML/CV engineers, and anyone who wants their AI assistant to answer "what's actually happening on my robot's graph right now" instead of guessing from training data.

There is no write path anywhere in TopicForge's architecture. Read-only is not a locked-down permission you could misconfigure; the write code was never written, so there is nothing to flip and nothing to exploit into a write.

This tutorial covers a first run, recurring monitoring prompts, and the privacy contract. The tool list, modes and environment variables are in the [README](../README.md). For OS-by-OS environment setup (WSL2, native Linux, Docker, native Windows), see [`TESTING.md`](TESTING.md).

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

Restart Claude Desktop. All twelve tools appear under the hammer icon. Ask something like:

> What topics are being published right now, and what message types do they carry?

The agent calls `list_topics`. In mock mode you'll see the five fixture topics of the demo robot (`/cmd_vel`, `/odom`, `/scan`, `/tf`, `/camera/image_raw`), deterministic across runs, which lets you learn the tool surface before pointing it at a real graph.

When you're ready for a real ROS2 environment, switch `TOPICFORGE_MODE` to `live` or `auto`: see the [README](../README.md) configuration section, and [`TESTING.md`](TESTING.md) for full setup paths per OS.

Windows note. File paths in this document are shown in POSIX style (`/tmp/demo.mcap`) because that's the form MCP clients pass internally, but TopicForge itself runs natively on Windows. Path resolution goes through `pathlib`, so both `C:\demos\run.mcap` and `C:/demos/run.mcap` work when you pass a bag path yourself.

---

## Scheduled-task prompts

TopicForge's tools are read-only, which makes them safe to run unattended on a schedule: a cron-triggered agent session, a scheduled task in whatever orchestrator you use, or a recurring reminder in your MCP client. The prompts below are generic: swap in your own topic names, domain ids, and bag paths. They're written as instructions to hand an agent, not as commands to run yourself.

Catches silent reader/writer QoS incompatibilities that block delivery, introduced by a new node, a config change, or a vendor default drifting from the rest of the bus.

```
Run detect_qos_mismatches across the whole bus (no topic filter). For every
result with severity "incompatible", explain in plain language which QoS
policy is blocking delivery, which side (reader or writer) is the outlier,
and the smallest concrete change that would fix it (e.g. "relax the reader
to BEST_EFFORT" or "raise the writer to TRANSIENT_LOCAL durability"). List
any "risky" (non-blocking) mismatches separately as a lower-priority note.
If nothing is found, say so in one line.
```

Catches environment drift before you trust the stack in the field: the wrong mode active, `ros2` not on PATH, a DDS backend that fell back to mock.

```
Before I start today's run, call health_check and confirm: mode is "live"
(not silently "mock" while requested_mode says otherwise), ros2_available
is true, and dds_backend matches what I expect. Then call list_topics and
list_participants(domain_id=0), and tell me if the topic count or
participant count looks abnormally low compared to a normal startup.
Flag anything that looks like a partial or degraded environment before
I rely on it.
```

Catches flapping nodes (crash-restart loops), unexpected disconnects mid-run, or a new participant on the bus that nobody deployed on purpose.

```
Call participant_events(domain_id=0, lookback_seconds=3600) and summarize
the last hour of DDS participant activity. Group by guid: which
participants were discovered then lost more than once (possible crash
loop), which are new, and which have been stable the whole window. Call
out anything under a hostname or vendor you don't recognize.
```

Catches an expected topic that is no longer announced on the bus (its publisher crashed or never started), or one that has writers but no readers, or the reverse.

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

Catches problems in recorded test or field runs that would otherwise be inspected only after something breaks: duration mismatches, missing topics, gaps.

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

Telemetry is off by default. It's opt-in via `TOPICFORGE_TELEMETRY=on` (accepted on-values: `on`, `1`, `true`, `yes`, `enabled`). An unset variable, or `off`, `0`, `false`, `no`, `disabled`, keeps it off. Any other value is not silently treated as off: the server refuses to start and prints a configuration error, so a typo can't leave you with the wrong setting.

When enabled, each tool call emits exactly six fields: `tool_name`, `latency_ms`, `mode`, `version`, `session_id`, `success`. `session_id` is a random id generated per server process: it isn't tied to your identity or any persistent identifier, and it's never written to disk.

When disabled (the default), the code path does not exist. The instrumentation wrapper returns the tool handler unmodified: no event object is built, no transport is constructed, no network code executes. This no-op behavior is covered by a dedicated test in the codebase.

### What never leaves your machine

Regardless of the telemetry setting, TopicForge never transmits topic names, message payloads, bag file paths or contents, hostnames, or any other environment variable. The service and adapter layers that see this data have no code path into the telemetry module.

### Bags are read locally

`analyze_bag` and `peek_bag_samples` read bag files on the filesystem where TopicForge runs. `analyze_bag` runs `ros2 bag info` on the path in live mode; `peek_bag_samples` parses the file in-process with the `rosbags` library (`pip install topicforge[bags]`). Nothing about a bag's content or path is uploaded anywhere.

---

## Where to go next

- [`DDS_QUICKSTART.md`](DDS_QUICKSTART.md): a tour of the DDS module: mock and live walkthroughs per backend, the QoS mismatch scenario, the composite-adapter routing table, and what `peek_dds_samples` and `topic_metrics` do and do not cover.
- [`TESTING.md`](TESTING.md): five paths to a working ROS2 environment (WSL2, native Linux, Docker, native Windows), plus MCP client wiring for Claude Desktop, Claude Code, and others.
- [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md): the error messages, what they mean, and how to fix them.
- [`dds-interop-matrix.md`](dds-interop-matrix.md): why TopicForge sees participants from any OMG-DDS-RTPS-conformant vendor, not just the one it's bound to.
