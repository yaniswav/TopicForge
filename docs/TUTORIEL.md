# TopicForge: Tutorial

TopicForge is a read-only Model Context Protocol (MCP) server that gives an AI agent grounded, structured visibility into a ROS2 robotics stack and the raw DDS layer beneath it (topics, participants, QoS, recorded bags), without ever being able to publish, call a service, or command a robot.

**Who this is for.** ROS2 developers, robotics ML/CV engineers, and anyone who wants their AI assistant to answer "what's actually happening on my robot's graph right now" instead of guessing from training data.

**The read-only guarantee, in one sentence.** There is no write path anywhere in TopicForge's architecture: not a locked-down permission you could misconfigure, but code that was never written, so there is nothing to flip and nothing to exploit into a write.

This tutorial covers installing TopicForge, using its eleven tools, and wiring it into a recurring monitoring workflow. For OS-by-OS environment setup (WSL2, native Linux, Docker, native Windows), see [`TESTING.md`](TESTING.md).

---

## Quickstart

Requires Python 3.11+. This section needs no ROS2 install: the mock adapter serves deterministic fixtures for a small demo robot, so you can try every tool cold.

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
| `health_check` | Reports effective mode (`live`/`mock`), whether `ros2` is on PATH, the active DDS backend, and other environment state. Always succeeds. | Call first when something looks wrong: every other tool can raise an error, this one won't. |
| `list_topics` | Lists every topic on the current ROS2 graph. | Discover what's currently being published before drilling into anything specific. |
| `get_topic_info` | Structured detail for one topic: message type, publisher/subscriber counts, QoS reliability. | Check a topic's shape and who's connected to it before subscribing or debugging. |
| `sample_messages` | Peeks recent messages on a ROS2 topic. | See real payload content without shelling out to `ros2 topic echo` yourself. |
| `analyze_bag` | Summarizes a `.mcap` / `.db3` / `.bag` recording: duration, message count, per-topic stats. | Get a quick overview of a recorded run before deciding whether to dig deeper. |
| `list_participants` | Lists DDS participants observed on a domain, at the raw DDS layer beneath ROS. | Diagnose why a participant isn't visible to the ROS graph, or inspect a non-ROS DDS stack. |
| `detect_qos_mismatches` | Finds incompatible QoS pairs between DDS readers and writers. | A subscriber isn't receiving despite an apparently healthy publisher: this tells you why. |
| `peek_dds_samples` | Peeks raw DDS samples, including the DCPS builtin discovery topics and (best-effort decoded) user topics. | Inspect non-ROS DDS topics, or the discovery layer itself. |
| `participant_events` | Timeline of participant discovered/lost events over a lookback window. | Investigate churn: nodes restarting, dropping off, or new ones joining. |
| `topic_metrics` | Observed frequency, latency percentiles, and sequence gaps for a topic over a time window. | Check whether a topic is actually meeting its declared publish rate or QoS Deadline. |
| `peek_bag_samples` | Peeks recent samples from a recorded bag file (MCAP, `.db3`, or legacy ROS1 `.bag`). | Inspect payload content from a past recording without replaying the whole bag. |

---

## Advanced options

### Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `TOPICFORGE_MODE` | `auto` | Selects `mock`, `live`, or `auto` (see below). |
| `TOPICFORGE_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, or `ERROR`. |
| `TOPICFORGE_ROS2_BIN` | `ros2` | Overrides the resolved `ros2` executable: useful when it isn't on PATH. |
| `TOPICFORGE_TELEMETRY` | off | Opt-in anonymous telemetry. On-values: `on`, `1`, `true`, `yes`, `enabled`. See [Privacy](#privacy). |
| `TOPICFORGE_DDS_BACKEND` | `mock` | Selects the DDS backend: `mock`, `cyclone`, `fast`, or `auto`. A few more identifiers exist in the settings schema (`rti`, `opensplice`, `coredx`, `intercom`, `opendds`, `dust`): see [Choosing a DDS backend](#choosing-a-dds-backend). |
| `TOPICFORGE_DDS_DOMAIN_ID` | `0` | DDS domain id to observe, `0`-`232`. |

Add any of these to the same `env` object shown in the Quickstart config block.

### The three modes

- **`mock`**: deterministic fixtures, no ROS2 or DDS SDK required. Use for development, demos, CI, or evaluating the tool surface before touching a real graph.
- **`live`**: talks to a real environment: the `ros2` CLI for the 5 ROS2 graph tools, and the configured DDS backend for the 6 DDS/observability tools. Use once ROS2 and/or a DDS backend are actually reachable from the shell that spawns TopicForge.
- **`auto`** (default): resolves to `live` if the `ros2` executable is found on PATH, otherwise falls back to `mock`. The DDS backend has its own `auto` resolution (see below), independent of `TOPICFORGE_MODE`.

### DDS extras, per vendor

Plain `pip install topicforge` gives you the 5 ROS2 tools plus mock fixtures for all eleven. To talk to a real DDS bus, install one of:

```bash
pip install topicforge[dds-cyclone]   # Eclipse CycloneDDS only
pip install topicforge[dds-fast]      # eProsima Fast DDS only
pip install topicforge[dds]           # both: the recommended default
pip install topicforge[all]           # currently equivalent to [dds]
pip install topicforge[bags]          # rosbags library: required for peek_bag_samples (no fallback);
                                       # analyze_bag falls back to `ros2 bag info` text parsing without it
