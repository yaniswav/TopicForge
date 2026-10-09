"""MCP prompts and server instructions.

Two prompts give a client a ready-made, read-only procedure: `diagnose-dds-bus`
and `inspect-ros2-robot`. Their text mirrors the plugin skills under
`plugin/skills/` in client-neutral wording. `INSTRUCTIONS` is the short text
sent in the `initialize` result, for clients that do not surface prompts.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from topicforge.services.health import CONTRACT_VERSION

INSTRUCTIONS = (
    "TopicForge is a read-only observer of ROS 2 and DDS: it never publishes, commands "
    "or changes QoS, and no tool can. Call `health_check` first: it always answers, "
    "tells a real bus (`mode` live) from fixtures (`mode` mock), and reports "
    f"`contract_version` (this server speaks {CONTRACT_VERSION}). Then use `list_topics` / "
    "`get_topic_info` / `list_nodes` / `get_node_info` / `sample_messages` for a ROS 2 graph, `list_participants` / "
    "`list_endpoints` / `detect_qos_mismatches` / `participant_events` for a DDS bus, and "
    "`analyze_bag` / `peek_bag_samples` for recordings. Every result carries "
    "`mode_effective` and a `note`: read the `note` before drawing a conclusion. The "
    "prompts `diagnose-dds-bus` and `inspect-ros2-robot` give the call order."
)

_DIAGNOSE = """\
Diagnose a DDS or ROS 2 bus with the TopicForge tools. They only observe discovery; \
nothing here publishes, commands or changes QoS. Never suggest doing that through TopicForge.
{focus}
Call order:

1. `health_check`. If `dds_backend` is `mock` or `none`, say so: results are fixtures or unavailable, not the real bus. Note `dds_domain_id` (only that domain is seen) and `observer_started_ns` (nothing earlier was observed). Tracker errors above 0 mean gaps.
2. `list_participants`. Is each expected node present, with the right `name`? A missing one may be on another domain or have crashed.
3. `list_endpoints` with `topic` set to the failing topic (`scan` and `rt/scan` match each other). Check `by_topic`: `orphan` is `no_reader` or `no_writer`, and `departed_writers` / `departed_readers` name a peer that left.
4. `detect_qos_mismatches` with the same `topic`.
5. `participant_events` for crashes or restarts. Use a short `lookback_s`.

Stop as soon as one step explains the symptom.

Reading the result: read `not_matched` before `reports`. Partition or type-name splits are checked first, and QoS rules are not run on those pairs. Then read `reports`: each gives requested vs offered values and the failed rule. Then `hints` (probable topic typos). `policies_unchecked` lists what was not compared: quote it before claiming the bus is fine. A `lost` participant timestamp is an upper bound of the death (the lease expiry after a crash). A restarted node is a new participant: one `lost`, one `discovered`, a different `guid`, the same `name`.

What TopicForge cannot see:

- Whether data actually flows. An empty `reports` does not prove the bus is healthy.
- A writer that is alive but silent or hung. User-topic payloads are not decoded.
- Other DDS domains. DDS Security: protected endpoints and data stay hidden.
- Runtime behavior such as missed deadlines. Only declared QoS is visible.

Answering: state the finding, the evidence (tool, field, value), and the fix as a change to the user's own code or config. When the evidence is partial, say what is unknown and what the user could check next. Do not claim a cause that discovery cannot show.
"""

_INSPECT = """\
Inspect a ROS 2 robot or a recorded bag with the TopicForge tools. They only read; \
never suggest publishing, commanding or changing the robot.
{focus}
Live graph:

1. `health_check`. If `mode` is `mock`, the data is a fictional demo robot, not the user's: say so. If `ros_backend` is `none` there is no ROS 2 CLI; use `list_endpoints` instead.
2. `list_topics`: names, types, publisher and subscriber counts, in `topics`. QoS is not in the listing: `get_topic_info` gives `publisher_qos`, `subscription_qos` and the node names on each side.
3. `get_topic_info` for one topic: reliability, durability (`transient_local` means latched, as on `/tf_static`).
4. `list_nodes` for who is on the graph, then `get_node_info` for one node: its publishers, subscribers, services, actions, parameters and `use_sim_time`. `duplicates` flags several nodes with one name (the CLI answers for one of them). `parameters` null with a `parameters_note` means the node did not answer its parameter read in time, so its executor is probably blocked: report that as a finding, not an error. Values named password, secret, token, api_key or credential are masked.
5. `sample_messages` for content. Keep `count` small and raise `timeout_s` (at most 40) for topics slower than 1 Hz.

sample_messages options:

- Arrays, strings and bytes are cut at 128 elements by default and listed in `_truncated_fields`. Set `max_array_length` (up to 65536, or null for everything) to read a full `LaserScan`; null is large for images and point clouds.
- `arrays_summary_only: true` shows only the non-array fields. Use it for images and point clouds.
- `timestamp_ns` is the message header stamp: sim time on a simulation, 0 when `stamp_source` is `none`. `received_ns` is the wall clock at print time. Do not mix the two.
- A short result carries a `note` saying why. A latched topic usually holds one message: use `count` 1.

Bags:

- `analyze_bag` (`.mcap`, `.db3`, `rosbag2_*` directory): duration, message count, per-topic stats. `frequency_basis` says how the rate was computed (`topic_span` or `bag_duration`); a `latched` topic can have a null rate. Anomaly detection exists in mock mode only.
- `peek_bag_samples` (also reads ROS 1 `.bag`) for decoded messages, with a `_decode_status` per sample. It needs the `rosbags` library; if missing, tell the user.

Answering: report what the tools returned, name the tool and field, and separate observed facts from guesses. If a topic is silent or a call timed out, say that is all you know.
"""


def _focus(label: str, value: str) -> str:
    """One line that points the procedure at what the user named, or nothing."""
    value = value.strip()
    return f"\n{label}: {value}\n" if value else ""


def register_prompts(mcp: MCPServer) -> None:
    """Register the two prompts on `mcp`."""

    @mcp.prompt(
        name="diagnose-dds-bus",
        title="Diagnose a DDS or ROS 2 bus",
        description=(
            "Step-by-step, read-only diagnosis of nodes that do not talk, a topic that gets no "
            "data, or a node that crashed or restarts."
        ),
    )
    def diagnose_dds_bus(topic: str = "", symptom: str = "") -> str:
        """`topic` and `symptom` are optional and only focus the text."""
        focus = _focus("Failing topic", topic) + _focus("Symptom", symptom)
        return _DIAGNOSE.format(focus=focus)

    @mcp.prompt(
        name="inspect-ros2-robot",
        title="Inspect a ROS 2 robot or bag",
        description=(
            "Read-only tour of a ROS 2 graph or a recorded bag: what topics exist, what a topic "
            "publishes, what a bag contains."
        ),
    )
    def inspect_ros2_robot(topic: str = "", bag_path: str = "") -> str:
        """`topic` and `bag_path` are optional and only focus the text."""
        focus = _focus("Topic", topic) + _focus("Bag", bag_path)
        return _INSPECT.format(focus=focus)
