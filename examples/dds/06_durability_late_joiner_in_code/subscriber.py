"""Subscriber of example 06: joins late, prints what it receives and DDS's verdict.

python subscriber.py --topic mission_tl --durability transient_local
"""

# No `from __future__ import annotations` here: IdlStruct reads the real
# annotations of the dataclass, and string annotations would break it.
import argparse
import signal
import threading
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
from cyclonedds.idl.types import int32
from cyclonedds.sub import DataReader
from cyclonedds.topic import Topic
from cyclonedds.util import duration

POLICIES = {2: "DURABILITY", 4: "DEADLINE", 8: "LIVELINESS", 11: "RELIABILITY"}


@dataclass
class Mission(IdlStruct, typename="Mission"):  # same type as the publisher's
    mission: int32


parser = argparse.ArgumentParser()
parser.add_argument("--domain", type=int, default=0)
parser.add_argument("--name", default="nav_planner")
parser.add_argument("--topic", default="mission_tl")
parser.add_argument(
    "--durability", choices=["transient_local", "volatile"], default="transient_local"
)
parser.add_argument("--delay", type=float, default=0.0)  # seconds to wait before joining
args = parser.parse_args()

stop = threading.Event()
for sig in ("SIGINT", "SIGTERM", "SIGBREAK"):
    if hasattr(signal, sig):
        signal.signal(getattr(signal, sig), lambda *_: stop.set())

stop.wait(args.delay)  # a late joiner: the publisher has long written already

participant = DomainParticipant(args.domain, qos=Qos(Policy.EntityName(args.name)))
topic = Topic(participant, args.topic, Mission)

# The reader's QoS: a TRANSIENT_LOCAL reader asks for the writer's kept
# samples; it needs a writer that is at least TRANSIENT_LOCAL itself.
durability = (
    Policy.Durability.TransientLocal
    if args.durability == "transient_local"
    else Policy.Durability.Volatile
)
qos = Qos(Policy.Reliability.Reliable(duration(seconds=1)), durability, Policy.History.KeepLast(1))
reader = DataReader(participant, topic, qos=qos)
print(f"{args.name} up on domain {args.domain}, topic {args.topic}, {args.durability}", flush=True)

# A WaitSet sleeps until the condition is true or the timeout expires, so
# the loop reacts to new data at once and to Ctrl+C within 500 ms.
waitset = WaitSet(participant)
waitset.attach(ReadCondition(reader, SampleState.NotRead | ViewState.Any | InstanceState.Any))

matched = None
incompatible = 0
while not stop.is_set():
    waitset.wait(duration(milliseconds=500))
    for sample in reader.take(N=100):
        if not isinstance(sample, Mission):  # no data: a notification about the writer
            continue
        print(f"rx mission={sample.mission}", flush=True)

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
