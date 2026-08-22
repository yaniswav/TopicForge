# 02: Debug a QoS mismatch on the DDS bus

**Scenario.** A subscriber reports "no data" but `ros2 topic list` shows
the topic and a publisher. Classic DDS QoS incompatibility. **Tools
exercised.** `list_participants`, `detect_qos_mismatches`,
`peek_dds_samples`. **Mode.** Mock: exercises the canonical fixture.

## Setup

```bash
pip install topicforge
TOPICFORGE_MODE=mock python -m topicforge
```

The mock DDS fixture ships three deterministic participants
(two Cyclone-tagged, one Fast-tagged) on domain `0`, with two topics :
`/dds/well_matched` (compatible QoS) and `/dds/qos_mismatch`
(deliberate Reliability incompatibility ; RELIABLE reader vs
BEST_EFFORT writer).

## Conversation

**You:**

> Who is on the DDS bus right now, and is there a topic where readers
> and writers don't agree on QoS?

**Claude calls:** `list_participants(domain_id=0)` -> returns 3
participants with `vendor`, `hostname`, `guid`, lifecycle fields
(`first_seen_ns`, `status="active"`, `seen_count`). The
multi-vendor mix exercises the OMG-DDS-RTPS protocol guarantee in
fixture form. Then `detect_qos_mismatches(topic=None)` -> returns 1
`MismatchReport` for `/dds/qos_mismatch` with
`incompatible_policies=["Reliability"]` and
`severity="incompatible"`.

**You:**

> What policy is wrong, and what should I change?

**Claude responds** with the canonical fix narrative :

> The `/dds/qos_mismatch` topic has a reader requesting
> `Reliability: RELIABLE` and a writer offering
> `Reliability: BEST_EFFORT`. By the OMG-DDS-RTPS Request-vs-Offered
> rule, BEST_EFFORT writers cannot satisfy RELIABLE readers: the
> match is rejected at discovery and no samples flow. Two fixes :
>
> 1. Relax the reader to `BEST_EFFORT` (if data loss is acceptable).
> 2. Upgrade the writer to `RELIABLE` (if the data is critical).
>
> The other topic `/dds/well_matched` has no mismatch reported, so
> the same diagnostic loop would not fire there.

**You:**

> Peek what's actually flowing on `/dds/well_matched`.

**Claude calls:** `peek_dds_samples(topic="/dds/well_matched",
count=3)` -> returns 3 deterministic samples with full payload. The
mock fixture also includes two user-topic exemplars exercising the
v0.4.0 decode story : `/dds/ddsforge/example` returns
`_decode_status="full"`, `/dds/ddsforge/opaque` returns
`_decode_status="raw"` with a `_raw_bytes_hex` preview.

## Going live

```bash
pip install topicforge[dds-cyclone]    # or [dds-fast] / [dds]
TOPICFORGE_MODE=live TOPICFORGE_DDS_BACKEND=cyclone python -m topicforge
```

The same three tool calls work against any real DDS domain: Cyclone
and Fast both observe every conformant vendor on the wire (RTI
Connext, OpenDDS, CoreDX, Dust DDS, etc.) via the OMG protocol
guarantee. See [`docs/dds-interop-matrix.md`](../docs/dds-interop-matrix.md).

## Why this is hard without TopicForge

Diagnosing QoS mismatches manually means correlating `ros2 topic info
-v` against the publisher's `rmw_qos_profile_t` setup in C++ source,
then mentally running the OMG compatibility matrix. TopicForge ships
that matrix as a vendor-neutral pure analyzer
(`adapters/common/qos_analyzer.py`) so the LLM can suggest the fix
without a 20-minute deep-dive.
