Stand-ins for `rclpy` and `rosidl_runtime_py`, just enough to run
`adapters/ros2_live/_sampler_helper.py` as a real process without ROS 2
(`tests/test_sampler_helper_process.py`). They publish nothing and join no domain.

Behavior comes from `FAKE_RCLPY_*` environment variables: `FAKE_RCLPY_MESSAGES` (how many messages
arrive), `FAKE_RCLPY_INTERVAL` (seconds between them), `FAKE_RCLPY_TRACE` (a file that receives one
line per call on the node, so a test can see a clean shutdown), `FAKE_RCLPY_FAIL` (`init` raises it),
`FAKE_RCLPY_NATIVE_STDOUT` (written to fd 1, as a native library would),
`FAKE_RCLPY_NATIVE_MISSING` (the native part fails to load), `FAKE_RCLPY_HANG_INIT` and
`FAKE_RCLPY_HANG_DESTROY` (seconds `init` and `destroy_node` block). A raw subscription gets the
message's index as text (`rclpy/serialization.py`); `FAKE_RCLPY_PUBLISHERS` is what `Node.count_publishers`
reports, default 1.
