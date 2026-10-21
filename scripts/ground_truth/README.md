# Ground-truth comparator

One comparator, two producers of ground truth:

1. the OmniSim repro kit (external, manual, Windows + WSL2), kept as confirmation;
2. the Docker bench publisher (`tests/integration/ros2/`), which writes its own
   `ground_truth.json` in the same schema subset and makes the comparison a CI gate.

Files (standard library plus `mcp` and, for `--bag-truth`, `rosbags`, both TopicForge dependencies):

| File | Role |
| --- | --- |
| `drive.py` | MCP stdio driver: runs `calls.json` in one session, saves one JSON per call (result, wall time) and the server's stderr |
| `calls.json` | Default call list. Graph calls come first, sampling after; `for_each` expands over topics and nodes; `{"settle_s": 3}` entries sleep (before the DDS calls and at the end) |
| `calls_graph_diff.json` | The four `graph_diff` calls of the bench only (the OmniSim kit does not run it): `{"spawn": ...}` starts `tests/integration/ros2/graph_change.py` between the steady call and the v2 call, and `drive.py` stops it when the session ends |
| `calls_nav_primitives.json` | The two Nav2 primitive calls of the bench only (`40_action_status`: one sample of the hidden, latched `/bench_fib/_action/status`; `41_bond`: `/bond` samples), run between `calls.json` and `calls_graph_diff.json` |
| `compare.py` | Reads `--truth ground_truth.json --results DIR`, writes `COMPARISON.md` and `comparison.json`, exit 1 on any FAIL |

Verdicts: PASS, FAIL (mismatch), WARN (known finding, 0.6.x shape, machine-dependent deviation),
SHAPE (output shape changed; counted as a failure with `--strict-shape`, which CI uses), INFO.
Sections and tolerances are listed at the top of `compare.py`: graph (topics, types, counts, QoS per
side, nodes, `use_sim_time` per node), scan (geometry, every range, wall and sector minima), stamps
(`stamp_source` per topic), rates (verdict, frequency, interval_cv), bag, dds, tf, clock, logs and, from
0.8.0, `graph_diff` (bench truth only): the first call has no baseline, the steady graph gives zero
changes, after `graph_change.py` starts (node `/bench_v2`, topic `/scan_v2`, a second writer on
`/cmd_vel_out`) exactly those three changes and no other, and a bag baseline removes nothing.
From 0.8.0 the `nav_primitives` section (bench truth only) reads the building blocks of the `diagnose-nav2` prompt, not Nav2: `/bench_robot` serves a never-called action `/bench_fib` and publishes `/bond` (`bond/msg/Status`, id `bench_robot`, 1 Hz). The hidden `_action/status` topic gives one sample with an empty `status_list`, `/bench_fib` is in `get_node_info.action_servers` of `/bench_robot`, the status topic is absent from `list_topics`, and every `/bond` sample carries the id.
The `visibility` section (bench truth only, from `05s_list_endpoints_scan` and the health and participant calls) checks the `/scan` writer's `node_name` is `/bench_robot`, its participant has `endpoint_count` >= 12 and `ros_security_enabled` is false with no note.

Machine-dependent rows (OmniSim kit runs on a shared host):

- Wall-clock frequency rows are WARN at most, never FAIL, when the kit's
  `omnisim.real_time_factor_during_window` is below 0.5, or when TopicForge's
  `observed_frequency_hz / sim_frequency_hz` differs from the kit's `rate_wall_hz / rate_sim_hz` by
  more than 25 percent. The note of the row says which (low RTF, or the two ratios). Simulated-time
  frequency rows stay the gate. (2026-10-14: a run at RTF 0.162 gave 8 wall-clock FAILs, all
  sim-time rows passed.)
- `/parameter_events` and `/rosout` are checked for presence and type only: their writer and reader
  counts (`list_endpoints`) and node lists include hidden `ros2` CLI and daemon nodes, so the
  comparator skips them.
- Participants flagged `is_topicforge_cli`, or whose node names start with `/_ros2cli_`, are not
  counted as ROS nodes in the `dds` section; an INFO row says how many were ignored. The flag is
  optional in the output: its absence changes nothing.
- `drive.py` runs the DDS calls (04-06) before `get_node_info` and settles 3 s before them and at the
  end, because a `ros2 node info` process stays a DDS participant for 0.3 to 2 s after it exits.

## Against the OmniSim kit (Windows + WSL)

1. On Windows, from the kit folder: `python -I run.py --distro Ubuntu-22.04 --bag --hold 600`
   (log to a file). After the `READY` banner note `bag.bag_dir` in `ground_truth.json`.
2. In the same WSL distro, with only `/opt/ros/humble` sourced and a venv that has TopicForge:

   ```
   source /opt/ros/humble/setup.bash
   python3 <repo>/scripts/ground_truth/drive.py --venv ~/topicforge_venv --bag <bag_dir> --out <results_dir>
   ```

   Do this after `READY` so the kit's own snapshot and bag are not disturbed. `--dds-backend cyclone`
   (the default) lets the DDS tools see the Fast DDS participants.
3. On Windows: `python -I <repo>\scripts\ground_truth\compare.py --truth <kit>\ground_truth.json --results <results_dir>`.
   Results from 0.6.x are accepted: shapes that moved in contract 2 come out as WARN.

## Against the bench (CI and locally)

`python tests/integration/ros2/run_bench.py --distro humble --rmw fastrtps --out bench-out`
builds the image, runs the integration tests, then inside the container `entrypoint.sh` runs
`drive.py` against the live graph and `compare.py --strict-shape --bag-truth` against
`/bench/ground_truth.json` (written by `publisher.py` from `tests/integration/ros2/ground_truth.py`).
`bench-out/` receives `COMPARISON.md`, `comparison.json`, `results/` (raw calls), the publisher and
recording logs. `.github/workflows/ros2-live.yml` uploads that folder per matrix cell and prints
the measured `interval_cv` of each fixed-rate topic (CONTRACT.md section 4, `STABLE_CV`).

The bench truth is exact by construction: ranges `1.0 + j * 0.001` m over 541 beams spanning
+-135 degrees (rounded to float32), sector minima from the CONTRACT.md sector rule, rates equal to
the timer periods (the bench stamps with seconds since start, so simulated rate equals wall rate),
`use_sim_time` false on `/bench_robot` and `null` on `/bench_blocked` (its executor never answers).
The TF tree (`tf` section) is three static edges on `/tf_static`, three steady edges on `/tf` (one gives `tool` a second parent) and `map -> odom`, which `publisher.py` sends for 3 s after each new subscriber of `/tf` and then stops, so the 12 s `inspect_tf` call (`17_inspect_tf`) must find exactly one `stale` and one `two_parents` finding.
`/parameter_events` and `/rosout` are checked for presence and type only: their endpoint counts
depend on hidden CLI and daemon nodes.

## Tests

`tests/test_ground_truth_compare.py` runs the comparator without ROS on a trimmed recording of a
real OmniSim run (`tests/fixtures/ground_truth/`, TopicForge 0.6.4, kit Apache-2.0), on two trimmed
0.7.1 runs of 2026-10-14 (`omnisim_071_morning_*`: RTF 0.162, must end PASS with WARN rows;
`omnisim_071_rerun_*`: RTF 0.908, PASS with zero FAIL; the morning truth is reconstructed from the
rerun truth plus the morning's rows because the kit overwrote the original file, and has no bag
section), on a synthetic contract-2 answer set for the bench truth, and on corrupted copies that
must fail with exit 1.
