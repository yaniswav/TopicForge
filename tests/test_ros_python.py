"""Which interpreter runs the sampler helper. Pure: no process is started."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from topicforge.adapters.common.ros_python import choose_ros_python, parse_shebang, read_shebang


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("#!/usr/bin/python3", "/usr/bin/python3"),
        ("#!/usr/bin/python3 -I", "/usr/bin/python3"),
        ("#! /opt/conda/envs/ros/bin/python3.11\n", "/opt/conda/envs/ros/bin/python3.11"),
        ("#!/usr/bin/env python3", "python3"),
        ("#!/usr/bin/env -S python3 -u", "python3"),
        ("#!/usr/bin/env PYTHONSAFEPATH=1 python3.10", "python3.10"),
        ('#!"C:\\Program Files\\Py\\python.exe"', "C:\\Program Files\\Py\\python.exe"),
        ("#!C:\\dev\\ros2\\python.exe", "C:\\dev\\ros2\\python.exe"),
        ("#!/bin/sh", None),
        ("#!/usr/bin/env bash", None),
        ("#!/usr/bin/env", None),
        ("#!", None),
        ("", None),
        ("@echo off", None),
        ("MZ\x90\x00", None),
    ],
)
def test_parse_shebang(line: str, expected: str | None) -> None:
    assert parse_shebang(line) == expected


def test_read_shebang_of_a_script_a_binary_and_a_missing_file(tmp_path: Path) -> None:
    script = tmp_path / "ros2"
    script.write_bytes(b"#!/usr/bin/python3\n# EASY-INSTALL\n")
    binary = tmp_path / "ros2.exe"
    binary.write_bytes(b"MZ\x90\x00\xff\xfe" * 2000)
    assert read_shebang(str(script)) == "/usr/bin/python3"
    assert read_shebang(str(binary)) is None
    assert read_shebang(str(tmp_path / "absent")) is None


def _which(*known: str) -> Callable[[str], str | None]:
    return lambda name: f"/resolved/{name}" if name in known else None


def test_the_override_comes_first_and_is_not_replaced_by_a_guess() -> None:
    assert choose_ros_python(
        "python3.10", "/opt/ros2", which=_which("python3.10"), shebang=lambda _: "/usr/bin/python3"
    ) == ("/resolved/python3.10", "TOPICFORGE_ROS_PYTHON")
    assert choose_ros_python("/no/such/python", "/opt/ros2", which=_which("python3")) == (
        "/no/such/python",
        "TOPICFORGE_ROS_PYTHON",
    )


def test_the_shebang_of_ros2_comes_next() -> None:
    assert choose_ros_python(
        None,
        "/opt/ros2",
        which=_which("/usr/bin/python3", "python3"),
        shebang=lambda _: "/usr/bin/python3",
    ) == ("/resolved//usr/bin/python3", "the shebang of `ros2`")


def test_a_dead_shebang_falls_through_to_python3_then_python() -> None:
    assert choose_ros_python(
        None, "/opt/ros2", which=_which("python3"), shebang=lambda _: "/gone/python3"
    ) == ("/resolved/python3", "`python3` on PATH")
    assert choose_ros_python(None, "ros2.exe", which=_which("python"), shebang=lambda _: None) == (
        "/resolved/python",
        "`python` on PATH",
    )


def test_no_interpreter_says_what_to_set() -> None:
    python, how = choose_ros_python(None, None, which=_which())
    assert python is None
    assert "TOPICFORGE_ROS_PYTHON" in how
