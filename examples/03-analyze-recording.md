# 03: Post-mortem inspection of a bag recording

**Scenario.** A field deployment failed at 14:32:17 UTC and the team
saved a `.mcap` of the 5-minute window around the failure. You need
quick answers : duration, message counts, anomaly signatures, then a
deep-dive on a specific topic. **Tools exercised.** `analyze_bag`,
`peek_bag_samples`. **Mode.** Mock: exercises the deterministic bag
fixtures.

## Setup

```bash
pip install topicforge[bags]    # rosbags is optional ; required for peek_bag_samples
TOPICFORGE_MODE=mock python -m topicforge
```

The mock fixture exposes `MOCK_BAG_ANALYSIS` and `MOCK_BAG_SAMPLES`
modeling a 60-second recording of the differential robot from
[`01-discover-ros2-stack.md`](01-discover-ros2-stack.md). Three
deterministic topics with realistic frequencies and one canned
anomaly per topic to exercise the analysis loop.

## Conversation

**You:**

> Summarize the bag at `/tmp/demo.mcap`: how long, how many messages,
> anything suspicious?

**Claude calls:** `analyze_bag(path="/tmp/demo.mcap")` -> returns
`BagAnalysis` with `duration_seconds=60.0`, `message_count=600`,
three `BagTopicStats` entries with per-topic counts and frequencies,
`bag_format="mcap"`, `recording_duration_ns=60_000_000_000`,
`samples_decoded_count=0` (analyze does not decode samples ; that's
`peek_bag_samples`'s job), `participants_recorded=[]` (MCAP can
embed participant metadata but the mock fixture doesn't), and a
list of canned `BagAnomaly` records (clock jumps, frequency drift,
etc.).

**You:**

> Show me the actual messages on `/scan` around the anomaly.

**Claude calls:** `peek_bag_samples(path="/tmp/demo.mcap",
topic="/scan", count=5)` -> returns 5 decoded samples through the
shared `cdr_decoder`. Each carries the same `_decode_status`
annotation as live `peek_dds_samples`: typically `"full"` for
ROS2-recorded bags with embedded type descriptions.

**You:**

> Were there any non-ROS DDS participants recorded in the bag?

**Claude responds** referencing `BagAnalysis.participants_recorded`.
For mock-mode bags this is empty (typical for `.db3` and `.bag`) ;
MCAP recordings from a multi-vendor DDS deployment may carry
participant records, which TopicForge surfaces verbatim. The same
`ParticipantInfo` schema is shared with `list_participants` so an
LLM consumer reads one envelope across live and post-mortem flows.

## Going live

```bash
pip install topicforge[bags]
source /opt/ros/humble/setup.bash    # for the analyze_bag CLI fallback
TOPICFORGE_MODE=live python -m topicforge
```

`analyze_bag` retains the v0.3.0 `ros2 bag info` text-parse fallback
on `Ros2CliAdapter` when rosbags is absent ; the enriched fields
(`bag_format`, `recording_duration_ns`, ...) populate at safe defaults
in that path. `peek_bag_samples` strictly requires `rosbags`: no
silent fallback because sample decoding has no CLI equivalent.

## Three formats, one API

`rosbags` (Apache 2.0, pure-Python) reads MCAP, ROS2 `.db3`, and ROS1
`.bag` recordings through a single `AnyReader` API. TopicForge wraps
that surface in `BagService` so the LLM doesn't care which container
the team used: the `bag_format` field surfaces the detected
container for downstream tooling.