```

`topicforge[dds-opendds]` and `topicforge[dds-dust]` also exist in `pyproject.toml`, but as of this writing they pin `pyopendds` and `dust-dds-python`: packages not currently maintained on PyPI. Installing either extra fails at `pip install` time; they exist so the auto-detect framework has a known module name to probe once upstream ships a working release. Don't rely on them yet.

### Choosing a DDS backend

Set `TOPICFORGE_DDS_BACKEND` explicitly (`cyclone` or `fast`) if you have a preference and both are installed, or leave it on `auto` and let TopicForge pick. In practice, for the free tier `auto` resolves to Fast DDS if installed, otherwise CycloneDDS if installed, otherwise `mock`. The full priority chain also probes Pro-tier vendors (`rti`, `opensplice`, `coredx`, `intercom`) first, but only if you've separately installed the `topicforge-pro` add-on: without it, those probes are skipped automatically and fall through to the OSS vendors. Selecting `rti` explicitly without the Pro add-on falls back to the ROS2-CLI-only adapter with a logged warning rather than failing outright. `opendds` and `dust` are recognized identifiers with no working install path today (see the extras caveat above).

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
Before I start today's run, call health_check and confirm: the effective
mode is "live" (not silently "mock"), the ros2 CLI is detected, and the
active DDS backend matches what I expect. Then call list_topics and
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

**Catches:** a critical topic silently dropping below its declared publish rate, latency creeping up, or sequence gaps appearing: degradation that doesn't throw an error but breaks downstream consumers.

```
For each of these topics: <topic-1>, <topic-2>, <topic-3>, first call
peek_dds_samples(topic=<topic>, count=10) to warm up the metrics buffer,
then call topic_metrics(topic=<topic>, window_seconds=60) and report
frequency_hz_observed against frequency_hz_declared, the p50/p95/p99
latency, and sequence_gaps_count. Flag any topic where the observed
frequency is more than 20% below the declared rate, or where
sequence_gaps_count is greater than zero.
```

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

Telemetry is **off by default**. It's opt-in via `TOPICFORGE_TELEMETRY=on` (accepted on-values: `on`, `1`, `true`, `yes`, `enabled`; anything else, including an unset variable, stays off).

When enabled, each tool call emits exactly six fields: `tool_name`, `latency_ms`, `mode`, `version`, `session_id`, `success`. `session_id` is a random id generated per server process: it isn't tied to your identity or any persistent identifier, and it's never written to disk.

When disabled (the default), this isn't "we choose not to send it," it's "the code path doesn't exist." The instrumentation wrapper returns the tool handler unmodified: no event object is built, no transport is constructed, no network code executes. This no-op behavior is covered by a dedicated test in the codebase.

### What never leaves your machine

Regardless of the telemetry setting, TopicForge never transmits topic names, message payloads, bag file paths or contents, hostnames, or any other environment variable. The service and adapter layers that see this data have no code path into the telemetry module, by construction, not just by convention.

### Bags are read locally

`analyze_bag` and `peek_bag_samples` open bag files on the filesystem where TopicForge runs and parse them in-process: via `ros2 bag info` in live mode, or the `rosbags` library when installed (`pip install topicforge[bags]`). Nothing about a bag's content or path is uploaded anywhere.

### Read-only, restated

The same architectural property that keeps TopicForge from commanding your robot also bounds what it can leak: there is no write path anywhere in the codebase (not to the bus, not to a remote endpoint) other than the six-field telemetry event described above, and that event stays off unless you explicitly turn it on.

---

## Where to go next

- [`DDS_QUICKSTART.md`](DDS_QUICKSTART.md): a deeper tour of the DDS module: mock vs. live walkthroughs per backend, the QoS mismatch scenario end-to-end, and the composite-adapter routing table.
- [`TESTING.md`](TESTING.md): five paths to a working ROS2 environment (WSL2, native Linux, Docker, native Windows), plus MCP client wiring for Claude Desktop, Claude Code, and others.
- [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md): the codebase's polished error messages, what they mean, and how to fix them.
- [`dds-interop-matrix.md`](dds-interop-matrix.md): why TopicForge sees participants from any OMG-DDS-RTPS-conformant vendor, not just the one it's bound to.
