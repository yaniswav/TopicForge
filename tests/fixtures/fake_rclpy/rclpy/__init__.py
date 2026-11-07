"""Minimal stand-in for `rclpy`: see the README next to this package."""

import os
import time

pending_callbacks = []


def trace(line):
    path = os.environ.get("FAKE_RCLPY_TRACE")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def init(args=None):
    time.sleep(float(os.environ.get("FAKE_RCLPY_HANG_INIT", "0")))
    failure = os.environ.get("FAKE_RCLPY_FAIL")
    if failure:
        raise RuntimeError(failure)
    native = os.environ.get("FAKE_RCLPY_NATIVE_STDOUT")
    if native:
        os.write(1, native.encode("utf-8"))
    trace(f"init {args}")


def try_shutdown():
    trace("shutdown")


def spin_once(node, timeout_sec=None):
    if pending_callbacks:
        pending_callbacks.pop(0)()
        time.sleep(float(os.environ.get("FAKE_RCLPY_INTERVAL", "0")))
    else:
        time.sleep(timeout_sec or 0)
