#!/usr/bin/env bash
# Runs inside the bench container: publisher, bag recording, pytest, then the
# ground-truth comparison (scripts/ground_truth: drive.py + compare.py).
# No `set -u`: the ROS setup scripts read unset variables.
source "/opt/ros/${ROS_DISTRO}/setup.bash"
HERE=/opt/topicforge/tests/integration/ros2
BAG="${TOPICFORGE_BENCH_BAG:-/bench/bag}"
OUT="${TOPICFORGE_BENCH_OUT:-/bench/out}"
# publisher.py writes the bench's own ground truth here when it starts.
export TOPICFORGE_BENCH_TRUTH=/bench/ground_truth.json
mkdir -p "$(dirname "$BAG")" "$OUT"
rm -rf "$BAG"

echo "bench: ROS_DISTRO=${ROS_DISTRO} RMW_IMPLEMENTATION=${RMW_IMPLEMENTATION:-default}"
python3 "$HERE/publisher.py" > /bench/publisher.log 2>&1 &
PUB_PID=$!
# A node with a stuck executor, for the `get_node_info` timeout test.
python3 "$HERE/blocked_node.py" > /bench/blocked.log 2>&1 &
BLOCKED_PID=$!
# A managed node in /robot1, for the `list_nodes` lifecycle flag and by_namespace.
python3 "$HERE/lifecycle_node.py" > /bench/lifecycle.log 2>&1 &
LIFECYCLE_PID=$!
trap 'kill $PUB_PID $BLOCKED_PID $LIFECYCLE_PID 2>/dev/null' EXIT

for _ in $(seq 1 60); do
    ros2 topic list 2>/dev/null | grep -qx /scan && break
    sleep 0.5
done
if ! ros2 topic list 2>/dev/null | grep -qx /scan; then
    echo "bench: publisher never appeared"
    cat /bench/publisher.log
    exit 2
fi

for _ in $(seq 1 30); do
    ros2 node list 2>/dev/null | grep -qx /bench_blocked && break
    sleep 0.5
done
for _ in $(seq 1 30); do
    ros2 node list 2>/dev/null | grep -qx /robot1/bench_lifecycle && break
    sleep 0.5
done

echo "bench: recording bag (8 s)"
# `ros2 bag record` has no --duration on Humble: stop it with SIGINT.
timeout -s INT 8 ros2 bag record -o "$BAG" \
    /clock /scan /cmd_vel_out /camera/image_raw /robot_description_lite /rosout \
    > /bench/record.log 2>&1
sleep 2
if [ ! -e "$BAG/metadata.yaml" ]; then
    echo "bench: bag recording failed"
    cat /bench/record.log
    exit 3
fi

# The camera starts late; let every topic be up before the tests.
sleep 2
cd /opt/topicforge
# ROS puts its own pytest plugins on PYTHONPATH (launch_testing needs yaml);
# the adapter's `ros2` subprocesses still need that PYTHONPATH.
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
/opt/tfenv/bin/python -m pytest -m integration tests/integration/ros2 -v "$@"
PYTEST_RC=$?

# Ground truth: drive TopicForge over MCP stdio against the live graph, then compare
# every answer with the truth the publisher wrote. Graph calls come before any sampling.
GT=scripts/ground_truth

# One pass per sampling backend, before the last pass changes the graph. TOPICFORGE_ROS_SAMPLER=rclpy
# fails rather than falls back and compare.py checks `sampling_backend`, so a silent fallback
# cannot pass. calls_sampler_writers.json holds a helper open: it comes last in its pass.
sampler_pass() {
    local sampler="$1"
    shift
    echo "bench: ground truth, TOPICFORGE_ROS_SAMPLER=$sampler"
    rm -rf "$OUT/results_$sampler"
    /opt/tfenv/bin/python "$GT/drive.py" --out "$OUT/results_$sampler" --bag "$BAG" \
        --calls "$GT/calls.json" "$GT/calls_declared.json" "$GT/calls_nav_primitives.json" "$GT/calls_diagnose.json" "$@" \
        --server-cmd /opt/tfenv/bin/topicforge --mode live --dds-backend cyclone \
        --env TOPICFORGE_LOG_LEVEL=WARNING --env "TOPICFORGE_ROS_SAMPLER=$sampler" \
        > "$OUT/drive_$sampler.log" 2>&1 || return 1
    tail -n 3 "$OUT/drive_$sampler.log"
    /opt/tfenv/bin/python "$GT/compare.py" --truth /bench/ground_truth.json \
        --results "$OUT/results_$sampler" --bag-truth "$BAG" --strict-shape \
        --expect-sampler "$sampler" --skip-section graph_diff \
        --out "$OUT/results_$sampler/COMPARISON.md" > "$OUT/compare_$sampler.log" 2>&1
    local rc=$?
    tail -n 1 "$OUT/compare_$sampler.log"
    return $rc
}
sampler_pass cli
CLI_RC=$?
sampler_pass rclpy "$GT/calls_sampler_writers.json"
RCLPY_RC=$?
# Let the last helper and the ghosts of both passes leave before the graph is read again.
sleep 5

echo "bench: ground truth (drive.py, compare.py)"
rm -rf "$OUT/results"
# calls_declared.json (get_node_info with params_file), calls_nav_primitives.json (the Nav2
# primitives), calls_diagnose.json (two `diagnose` sweeps, run before the graph changes) and
# calls_graph_diff.json (it starts graph_change.py, which changes the graph, so it runs last)
# are bench-only: the OmniSim kit runs calls.json alone.
/opt/tfenv/bin/python "$GT/drive.py" --out "$OUT/results" --bag "$BAG" \
    --calls "$GT/calls.json" "$GT/calls_declared.json" "$GT/calls_nav_primitives.json" "$GT/calls_diagnose.json" "$GT/calls_graph_diff.json" \
    --server-cmd /opt/tfenv/bin/topicforge --mode live --dds-backend cyclone \
    --env TOPICFORGE_LOG_LEVEL=WARNING > "$OUT/drive.log" 2>&1
DRIVE_RC=$?
tail -n 5 "$OUT/drive.log"
/opt/tfenv/bin/python "$GT/compare.py" --truth /bench/ground_truth.json \
    --results "$OUT/results" --bag-truth "$BAG" --strict-shape --expect-sampler rclpy \
    --out "$OUT/COMPARISON.md"
COMPARE_RC=$?
cp /bench/ground_truth.json /bench/publisher.log /bench/lifecycle.log /bench/record.log "$OUT/" 2>/dev/null
cp "$BAG/metadata.yaml" "$OUT/bag_metadata.yaml" 2>/dev/null
echo "bench: pytest rc=$PYTEST_RC cli pass rc=$CLI_RC rclpy pass rc=$RCLPY_RC drive rc=$DRIVE_RC compare rc=$COMPARE_RC"
[ "$PYTEST_RC" -eq 0 ] && [ "$CLI_RC" -eq 0 ] && [ "$RCLPY_RC" -eq 0 ] && [ "$DRIVE_RC" -eq 0 ] && [ "$COMPARE_RC" -eq 0 ]
