---
name: diagnose-nav2
description: Diagnose a Nav2 stack with TopicForge when navigation does not start, goals are aborted, or the robot does not move. Read-only.
---

# Diagnose a Nav2 stack

Use the TopicForge tools (output contract 2: check `health_check.contract_version` is 2). The same procedure is available as the MCP prompt `diagnose-nav2`. They only observe; nothing here publishes, commands or changes a parameter or a node state. Never suggest doing that through TopicForge.

## Call order

1. `health_check`. If `mode` is `mock`, the data is a fictional demo robot with no Nav2 stack: say so and stop. If `ros_backend` is `none` there is no ROS 2 CLI and none of the steps below can run.
2. `list_nodes`. Look for the Nav2 servers (`controller_server`, `planner_server`, `bt_navigator`, `behavior_server`, `smoother_server`, `waypoint_follower`, `velocity_smoother`, `map_server`, `amcl`) and the `lifecycle_manager_*` nodes. The servers are managed nodes: `lifecycle` is true on them. A server that is missing never started or crashed (step 7 for the reason). TopicForge reads that a node is managed, never its state (unconfigured, inactive, active): steps 3 and 4 infer it. Under a namespace, every node and topic name below carries it.
3. `get_node_info` on each `lifecycle_manager_*`, in `parameters`: `node_names` (the nodes it manages), `autostart` (false means nothing starts until someone triggers the manager) and `bond_timeout`. A name in `node_names` that step 2 did not list was never launched. `parameters` null with a `parameters_note` means the node does not answer: report it as a finding.
4. `sample_messages` on `/bond` (type `bond/msg/Status`) with `count` 50 and `timeout_s` 5. A managed node creates its bond when it is activated, and each bond (its `id` is the node name) is heard from both ends several times per second. A name in `node_names` whose `id` never appears is not bonded: not activated, dead, or its bond broke and the manager brought the stack down. No publisher on `/bond` at all means no node was ever activated. Sample once more before concluding on a single missing `id`.
5. `get_node_info` on `bt_navigator`: its `action_servers` (`/navigate_to_pose`, `/navigate_through_poses`). For each, `sample_messages` on `<action>/_action/status` with `count` 1. `list_topics` hides the `_action` topics, but `sample_messages` reads them, and they are latched. In `status_list`, `status` is 1 accepted, 2 executing, 3 canceling, 4 succeeded, 5 canceled, 6 aborted. An empty list means no goal is active. A topic with no message may simply be a server that has not handled a goal since it started: say so, do not call it a failure. After an abort (6), look at the same topic of `follow_path` (`controller_server`) and `compute_path_to_pose` (`planner_server`) to see which server gave up.
6. `inspect_tf` with `source_frame` `map` and `target_frame` `base_link`. `path` is the chain, `findings` names `no_path` (disconnected: localization is not publishing `map -> odom`), `stale` and `two_parents`. Ages are on header stamps.
7. `sample_messages` on `/rosout` with `min_log_level` `warn` and `node` set to the server you suspect (`/bt_navigator`, `/controller_server`, `/planner_server`, then the `lifecycle_manager_*`). It returns the newest matching lines after listening for `timeout_s`. Quote the line.
8. While a goal is executing (step 5), `sample_messages` on the controller's velocity output with `count` 5: the topic name is in `get_node_info` on `controller_server`, `publishers` (often `/cmd_vel` or `/cmd_vel_nav`). Silence while the goal is executing points to the controller. Messages that flow but no robot motion point downstream of Nav2: `get_topic_info` on that topic shows who subscribes (`subscriber_nodes`), and a name mismatch is a common cause.

Stop as soon as one step explains the symptom.

## What TopicForge cannot see

- A node's lifecycle state. Only the bond and the action status hint at it.
- Costmaps. They can be sampled, not judged: a robot boxed in by an obstacle looks like an aborted goal and a log line.
- The behavior tree: which node ran, and which recoveries retried. The `behavior_server` actions (`/spin`, `/backup`, `/wait`) and `/rosout` are the only traces.
- Whether the robot moves. It sees commands, not odometry quality or the base driver.

## Answering

State the finding, the evidence (tool, field, value), and the fix as a change to the user's own launch file or parameters. When the evidence is partial, say what is unknown and what the user could check next. Do not claim a cause that these tools cannot show.
