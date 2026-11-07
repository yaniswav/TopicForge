#!/usr/bin/env python3
"""Short-lived sampler: one subscription, `--count` messages or a deadline, then a clean exit.

Started by path under the distro's interpreter by `helper_stream.py`, one process per
`sample_messages` call. It must stay importable there: standard library, `rclpy` and
`rosidl_runtime_py` only, never `topicforge`. The flags mirror `ros2 topic echo`, and each
message is the text that command prints (`message_to_yaml`), so the server decodes it with the
same code. It only reads: the one topic subscription is the only entity it asks for, next to
what `Node` itself creates (`SECURITY.md` lists them).

Output on stdout is binary frames (see `adapters/common/sampler_frames.py`, which decodes them).
Native libraries may print to fd 1, so the frames use a private copy of it and fd 1 becomes
stderr. Stdin is a pipe the parent holds open: EOF ends the run, so a dead parent cannot leak
this process. A clean exit tells the graph at once; a killed reader stays matched on the writers
for a while, which is why the parent closes stdin first and kills only as a last resort.

Exit codes: 0 got `--count`, 3 deadline, 4 stdin EOF or closed stdout, 5 setup error (one line
on stderr).
"""

import argparse
import copy
import json
import os
import struct
import sys
import threading
import time

T0 = time.monotonic()

EXIT_COUNT = 0
EXIT_DEADLINE = 3
EXIT_STOPPED = 4
EXIT_ERROR = 5

# How long a clean shutdown may take before the guards exit hard. Below the parent's stop wait.
GRACE_S = 0.8

_FRAME_HEADER = struct.Struct(">2sBBII")
_MAGIC = b"TF"
_VERSION = 1
KIND_HELLO = ord("h")
KIND_MESSAGE = ord("m")
KIND_DROPPED = ord("d")


def encode_frame(kind, meta, body=b""):
    """One frame: fixed header, JSON metadata, body."""
    head = json.dumps(meta, separators=(",", ":")).encode("utf-8")
    return _FRAME_HEADER.pack(_MAGIC, _VERSION, kind, len(head), len(body)) + head + body


class OutputClosed(Exception):
    """The parent closed the pipe the frames go to."""


def write_all(fd, data):
    """Write every byte; a pipe may take only part of a large frame at once."""
    view = memoryview(data)
    try:
        while view:
            view = view[os.write(fd, view) :]
    except OSError as exc:
        raise OutputClosed from exc


def parse_args(argv):
    """The `ros2 topic echo` flags the server uses, plus the run's limits."""
    p = argparse.ArgumentParser()
    p.add_argument("topic")
    p.add_argument("--type", required=True, help="pkg/msg/Name")
    p.add_argument("--count", type=int, default=1)
    p.add_argument("--deadline", type=float, required=True, help="seconds from process start")
    p.add_argument("--max-chars", type=int, default=None, help="longer messages are only counted")
    p.add_argument("--qos-reliability", choices=["reliable", "best_effort"], default="best_effort")
    p.add_argument("--qos-durability", choices=["transient_local", "volatile"], default="volatile")
    p.add_argument("--qos-history", choices=["keep_last"], default=None)
    p.add_argument("--qos-depth", type=int, default=None, help="else the sensor_data depth")
    p.add_argument("--truncate-length", type=int, default=128)
    p.add_argument("--full-length", action="store_true")
    p.add_argument("--no-arr", action="store_true")
    return p.parse_args(argv)


def start_guards(deadline_at, stop):
    """Stdin EOF ends the run; both guards exit hard if rclpy blocks past the grace."""

    def watch_stdin():
        try:
            while os.read(0, 4096):
                pass
        except OSError:
            pass
        stop["why"] = stop["why"] or EXIT_STOPPED
        time.sleep(GRACE_S)
        os._exit(EXIT_STOPPED)

    def watch_deadline():
        time.sleep(max(0.0, deadline_at - time.monotonic()) + GRACE_S)
        os._exit(EXIT_DEADLINE)

    for fn in (watch_stdin, watch_deadline):
        threading.Thread(target=fn, daemon=True).start()


def make_node():
    """The node: hidden name, no `/rosout`, no parameter or type-description services."""
    import rclpy
    from rclpy.node import Node
    from rclpy.parameter import Parameter

    rclpy.init(args=[])
    overrides = []
    try:  # Iron and later; Humble has no such module nor service
        from rclpy.type_description_service import START_TYPE_DESCRIPTION_SERVICE_PARAM

        overrides.append(Parameter(START_TYPE_DESCRIPTION_SERVICE_PARAM, value=False))
    except ImportError:
        pass
    return Node(
        f"_topicforge_sampler_{os.getpid()}",
        enable_rosout=False,
        start_parameter_services=False,
        use_global_arguments=False,
        parameter_overrides=overrides,
    )


