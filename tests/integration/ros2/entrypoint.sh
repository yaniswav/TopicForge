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
trap 'kill $PUB_PID $BLOCKED_PID 2>/dev/null' EXIT

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

echo "bench: recording bag (8 s)"
# `ros2 bag record` has no --duration on Humble: stop it with SIGINT.
timeout -s INT 8 ros2 bag record -o "$BAG" \
    /clock /scan /cmd_vel_out /camera/image_raw /robot_description_lite \
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
echo "bench: ground truth (drive.py, compare.py)"
GT=scripts/ground_truth
rm -rf "$OUT/results"
/opt/tfenv/bin/python "$GT/drive.py" --out "$OUT/results" --bag "$BAG" \
    --server-cmd /opt/tfenv/bin/topicforge --mode live --dds-backend cyclone \
    --env TOPICFORGE_LOG_LEVEL=WARNING > "$OUT/drive.log" 2>&1
DRIVE_RC=$?
tail -n 5 "$OUT/drive.log"
/opt/tfenv/bin/python "$GT/compare.py" --truth /bench/ground_truth.json \
    --results "$OUT/results" --bag-truth "$BAG" --strict-shape \
    --out "$OUT/COMPARISON.md"
COMPARE_RC=$?
cp /bench/ground_truth.json /bench/publisher.log /bench/record.log "$OUT/" 2>/dev/null
cp "$BAG/metadata.yaml" "$OUT/bag_metadata.yaml" 2>/dev/null
echo "bench: pytest rc=$PYTEST_RC drive rc=$DRIVE_RC compare rc=$COMPARE_RC"
[ "$PYTEST_RC" -eq 0 ] && [ "$DRIVE_RC" -eq 0 ] && [ "$COMPARE_RC" -eq 0 ]
