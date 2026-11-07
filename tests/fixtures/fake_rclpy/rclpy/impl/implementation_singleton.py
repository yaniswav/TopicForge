"""Stand-in for the module that loads the native part of `rclpy`."""

import os

if os.environ.get("FAKE_RCLPY_NATIVE_MISSING"):
    raise ImportError("_rclpy_pybind11.cpython-312 isn't present")
rclpy_implementation = object()
