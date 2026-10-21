"""MCP prompts and server instructions.

Three prompts give a client a ready-made, read-only procedure: `diagnose-dds-bus`,
`inspect-ros2-robot` and `diagnose-nav2`. Their text mirrors the plugin skills under
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
    "`get_topic_info` / `list_nodes` / `get_node_info` / `inspect_tf` / `sample_messages` for a ROS 2 graph, `list_participants` / "
    "`list_endpoints` / `detect_qos_mismatches` / `participant_events` for a DDS bus, and "
    "`analyze_bag` / `peek_bag_samples` for recordings. Every result carries "
    "`mode_effective` and a `note`: read the `note` before drawing a conclusion. The "
    "prompts `diagnose-dds-bus`, `inspect-ros2-robot` and `diagnose-nav2` give the call order."
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
5. `inspect_tf` when frames, transforms or `tf` lookups are in question: the tree from `/tf_static` and `/tf`, its `roots` (more than one means disconnected), and `findings` (`stale`, `two_parents`, `static_and_dynamic`, `cycle`, `no_path`). Ages are on header stamps, so they hold on sim time; give `source_frame` and `target_frame` for the chain between two frames.
6. `sample_messages` for content. Keep `count` small and raise `timeout_s` (at most 40) for topics slower than 1 Hz.

sample_messages options:

- Arrays, strings and bytes are cut at 128 elements by default and listed in `_truncated_fields`. Set `max_array_length` (up to 65536, or null for everything) to read a full `LaserScan`; null is large for images and point clouds.
- `arrays_summary_only: true` shows only the non-array fields. Use it for images and point clouds.
- `timestamp_ns` is the message header stamp: sim time on a simulation, 0 when `stamp_source` is `none`. `received_ns` is the wall clock at print time. Do not mix the two.
- A short result carries a `note` saying why. A latched topic usually holds one message: use `count` 1.
- For why a node failed, read `/rosout` with `min_log_level` and `node`: it returns the newest matching logs. `peek_bag_samples` takes `start_s` / `end_s` and the same filters on a bag (first matches).

Bags:

- `analyze_bag` (`.mcap`, `.db3`, `rosbag2_*` directory): duration, message count, per-topic stats. `frequency_basis` says how the rate was computed (`topic_span` or `bag_duration`); a `latched` topic can have a null rate. Anomaly detection exists in mock mode only.
- `peek_bag_samples` (also reads ROS 1 `.bag`) for decoded messages, with a `_decode_status` per sample. It needs the `rosbags` library; if missing, tell the user.

Answering: report what the tools returned, name the tool and field, and separate observed facts from guesses. If a topic is silent or a call timed out, say that is all you know.
"""


_NAV2 = """\
Diagnose a Nav2 stack (navigation that does not start, aborts its goals, or does not move \
the robot) with the TopicForge tools. They only observe; nothing here publishes, commands or \
changes a parameter or a node state. Never suggest doing that through TopicForge.
{focus}
Call order:

1. `health_check`. If `mode` is `mock`, the data is a fictional demo robot with no Nav2 stack: say so and stop. If `ros_backend` is `none` there is no ROS 2 CLI and none of the steps below can run.
2. `list_nodes`. Look for the Nav2 servers (`controller_server`, `planner_server`, `bt_navigator`, `behavior_server`, `smoother_server`, `waypoint_follower`, `velocity_smoother`, `map_server`, `amcl`) and the `lifecycle_manager_*` nodes. The servers are managed nodes: `lifecycle` is true on them. A server that is missing never started or crashed (step 7 for the reason). TopicForge reads that a node is managed, never its state (unconfigured, inactive, active): steps 3 and 4 infer it. Under a namespace, every node and topic name below carries it.
3. `get_node_info` on each `lifecycle_manager_*`, in `parameters`: `node_names` (the nodes it manages), `autostart` (false means nothing starts until someone triggers the manager) and `bond_timeout`. A name in `node_names` that step 2 did not list was never launched. `parameters` null with a `parameters_note` means the node does not answer: report it as a finding.
4. `sample_messages` on `/bond` (type `bond/msg/Status`) with `count` 50 and `timeout_s` 5. A managed node creates its bond when it is activated, and each bond (its `id` is the node name) is heard from both ends several times per second. A name in `node_names` whose `id` never appears is not bonded: not activated, dead, or its bond broke and the manager brought the stack down. No publisher on `/bond` at all means no node was ever activated. Sample once more before concluding on a single missing `id`.
5. `get_node_info` on `bt_navigator`: its `action_servers` (`/navigate_to_pose`, `/navigate_through_poses`). For each, `sample_messages` on `<action>/_action/status` with `count` 1. `list_topics` hides the `_action` topics, but `sample_messages` reads them, and they are latched. In `status_list`, `status` is 1 accepted, 2 executing, 3 canceling, 4 succeeded, 5 canceled, 6 aborted. An empty list means no goal is active. A topic with no message may simply be a server that has not handled a goal since it started: say so, do not call it a failure. After an abort (6), look at the same topic of `follow_path` (`controller_server`) and `compute_path_to_pose` (`planner_server`) to see which server gave up.
6. `inspect_tf` with `source_frame` `map` and `target_frame` `base_link`. `path` is the chain, `findings` names `no_path` (disconnected: localization is not publishing `map -> odom`), `stale` and `two_parents`. Ages are on header stamps.
7. `sample_messages` on `/rosout` with `min_log_level` `warn` and `node` set to the server you suspect (`/bt_navigator`, `/controller_server`, `/planner_server`, then the `lifecycle_manager_*`). It returns the newest matching lines after listening for `timeout_s`. Quote the line.
8. While a goal is executing (step 5), `sample_messages` on the controller's velocity output with `count` 5: the topic name is in `get_node_info` on `controller_server`, `publishers` (often `/cmd_vel` or `/cmd_vel_nav`). Silence while the goal is executing points to the controller. Messages that flow but no robot motion point downstream of Nav2: `get_topic_info` on that topic shows who subscribes (`subscriber_nodes`), and a name mismatch is a common cause.

Stop as soon as one step explains the symptom.

What TopicForge cannot see:

- A node's lifecycle state. Only the bond and the action status hint at it.
- Costmaps. They can be sampled, not judged: a robot boxed in by an obstacle looks like an aborted goal and a log line.
- The behavior tree: which node ran, and which recoveries retried. The `behavior_server` actions (`/spin`, `/backup`, `/wait`) and `/rosout` are the only traces.
- Whether the robot moves. It sees commands, not odometry quality or the base driver.

Answering: state the finding, the evidence (tool, field, value), and the fix as a change to the user's own launch file or parameters. When the evidence is partial, say what is unknown and what the user could check next. Do not claim a cause that these tools cannot show.
"""


def _focus(label: str, value: str) -> str:
    """One line that points the procedure at what the user named, or nothing."""
    value = value.strip()
    return f"\n{label}: {value}\n" if value else ""


def register_prompts(mcp: MCPServer) -> None:
    """Register the three prompts on `mcp`."""

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

    @mcp.prompt(
        name="diagnose-nav2",
        title="Diagnose a Nav2 stack",
        description=(
            "Step-by-step, read-only diagnosis of a Nav2 stack that does not start, aborts "
            "goals or does not move the robot."
        ),
    )
    def diagnose_nav2(symptom: str = "", namespace: str = "") -> str:
        """`symptom` and `namespace` are optional and only focus the text."""
        focus = _focus("Symptom", symptom) + _focus("Namespace", namespace)
        return _NAV2.format(focus=focus)
