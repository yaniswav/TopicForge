# 02: Debug a QoS mismatch on the DDS bus

A subscriber reports "no data" but `ros2 topic list` shows the topic and a
publisher. The cause is a DDS QoS incompatibility. This walkthrough uses
`list_participants`, `list_endpoints`, `detect_qos_mismatches` and
`peek_dds_samples`, against the mock.

## Setup

```bash
pip install topicforge
TOPICFORGE_MODE=mock python -m topicforge
```

The mock DDS fixture ships four deterministic participants
(two Cyclone, one Fast, one Dust) on domain `0`, with four topics:
`/dds/well_matched` (compatible QoS), `/dds/qos_mismatch`
(deliberate Reliability incompatibility: RELIABLE reader vs
BEST_EFFORT writer), and two user-topic payload exemplars,
`/dds/topicforge/example` and `/dds/topicforge/opaque`, covered at the end.

## Conversation

You:

> Who is on the DDS bus right now, and is there a topic where readers
> and writers don't agree on QoS?

Claude calls: `list_participants(domain_id=0)` -> returns 4
participants with `vendor`, `hostname`, `guid`, lifecycle fields
(`first_seen_ns`, `status="active"`, `seen_count`). The
mixed vendors are mock data; on a live bus the author has observed Cyclone DDS and Dust DDS. `list_endpoints()` then gives every writer and reader with its
participant, type and structured QoS, and a `by_topic` roll-up that flags
topics with a writer and no reader. Then `detect_qos_mismatches(topic=None)` -> returns a
`MismatchScan` whose `reports` hold 1 `MismatchReport` for
`/dds/qos_mismatch` with `incompatible_policies=["Reliability"]`,
`severity="incompatible"`, the two participant names and
`details=[{policy: "Reliability", requested: "RELIABLE", offered:
"BEST_EFFORT", rule: ...}]`. `not_matched` is empty (no partition or type
split) and `hints` notes that `/dds/topicforge/opaque` has a writer and no
reader.

You:

> What policy is wrong, and what should I change?

Claude answers:

> The `/dds/qos_mismatch` topic has a reader requesting
> `Reliability: RELIABLE` and a writer offering
> `Reliability: BEST_EFFORT`. By the OMG-DDS-RTPS Request-vs-Offered
> rule, BEST_EFFORT writers cannot satisfy RELIABLE readers: the
> match is rejected at discovery and no samples flow. Two fixes:
>
> 1. Relax the reader to `BEST_EFFORT` (if data loss is acceptable).
> 2. Upgrade the writer to `RELIABLE` (if the data is critical).
>
> The other topic `/dds/well_matched` has no mismatch reported, so
> the same diagnostic loop would not fire there. Caveat: discovery shows
> the QoS each endpoint *declared*, and the scan lists what it does not
> cover in `policies_unchecked`, so an empty result is not proof of a
> healthy bus.

You:

> Peek what's actually flowing on `/dds/well_matched`.

Claude calls: `peek_dds_samples(topic="/dds/well_matched",
count=3)` -> returns 3 deterministic samples (`{"seq": i, "vendor":
"cyclone"}`). The mock fixture also includes two user-topic examples
that show the payload annotations: `/dds/topicforge/example`
returns `_decode_status="full"` with decoded fields, `/dds/topicforge/opaque`
returns `_decode_status="raw"` with a `_raw_bytes_hex` preview.

Mock only. Those samples show the wire shape, not live behaviour. On a
real bus, Cyclone and Fast do not decode user-topic payloads:
`peek_dds_samples` on a user topic returns `count=0` and a `note` saying
decoding is disabled and that this does not mean the topic is silent. Use
`list_endpoints` to see who writes and reads the topic. Neither `"full"` nor
`"partial"` is produced by any live adapter today. See
[`docs/DDS_QUICKSTART.md`](../docs/DDS_QUICKSTART.md) section 5.

## Going live

```bash
pip install topicforge[dds-cyclone]    # [dds] is equivalent
TOPICFORGE_DDS_BACKEND=cyclone python -m topicforge
```

The Fast DDS adapter (`TOPICFORGE_DDS_BACKEND=fast`) has no PyPI extra:
it needs a Python binding built from eProsima's sources.

`list_participants`, `list_endpoints` and `detect_qos_mismatches` are
discovery-based and work against any real DDS domain: Cyclone observes every
conformant vendor on the wire (RTI Connext, OpenDDS, CoreDX, Dust DDS, etc.)
via the OMG protocol guarantee. `list_endpoints` is not served by the Fast DDS
backend. `peek_dds_samples` on a user topic cannot show what is flowing. See
[`docs/dds-interop-matrix.md`](../docs/dds-interop-matrix.md). Cyclone and Dust
DDS participants have been run together on a live bus; the other vendors are
covered by the protocol guarantee only.

## How the analysis works

Doing this by hand means correlating `ros2 topic info -v` against the
publisher's `rmw_qos_profile_t` setup in C++ source, then working through the
OMG compatibility matrix. TopicForge implements that matrix as a
vendor-neutral pure analyzer (`adapters/common/qos_analyzer.py`). It covers Reliability,
Durability, Deadline, Liveliness, LatencyBudget, Ownership,
DestinationOrder and DataRepresentation (History as a risk only), and
checks Partition and the type name first: a reader and a writer in
different partitions are returned in `not_matched` with reason
`partition`, never blamed on Reliability. Presentation, XTypes
assignability and runtime behavior are listed in `policies_unchecked`.
Matching pairs are listed under `matched`, and discovery shows declared QoS
only: a writer that is alive but silent looks healthy.
