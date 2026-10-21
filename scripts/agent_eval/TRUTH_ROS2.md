# Ground truth of the ROS 2 scenarios (0.8.0 campaign)

Scenarios of `ros2_scenarios.py` (bench robot `/bench_robot`, `publisher.py --fault NAME`, clean
profile of `tests/integration/ros2/faults.py`). Each has one fault or none. The agent has
TopicForge's tools only; score its final answer against the section of its scenario.

Grades, as in `docs/EVAL.md`: correct (the real cause and the affected components named,
nothing invented), partially correct (right direction, a wrong or missing element), wrong,
false alarm (a problem reported that does not exist). A remark listed under "acceptable" is
never a false alarm. A `critical` or "do not ship" verdict that rests on an acceptable remark
is.

Present on every scenario, so never the answer and acceptable to mention:

- the robot is `/bench_robot` (Fast DDS names every participant `/`, the node name is the link);
- `map` and `odom` are TF frames with no URDF link (`frames_not_in_urdf`), which is legitimate;
  nobody publishes `/joint_states` (the `tool` frame still comes out on `/tf`);
- `/scan_edge` carries `inf`, `nan` and `-inf` ranges (a lidar's no-return beams);
- `/bond` has a publisher and no lifecycle manager exists, `/bench_fib` is an action server
  nobody calls (its `_action/status` topic is latched with an empty `status_list`): the
  primitives of the `diagnose-nav2` prompt, there is no Nav2 stack on this rig;
- most topics have publishers and no subscribers: nothing consumes them on this rig;
- hidden `ros2` CLI nodes and participants (`is_topicforge_cli`), `/rosout`, `/parameter_events`;
- `/camera/image_raw` starts about 5 s after the robot.

## healthy

No fault. Clean TF (static: `base_link -> base_laser`, `base_link -> camera_link`, `camera_link ->
camera_optical_frame`; dynamic `map -> odom -> base_link -> tool`, one root `map`, no second
parent), a URDF whose five links are all published (`declared_frames_missing` empty), no WARN or
ERROR line on `/rosout`, no QoS mismatch (no reader asks more than a writer offers), one node.
A correct answer says the robot looks healthy and states what was not checked (data quality, who
consumes what). False alarm: any finding presented as a fault, in particular `stale`,
`two_parents`, `declared_not_published`, a QoS mismatch, a crash or a restart; "no subscribers"
or the `inf` beams offered as the problem; a "critical" label of any kind.

## blocked_executor

`/bench_blocked` is on the graph (listed by `list_nodes`, `ros2 node info` answers) but its
executor never runs again: it sleeps for ever in a timer callback. `get_node_info` returns its
interfaces, `parameters` null with a `parameters_note` that says the parameter read did not
answer in time, and `use_sim_time` null. A correct answer: the node is alive on the graph and
blocked (not crashed, not missing), the evidence is the unanswered parameter read, the usual
cause is a callback that blocks the executor (a sleep, a blocking call or a lock), the fix is in
that node's code (callback groups with a multi-threaded executor, or no blocking in a
callback). The rest of the robot is fine. Wrong: the node crashed or does not exist, a DDS or
network cause, a QoS cause. False alarm: any fault on `/bench_robot` or its topics.

## tf_stale

`map -> odom` is published for 3 s after each new subscriber of `/tf`, then stops (the same
mechanism as the CI bench: TopicForge sees `/tf` only through a capture window). `odom ->
base_link`, `base_link -> tool` and the static edges are steady at 10 Hz. `inspect_tf` with
`source_frame` `map`, `target_frame` `base_link`: a finding `stale` on frame `odom` (its
transform from `map` trails the others by more than `stale_after_s`), `roots` `map`, and a note
that the chain crosses a stale frame. A correct answer: the `map -> odom` transform stopped
being published (the localization side: AMCL, SLAM or whatever owns that edge), the rest of the
tree is live, TopicForge reads the publisher of `/tf` as `/bench_robot` (the only one) and can
say no more about which process owns the edge. Wrong: `odom -> base_link` or the robot base
blamed, "frame does not exist" without the stale reading, a QoS or clock cause. False alarm:
`two_parents`, `declared_not_published`, or any fault on another topic.

