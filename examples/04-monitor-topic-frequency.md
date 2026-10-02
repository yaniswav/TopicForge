# 04: Topic metrics and participant lifecycle

A robotics integrator complains that a 10 Hz heartbeat topic "seems jittery".
You want numbers (observed frequency, sequence gaps, latency percentiles) and
visibility on participants joining and leaving the bus. This walkthrough uses
`topic_metrics` and `participant_events`, against a pre-filled 10 Hz mock
fixture.

The mock fixture is richer than anything a live adapter can produce today. On a live bus `topic_metrics` only has data
for the builtin discovery topics, and its frequency is the cadence of
your own `peek_dds_samples` calls, not the publication rate of a topic.
The jitter question in this scenario cannot be answered with TopicForge
on a real bus; the last section says what can. The mock run is useful to
see the response shape, not to learn what your robot is doing.

## Setup

```bash
pip install topicforge
TOPICFORGE_MODE=mock python -m topicforge
```

The mock observability fixture contains:

- `/dds/heartbeat_10hz`: 100 samples spaced 100 ms apart with
  synthetic 50 ms latency and contiguous sequence numbers 0..99
- `/dds/singleton`: one sample, for the "fewer than 2 samples" edge case
- `/dds/cross_domain`: one sample on domain 42, exercising the
  `domain_id` filter

## Conversation

You:

> Tell me the observed frequency and any sequence gaps on
> `/dds/heartbeat_10hz` over the last minute.

Claude calls: `topic_metrics(topic="/dds/heartbeat_10hz",
window_seconds=60, domain_id=0)` -> returns a `TopicMetrics`:

```json
{
  "topic": "/dds/heartbeat_10hz",
  "window_seconds": 60,
  "window_seconds_actual": 10.0,
  "samples_observed": 100,
  "frequency_hz_observed": 10.0,
  "frequency_hz_declared": null,
  "sequence_gaps_count": 0,
  "sequence_numbers_available": true,
  "latency_ns_p50": 50000000,
  "latency_ns_p95": 50000000,
  "latency_ns_p99": 50000000,
  "latency_available": true,
  "mode_effective": "mock"
}
```

`frequency_hz_observed` is 99 intervals over the 9.9 s span of the
samples, so 10.0 up to floating-point rounding. `frequency_hz_declared`
is `null` here, as it is in every response: no adapter populates it, so
there is nothing to compare the observed rate against.

You:

> Did any participants come or go on the bus?

Claude calls: `participant_events(domain_id=0,
lookback_seconds=300)` -> returns 4 `ParticipantEvent` entries, newest
first, all `event_type="discovered"` and none `"lost"`: the Dust DDS
participant `mock-rust-node`, the Fast DDS participant
`mock-aerospace-node`, then the Cyclone participants `mock-laptop` and
`mock-robot`. Each carries `guid`, `event_type`,
`vendor`, `timestamp_ns`, `hostname`, `domain_id` and `mode_effective`.
(The mock clock sits two minutes after the first event, so
`lookback_seconds=60` returns an empty list and `300` returns all four.)

Claude answers:

> The mock buffer holds 100 samples on `/dds/heartbeat_10hz` at about
> 10 Hz, with contiguous sequence numbers (0 gaps) and a uniform 50 ms
> publish-to-receive latency, so there is no jitter in this data. I
> can't check the rate against a declared one, because
> `frequency_hz_declared` is null. No participant was lost in the
> window. This is fixture data: it says nothing about a real robot.

## What happens on a live bus

`topic_metrics` only has data for the builtin topics. The buffer is filled
when `peek_dds_samples` surfaces samples, neither binding exposes an
at-receive callback, and only the builtin DCPS topics (`DCPSParticipant`,
`DCPSSubscription`, `DCPSPublication`) surface any.
Since 0.5.3 a user topic returns `samples_observed=0` with every metric
`null`; before that, the Fast adapter counted placeholder samples it had
made up. On a builtin topic:

- `frequency_hz_observed` reflects how often you call
  `peek_dds_samples`. Samples from one call share one capture instant,
  so a single call gives `null`.
- `sequence_numbers_available` is `false` and `sequence_gaps_count` is
  `0`: builtin samples carry no application sequence number.
- `latency_ns_p50/p95/p99` are `null` and `latency_available` is `false`:
  builtin samples carry no publish timestamp.

`participant_events` works on live adapters. On Cyclone a background thread
reads the builtin discovery topics every 0.5 s, so the timeline does not depend
on when you call it; a participant that cycles faster than the discovery
history depth between two passes can still be missed. Fast DDS uses listener
callbacks and captures both arrival and removal, but that adapter has never
run against a live bus.

```bash
pip install topicforge[dds-cyclone]
TOPICFORGE_DDS_BACKEND=cyclone python -m topicforge
```

The Fast DDS adapter (`TOPICFORGE_DDS_BACKEND=fast`) needs a Python
binding built from eProsima's sources; there is no PyPI extra for it.

## What to do about the jittery heartbeat

Inside TopicForge, `participant_events` can tell you whether the
publisher's participant is flapping, and `list_participants` plus
`detect_qos_mismatches` can tell you whether a QoS mismatch (Deadline
included) is in play. Measuring the topic's real rate and jitter needs a
tool that subscribes to the data: `ros2 topic hz` on a ROS2 graph, or
your DDS vendor's own tooling. TopicForge does not subscribe to
user-topic data.
