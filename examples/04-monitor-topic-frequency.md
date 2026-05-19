# 04 — Monitor topic frequency and participant lifecycle

**Scenario.** A robotics integrator complains that a 10 Hz heartbeat
topic "seems jittery". You want concrete numbers — observed
frequency, sequence gaps, latency percentiles — plus visibility on
participants joining and leaving the bus during the observation
window. **Tools exercised.** `topic_metrics`, `participant_events`.
**Mode.** Mock — exercises the deterministic 10 Hz heartbeat fixture.

## Setup

```bash
pip install topicforge
TOPICFORGE_MODE=mock python -m topicforge
```

The mock observability fixture ships :

- `/dds/heartbeat_10hz` — 100 samples spaced 100 ms apart with
  synthetic 50 ms latency and contiguous sequence numbers 0..99
- A singleton topic (one sample) for low-traffic edge-case testing
- A cross-domain topic exercising the `domain_id` filter

## Conversation

**You:**

> Tell me the observed frequency, jitter, and any sequence gaps on
> `/dds/heartbeat_10hz` over the last minute.

**Claude calls:** `topic_metrics(topic="/dds/heartbeat_10hz",
window_seconds=60, domain_id=0)` → returns `TopicMetrics` with
`samples_observed=100`, `frequency_hz_observed≈10.0`,
`frequency_hz_declared=10.0` (from QoS Deadline),
`sequence_gaps_count=0`, `latency_ns_p50≈50_000_000`,
`latency_ns_p95≈50_000_000`, `latency_ns_p99≈50_000_000`, and
boolean availability flags for each conditional metric.

**You:**

> Did any participants come or go on the bus during that window?

**Claude calls:** `participant_events(domain_id=0,
lookback_seconds=60)` → returns a list of `ParticipantEvent` with
`event_type="discovered"` or `"lost"`, `timestamp_ns`, and the full
embedded `ParticipantInfo` (vendor, hostname, GUID, lifecycle
fields).

**Claude responds** with a synthesis :

> Over the last 60 seconds I observed 100 samples on
> `/dds/heartbeat_10hz`, exactly matching the declared 10 Hz QoS
> Deadline. Sequence numbers were contiguous (0 gaps), and end-to-end
> latency p50/p95/p99 all sat at ~50 ms — uniform, no jitter.
> Three participants stayed active throughout the window — no
> discovery or loss events fired. The "jittery heartbeat" report
> isn't reproducing here — recommend checking the **complainant's
> subscriber** end (publisher → bus → subscriber is fine ;
> subscriber → application callback might be the bottleneck).

## Caveats — opportunistic fill

**`topic_metrics` is opportunistic.** Neither `cyclonedds` nor
`fastdds` 2.6.x Python bindings expose at-sample-receive callbacks,
so the `MetricsBuffer` accumulates ONLY as `peek_dds_samples` is
exercised on the same topic. In a production observation loop you'd
periodically call `peek_dds_samples` to keep the buffer warm. The
tool description carries this caveat to the LLM ; mock mode hides it
(deterministic fixtures pre-populate the buffer).

**`participant_events` on Cyclone is polling-driven.** Cyclone's
lifecycle log updates only when `list_participants` (or any internal
poll of the `DCPSParticipant` builtin reader) is called. A
participant that joined and left between two tool calls is invisible.
Fast DDS uses native listener callbacks and captures both arrival
and removal natively.

## Going live

```bash
pip install topicforge[dds-fast]    # listener-callback-driven lifecycle
TOPICFORGE_MODE=live TOPICFORGE_DDS_BACKEND=fast python -m topicforge
```

On Fast DDS, `participant_events` captures every `discovered` /
`lost` event natively. On Cyclone, periodic `list_participants`
calls keep the lifecycle buffer warm. Either backend serves
`topic_metrics` identically — the buffer is vendor-neutral.
