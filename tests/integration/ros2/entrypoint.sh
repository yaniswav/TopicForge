#!/usr/bin/env bash
# Runs inside the bench container: publisher, bag recording, then pytest.
# No `set -u`: the ROS setup scripts read unset variables.
source "/opt/ros/${ROS_DISTRO}/setup.bash"
HERE=/opt/topicforge/tests/integration/ros2
BAG="${TOPICFORGE_BENCH_BAG:-/bench/bag}"
mkdir -p "$(dirname "$BAG")"
rm -rf "$BAG"

echo "bench: ROS_DISTRO=${ROS_DISTRO} RMW_IMPLEMENTATION=${RMW_IMPLEMENTATION:-default}"
python3 "$HERE/publisher.py" > /bench/publisher.log 2>&1 &
PUB_PID=$!
trap 'kill $PUB_PID 2>/dev/null' EXIT

for _ in $(seq 1 60); do
    ros2 topic list 2>/dev/null | grep -qx /scan && break
    sleep 0.5
done
if ! ros2 topic list 2>/dev/null | grep -qx /scan; then
    echo "bench: publisher never appeared"
    cat /bench/publisher.log
    exit 2
fi

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
