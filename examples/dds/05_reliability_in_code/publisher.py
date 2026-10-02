"""Publisher: sends a Scan message on the `scan` topic, 10 times a second.

python publisher.py [--domain 0] [--topic scan] [--rate-hz 10] [--reliable | --best-effort]
"""

# No `from __future__ import annotations` here: Cyclone reads the real field
# types of the IdlStruct at class creation, and string annotations break it.
import argparse
import signal
import threading
import time
from dataclasses import dataclass

from cyclonedds.core import Policy, Qos
from cyclonedds.domain import DomainParticipant
from cyclonedds.idl import IdlStruct
from cyclonedds.idl.types import float32, uint32
from cyclonedds.pub import DataWriter
from cyclonedds.topic import Topic
from cyclonedds.util import duration


@dataclass
class Scan(IdlStruct, typename="Scan"):
    seq: uint32
    range_m: float32


parser = argparse.ArgumentParser()
parser.add_argument("--domain", type=int, default=0)
parser.add_argument("--topic", default="scan")
parser.add_argument("--rate-hz", type=float, default=10.0)
parser.add_argument("--name", default="lidar_driver")
group = parser.add_mutually_exclusive_group()
group.add_argument("--reliable", dest="reliable", action="store_true", default=True)
group.add_argument("--best-effort", dest="reliable", action="store_false")
args = parser.parse_args()

stop = threading.Event()  # set by Ctrl+C or a termination request
for sig in ("SIGINT", "SIGTERM", "SIGBREAK"):  # SIGBREAK exists on Windows only
    if hasattr(signal, sig):
        signal.signal(getattr(signal, sig), lambda *_: stop.set())

participant = DomainParticipant(args.domain, qos=Qos(Policy.EntityName(args.name)))
topic = Topic(participant, args.topic, Scan)

# The writer OFFERS a reliability. A reader may only REQUEST the same or less:
# a RELIABLE reader cannot connect to a BEST_EFFORT writer.
if args.reliable:
    offered, reliability = "RELIABLE", Policy.Reliability.Reliable(duration(seconds=1))
else:
    offered, reliability = "BEST_EFFORT", Policy.Reliability.BestEffort
writer = DataWriter(participant, topic, qos=Qos(reliability, Policy.History.KeepLast(10)))

print(f"publishing Scan on '{args.topic}' offering {offered} (domain {args.domain})", flush=True)
seq, last_log = 0, 0.0
while not stop.is_set():
    writer.write(Scan(seq=seq, range_m=2.0 + 0.01 * seq))
    if time.monotonic() - last_log >= 1.0:  # log once a second, not every sample
        print(f"wrote seq={seq}", flush=True)
        last_log = time.monotonic()
    seq += 1
    stop.wait(1.0 / args.rate_hz)
