"""Subscriber: prints every Battery message it receives, and DDS status changes.

python subscriber.py [--domain 0] [--topic battery]
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
class Battery(IdlStruct, typename="Battery"):  # must match the publisher's type
    seq: uint32
    voltage: float32


parser = argparse.ArgumentParser()
parser.add_argument("--domain", type=int, default=0)
parser.add_argument("--topic", default="battery")
parser.add_argument("--name", default="dashboard")
args = parser.parse_args()

running = True


def stop(*_: object) -> None:
    global running
    running = False


for sig in ("SIGINT", "SIGTERM", "SIGBREAK"):  # SIGBREAK exists on Windows only
    if hasattr(signal, sig):
        signal.signal(getattr(signal, sig), stop)

# Same three steps as the publisher: participant, topic, reader.
participant = DomainParticipant(args.domain, qos=Qos(Policy.EntityName(args.name)))
topic = Topic(participant, args.topic, Battery)
reader = DataReader(participant, topic)  # default QoS: BEST_EFFORT, KeepLast(1)

# A WaitSet sleeps until a condition is true or the timeout passes (500 ms
# here, so Ctrl+C is noticed quickly). The condition: "a new sample exists".
waitset = WaitSet(participant)
waitset.attach(ReadCondition(reader, SampleState.NotRead | ViewState.Any | InstanceState.Any))

print(f"listening on '{args.topic}' (domain {args.domain})", flush=True)
matched = None
while running:
    waitset.wait(duration(milliseconds=500))
    # Statuses first: how DDS tells you what happened to the connection.
    now = reader.get_subscription_matched_status().current_count
    if now != matched:
        matched = now
        print(f"matched writers: {matched}", flush=True)
    for sample in reader.take(N=100):  # take() removes the samples from the reader
        if not isinstance(sample, Battery):  # no data: a notification about the writer
            continue
        print(f"rx seq={sample.seq} voltage={sample.voltage:.3f}", flush=True)
