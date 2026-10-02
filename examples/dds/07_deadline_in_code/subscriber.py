"""Subscriber of example 07: prints what it receives and the deadline verdicts.

python subscriber.py --topic scan_b --deadline-ms 200
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
from cyclonedds.idl.types import uint32
from cyclonedds.sub import DataReader
from cyclonedds.topic import Topic
from cyclonedds.util import duration

POLICIES = {2: "DURABILITY", 4: "DEADLINE", 8: "LIVELINESS", 11: "RELIABILITY"}


@dataclass
class Scan(IdlStruct, typename="Scan"):  # same type as the publisher's
    seq: uint32


parser = argparse.ArgumentParser()
parser.add_argument("--domain", type=int, default=0)
parser.add_argument("--name", default="nav_planner")
parser.add_argument("--topic", default="scan")
parser.add_argument("--deadline-ms", type=int, default=200)  # the demand; 0 = no demand
args = parser.parse_args()

stop = threading.Event()
for sig in ("SIGINT", "SIGTERM", "SIGBREAK"):
    if hasattr(signal, sig):
        signal.signal(getattr(signal, sig), lambda *_: stop.set())

participant = DomainParticipant(args.domain, qos=Qos(Policy.EntityName(args.name)))
topic = Topic(participant, args.topic, Scan)

# Deadline on a reader is a DEMAND: "I want a new sample at least every N ms,
# and I want to be told when it does not come". A writer that promises
# nothing cannot satisfy it, and DDS then refuses to connect the two.
policies = [Policy.Reliability.Reliable(duration(seconds=1)), Policy.History.KeepLast(1)]
if args.deadline_ms:
    policies.append(Policy.Deadline(duration(milliseconds=args.deadline_ms)))
reader = DataReader(participant, topic, qos=Qos(*policies))
demand = f"{args.deadline_ms} ms" if args.deadline_ms else "none"
print(
    f"{args.name} up on domain {args.domain}, topic {args.topic}, deadline required: {demand}",
    flush=True,
)

# A WaitSet sleeps until the condition is true or the timeout expires, so
# the loop reacts to new data at once and to Ctrl+C within 500 ms.
waitset = WaitSet(participant)
waitset.attach(ReadCondition(reader, SampleState.NotRead | ViewState.Any | InstanceState.Any))

matched = None
incompatible = 0
missed = 0
while not stop.is_set():
    waitset.wait(duration(milliseconds=500))
    for sample in reader.take(N=100):
        if not isinstance(sample, Scan):  # no data: a notification about the writer
            continue
        print(f"rx seq={sample.seq}", flush=True)

    # Statuses: DDS's own account of the connection. They are POLLED here, every
    # 500 ms turn, not triggered. The event-driven alternative is to attach the
    # reader's StatusCondition to the WaitSet, so that the wait wakes up on a
    # status change instead of on the timeout.
    count = reader.get_subscription_matched_status().current_count
    if count != matched:
        matched = count
        print(f"matched writers: {count}", flush=True)
    status = reader.get_requested_incompatible_qos_status()
    if status.total_count > incompatible:
        incompatible = status.total_count
        policy = POLICIES.get(status.last_policy_id, "POLICY")
        print(f"incompatible QoS from a writer: {policy} (id {status.last_policy_id})", flush=True)
    total = reader.get_requested_deadline_missed_status().total_count
    if total > missed:
        missed = total
        print(f"deadline missed total={total}", flush=True)