## qos_mismatch

`/scan_edge`: the writer (`/bench_robot`) offers BEST_EFFORT, the reader (`/edge_viewer`)
requests RELIABLE, so DDS never connects them and the viewer receives nothing. `get_topic_info`
shows `publisher_qos` best_effort and `subscription_qos` reliable; `detect_qos_mismatches` (DDS
backend) reports the pair on reliability. A correct answer names the topic, both nodes, both
policies, says the cause is the reader asking for more than the writer offers, and proposes
BEST_EFFORT on the reader (sensor-data QoS) or RELIABLE on the writer. The mismatch may or may
not show as a warning in `/rosout`. Wrong: a partition, a type or a topic-name cause, the node
crashed, the `inf` beams. False alarm: a mismatch on any other topic (there is none).

## restart_loop

`/path_planner` (a node publishing `/plan`) lives about 6 s, logs an ERROR "path_planner:
costmap not received, giving up", exits with code 1, and a supervisor starts a new process
about 2 s later, for ever (a new process is a new DDS participant). At question time the
broker has watched it for 45 s or more: expect 4 or more restarts; the true number is
`restarts_at_question` in the run's `summary.json` (plus or minus 1 is accepted, the cycle is 8
to 9 s). Evidence: `participant_events` and `restarts_by_name` (several `lost` and
`discovered` pairs with different `guid`s and the same name), `list_nodes` showing the node
only some of the time, `/rosout` errors from `/path_planner`. A correct answer: the node
crash-restarts repeatedly (a loop, not one restart), it exits by itself after a few seconds of
life, the logged reason is the missing costmap (a dependency of the planner, not the planner's
own code), and it gives a count. Partially correct: one restart reported, or "the node is
missing" from a listing taken in the gap, or a count with no cause. Wrong: "stable", or a
cause on another component. False alarm: any fault on `/bench_robot`.

## declared_drift

Flagship 3 (declared against real). Two differences, no more:

1. The URDF on `/robot_description` declares `arm_link` on a revolute joint under `base_link`,
   and no TF frame `arm_link` exists: `inspect_tf` answers `urdf_source` `robot_description`,
   `declared_frames_missing` `["arm_link"]` and a finding `declared_not_published` (its joint
   is not fixed, so the frame exists only while `/joint_states` is published).
2. The parameter file `bench_params.yaml` (path in the question) says `max_range: 12.0`, the
   node has 10.0 (`different`), and says `extra_gain: 1.5`, which the node does not have
   (`declared_only`). `get_node_info` of `/bench_robot` with `params_file` returns this as
   `param_diff`; `frame_id: laser` is the same on both sides (counted in
   `param_diff_summary`, not a difference), and the node's other parameters (`scan_period`,
   `use_sim_time`) are live only, not differences.

A correct answer names `arm_link`, `max_range` (12.0 declared, 10.0 live) and `extra_gain`
(declared, absent live), and nothing else as a difference. Partially correct: one of the three
missing. False alarm: `frame_id` reported as different, `map`, `odom` or `tool` reported as
URDF defects, `arm_link` reported as published, a difference on `scan_period`.

## graph_v1_v2

Flagship 1 (what changed since it last worked). The runner took a `graph_diff` baseline before
the change (so the agent's first `graph_diff` compares with it) and then started
`graph_change.py`. Exactly three changes: node `/bench_v2` added; topic `/scan_v2`
(`sensor_msgs/msg/LaserScan`) added; `/cmd_vel_out` `publisher_count` changed 1 to 2 (a second
writer, same QoS, from `/bench_v2`). `builtin_changes_count` may be non-zero (counters of
`/rosout` and `/parameter_events` are set aside, not changes), and a note about a lingering
`ros2` CLI reader is acceptable. A correct answer lists those three, with before and after for
the count, says nothing else changed (no node, topic, type or QoS removed or altered), and may
remark that two writers on `/cmd_vel_out` deserve a look. A second `graph_diff` call compares
the graph with the first one's snapshot and answers zero changes: an agent that reports that as
"nothing changed" has missed `baseline` (`snap-1`). Partially correct: two of the three. False
alarm: any other change listed, a removed node or topic, a QoS or rate change, `/bench_v2`
called broken.