def reader_qos(args):
    """`sensor_data` with the flags' reliability, durability and depth, as `ros2 topic echo`."""
    from rclpy.qos import (
        QoSDurabilityPolicy,
        QoSHistoryPolicy,
        QoSReliabilityPolicy,
        qos_profile_sensor_data,
    )

    profile = copy.copy(qos_profile_sensor_data)
    profile.reliability = (
        QoSReliabilityPolicy.RELIABLE
        if args.qos_reliability == "reliable"
        else QoSReliabilityPolicy.BEST_EFFORT
    )
    profile.durability = (
        QoSDurabilityPolicy.TRANSIENT_LOCAL
        if args.qos_durability == "transient_local"
        else QoSDurabilityPolicy.VOLATILE
    )
    if args.qos_depth is not None:
        profile.history = QoSHistoryPolicy.KEEP_LAST
        profile.depth = args.qos_depth
    return profile


def run(args, out_fd, stop, nodes):
    """Subscribe and forward messages; returns the exit code."""
    import rclpy
    from rosidl_runtime_py import message_to_yaml
    from rosidl_runtime_py.utilities import get_message

    deadline_at = T0 + args.deadline
    node = make_node()
    nodes.append(node)
    cls = get_message(args.type)
    truncate = None if args.full_length else args.truncate_length
    got = [0]

    def on_message(msg):
        if got[0] >= args.count:
            return
        wall_ns = time.time_ns()
        text = message_to_yaml(msg, truncate_length=truncate, no_arr=args.no_arr)
        if args.max_chars is not None and len(text) > args.max_chars:
            write_all(out_fd, encode_frame(KIND_DROPPED, {"wall_ns": wall_ns}))
            return
        meta = {"wall_ns": wall_ns, "encoding": "yaml"}
        write_all(out_fd, encode_frame(KIND_MESSAGE, meta, text.encode("utf-8")))
        got[0] += 1

    node.create_subscription(cls, args.topic, on_message, reader_qos(args))
    write_all(out_fd, encode_frame(KIND_HELLO, {"pid": os.getpid(), "type": args.type}))
    while got[0] < args.count and not stop["why"]:
        left = deadline_at - time.monotonic()
        if left <= 0:
            return EXIT_DEADLINE
        rclpy.spin_once(node, timeout_sec=min(left, 0.05))
    return stop["why"] or EXIT_COUNT


def probe():
    """Exit 0 when this interpreter can sample. Joins no domain: imports only.

    The native part of `rclpy` loads in the second import: that is where a Python of another
    minor version than the distro's fails.
    """
    try:
        import rclpy  # noqa: F401
        from rclpy.impl.implementation_singleton import rclpy_implementation  # noqa: F401
        from rosidl_runtime_py import message_to_yaml  # noqa: F401
        from rosidl_runtime_py.utilities import get_message  # noqa: F401
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_COUNT


def drop_script_dir():
    """This file's directory must not shadow a message package or a `yaml`."""
    here = os.path.dirname(os.path.abspath(__file__))
    if sys.path and os.path.abspath(sys.path[0] or os.curdir) == here:
        sys.path.pop(0)


def claim_stdout():
    """Keep fd 1 for the frames under a new number; whatever else prints there goes to stderr."""
    out_fd = os.dup(1)
    os.dup2(2, 1)
    return out_fd


def main():
    drop_script_dir()
    if sys.argv[1:] == ["--probe"]:
        sys.stderr.flush()
        os._exit(probe())
    args = parse_args(sys.argv[1:])
    out_fd = claim_stdout()
    stop = {"why": 0}
    start_guards(T0 + args.deadline, stop)
    nodes = []
    try:
        code = run(args, out_fd, stop, nodes)
    except (OutputClosed, KeyboardInterrupt):
        code = EXIT_STOPPED
    except Exception as exc:  # one line for the parent, not a traceback
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        code = EXIT_ERROR
    try:
        if nodes:  # tell the graph, so writers drop this reader now
            import rclpy

            nodes[0].destroy_node()
            rclpy.try_shutdown()
    finally:
        os._exit(code)


if __name__ == "__main__":
    main()
