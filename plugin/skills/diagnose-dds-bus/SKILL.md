---
name: diagnose-dds-bus
description: Diagnose a DDS or ROS 2 bus with TopicForge when nodes do not talk, a topic gets no data, or a node crashed or restarts. Read-only.
---

# Diagnose a DDS bus

Use the TopicForge tools. They only observe discovery; nothing here publishes, commands or changes QoS. Never suggest doing that through TopicForge.

## Call order

1. `health_check`. If `dds_backend` is `mock` or `none`, say so: results are fixtures or unavailable, not the real bus. Note `dds_domain_id` (only that domain is seen) and `observer_started_ns` (nothing earlier was observed). Tracker errors above 0 mean gaps.
2. `list_participants`. Is each expected node present, with the right `name`? A missing one may be on another domain or have crashed.
3. `list_endpoints` with `topic` set to the failing topic (`scan` and `rt/scan` match each other). Check `by_topic`: `orphan` is `no_reader` or `no_writer`, and `departed_writers` / `departed_readers` name a peer that left.
4. `detect_qos_mismatches` with the same `topic`.
5. `participant_events` for crashes or restarts. Use a short `lookback_seconds`.

Stop as soon as one step explains the symptom.

## Reading the result

Read `not_matched` before `reports`. Partition or type-name splits are checked first, and QoS rules are not run on those pairs. Then read `reports`: each gives requested vs offered values and the failed rule. Then `hints` (probable topic typos). Details and examples: `references/reading-results.md`.

## What TopicForge cannot see

- Whether data actually flows. An empty `reports` does not prove the bus is healthy.
- A writer that is alive but silent or hung. User-topic payloads are not decoded.
- Other DDS domains. DDS Security: protected endpoints and data stay hidden.
- Runtime behavior such as missed deadlines. Only declared QoS is visible.

## Answering

State the finding, the evidence (tool, field, value), and the fix as a change to the user's own code or config. When the evidence is partial, say what is unknown and what the user could check next. Do not claim a cause that discovery cannot show.
