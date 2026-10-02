"""Subscriber: prints every Scan message it receives, and DDS status changes.

python subscriber.py [--domain 0] [--topic scan] [--reliable | --best-effort]
"""

# No `from __future__ import annotations` here: it breaks IdlStruct.
import argparse
import signal
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
from cyclonedds.idl.types import float32, uint32
from cyclonedds.sub import DataReader
from cyclonedds.topic import Topic
from cyclonedds.util import duration


@dataclass
class Scan(IdlStruct, typename="Scan"):  # must match the publisher's type
    seq: uint32
    range_m: float32


# Policy ids as Cyclone reports them in the incompatible-QoS status.
POLICIES = {2: "DURABILITY", 4: "DEADLINE", 8: "LIVELINESS", 10: "PARTITION", 11: "RELIABILITY"}

parser = argparse.ArgumentParser()
parser.add_argument("--domain", type=int, default=0)
parser.add_argument("--topic", default="scan")
parser.add_argument("--name", default="nav_planner")
group = parser.add_mutually_exclusive_group()
group.add_argument("--reliable", dest="reliable", action="store_true", default=False)
group.add_argument("--best-effort", dest="reliable", action="store_false")
args = parser.parse_args()

running = True


def stop(*_: object) -> None:
    global running
    running = False


for sig in ("SIGINT", "SIGTERM", "SIGBREAK"):  # SIGBREAK exists on Windows only
    if hasattr(signal, sig):
        signal.signal(getattr(signal, sig), stop)

participant = DomainParticipant(args.domain, qos=Qos(Policy.EntityName(args.name)))
topic = Topic(participant, args.topic, Scan)

# The reader REQUESTS a reliability: it must be the writer's offer or less.
if args.reliable:
    asked, reliability = "RELIABLE", Policy.Reliability.Reliable(duration(seconds=1))
else:
    asked, reliability = "BEST_EFFORT", Policy.Reliability.BestEffort
reader = DataReader(participant, topic, qos=Qos(reliability))

# Sleep until a new sample exists, at most 500 ms (so Ctrl+C is noticed).
waitset = WaitSet(participant)
waitset.attach(ReadCondition(reader, SampleState.NotRead | ViewState.Any | InstanceState.Any))

print(f"listening on '{args.topic}' asking {asked} (domain {args.domain})", flush=True)
matched, refused = None, 0
while running:
    waitset.wait(duration(milliseconds=500))
    now = reader.get_subscription_matched_status().current_count
    if now != matched:
        matched = now
        print(f"matched writers: {matched}", flush=True)
    # DDS says THAT a writer was refused and which policy, not which writer:
    # TopicForge names both ends (detect_qos_mismatches).
    status = reader.get_requested_incompatible_qos_status()
    if status.total_count != refused:
        refused = status.total_count
        policy = POLICIES.get(status.last_policy_id, "?")
        print(f"incompatible QoS from a writer: {policy} (id {status.last_policy_id})", flush=True)
    # Then the data that arrived during the wait.
    for sample in reader.take(N=100):
        if not isinstance(sample, Scan):  # no data: a notification about the writer
            continue
        print(f"rx seq={sample.seq} range_m={sample.range_m:.2f}", flush=True)
