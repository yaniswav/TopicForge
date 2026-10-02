"""Subscriber of example 08: prints heartbeats and tells when the writer is lost.

python subscriber.py --topic hb_leased
"""

# No `from __future__ import annotations` here: IdlStruct reads the real
# annotations of the dataclass, and string annotations would break it.
import argparse
import signal
import threading
import time
from dataclasses import dataclass

from cyclonedds.core import (
    InstanceState,
    Policy,
    Qos,
    ReadCondition,
    SampleState,
    ViewState,
    WaitSet,
)
from cyclonedds.domain import DomainParticipant
from cyclonedds.idl import IdlStruct
from cyclonedds.idl.types import uint32
from cyclonedds.sub import DataReader
from cyclonedds.topic import Topic
from cyclonedds.util import duration

POLICIES = {2: "DURABILITY", 4: "DEADLINE", 8: "LIVELINESS", 11: "RELIABILITY"}


@dataclass
class Heartbeat(IdlStruct, typename="Heartbeat"):  # same type as the publisher's
    seq: uint32


parser = argparse.ArgumentParser()
parser.add_argument("--domain", type=int, default=0)
parser.add_argument("--name", default="nav_planner")
parser.add_argument("--topic", default="hb_leased")
args = parser.parse_args()

stop = threading.Event()
for sig in ("SIGINT", "SIGTERM", "SIGBREAK"):
    if hasattr(signal, sig):
        signal.signal(getattr(signal, sig), lambda *_: stop.set())

participant = DomainParticipant(args.domain, qos=Qos(Policy.EntityName(args.name)))
topic = Topic(participant, args.topic, Heartbeat)

# The reader asks for no particular liveliness (the default), so it accepts
# any writer lease. It still gets told when a writer's lease runs out.
reader = DataReader(participant, topic, qos=Qos(Policy.Reliability.Reliable(duration(seconds=1))))
print(f"{args.name} up on domain {args.domain}, topic {args.topic}", flush=True)

# A WaitSet sleeps until the condition is true or the timeout expires, so
# the loop reacts to new data at once and to Ctrl+C within 500 ms.
waitset = WaitSet(participant)
waitset.attach(ReadCondition(reader, SampleState.NotRead | ViewState.Any | InstanceState.Any))

matched = None
incompatible = 0
last_rx = time.monotonic()  # when this reader last heard from the writer
lost = False
while not stop.is_set():
    waitset.wait(duration(milliseconds=500))
    for sample in reader.take(N=100):
        if not isinstance(sample, Heartbeat):  # no data: a notification about the writer
            continue
        print(f"rx seq={sample.seq}", flush=True)
        last_rx = time.monotonic()

    # Statuses: DDS's own account of the connection, polled each turn.
    count = reader.get_subscription_matched_status().current_count
    if count != matched:
        matched = count
        print(f"matched writers: {count}", flush=True)
    status = reader.get_requested_incompatible_qos_status()
    if status.total_count > incompatible:
        incompatible = status.total_count
        policy = POLICIES.get(status.last_policy_id, "POLICY")
        print(f"incompatible QoS from a writer: {policy} (id {status.last_policy_id})", flush=True)

    # not_alive_count: writers whose lease ran out (or that went away).
    liveliness = reader.get_liveliness_changed_status()
    if liveliness.not_alive_count > 0 and not lost:
        lost = True
        print(f"writer lost liveliness after {time.monotonic() - last_rx:.1f} s", flush=True)
    elif liveliness.alive_count > 0:
        lost = False
