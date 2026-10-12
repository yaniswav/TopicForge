# Ground-truth comparator

One comparator, two producers of ground truth:

1. the OmniSim repro kit (external, manual, Windows + WSL2), kept as confirmation;
2. the Docker bench publisher (`tests/integration/ros2/`), which writes its own
   `ground_truth.json` in the same schema subset and makes the comparison a CI gate.

Files (standard library plus `mcp` and, for `--bag-truth`, `rosbags`, both TopicForge dependencies):

| File | Role |
| --- | --- |
| `drive.py` | MCP stdio driver: runs `calls.json` in one session, saves one JSON per call (result, wall time) and the server's stderr |
| `calls.json` | Default call list. Graph calls come first, sampling after; `for_each` expands over topics and nodes |
| `compare.py` | Reads `--truth ground_truth.json --results DIR`, writes `COMPARISON.md` and `comparison.json`, exit 1 on any FAIL |

Verdicts: PASS, FAIL (mismatch), WARN (known finding, 0.6.x shape, machine-dependent deviation),
SHAPE (output shape changed; counted as a failure with `--strict-shape`, which CI uses), INFO.
Sections and tolerances are listed at the top of `compare.py`: graph (topics, types, counts, QoS per
side, nodes, `use_sim_time` per node), scan (geometry, every range, wall and sector minima), stamps
(`stamp_source` per topic), rates (verdict, frequency, interval_cv), bag, dds.

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
`/parameter_events` and `/rosout` are checked for presence and type only: their endpoint counts
depend on hidden CLI and daemon nodes.

## Tests

`tests/test_ground_truth_compare.py` runs the comparator without ROS on a trimmed recording of a
real OmniSim run (`tests/fixtures/ground_truth/`, TopicForge 0.6.4, kit Apache-2.0), on a synthetic
contract-2 answer set for the bench truth, and on corrupted copies that must fail with exit 1.
