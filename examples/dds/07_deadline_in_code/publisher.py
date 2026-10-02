"""Publisher of example 07: writes numbered scans at a fixed rate.

python publisher.py --topic scan_b --deadline-ms 100 --rate-hz 2
"""

# No `from __future__ import annotations` here: IdlStruct reads the real
# annotations of the dataclass, and string annotations would break it.
import argparse
import signal
import threading
import time
from dataclasses import dataclass

from cyclonedds.core import Policy, Qos
from cyclonedds.domain import DomainParticipant
from cyclonedds.idl import IdlStruct
from cyclonedds.idl.types import uint32
from cyclonedds.pub import DataWriter
from cyclonedds.topic import Topic
from cyclonedds.util import duration


@dataclass
class Scan(IdlStruct, typename="Scan"):  # the message: a sequence number
    seq: uint32


parser = argparse.ArgumentParser()
parser.add_argument("--domain", type=int, default=0)  # DDS domain id
parser.add_argument("--name", default="lidar_driver")  # shown by TopicForge
parser.add_argument("--topic", default="scan")
parser.add_argument("--rate-hz", type=float, default=10.0)  # how often it really writes
parser.add_argument("--deadline-ms", type=int, default=0)  # the promise; 0 = no promise
args = parser.parse_args()

stop = threading.Event()  # set by Ctrl+C or a termination request
for sig in ("SIGINT", "SIGTERM", "SIGBREAK"):  # SIGBREAK exists on Windows only
    if hasattr(signal, sig):
        signal.signal(getattr(signal, sig), lambda *_: stop.set())

participant = DomainParticipant(args.domain, qos=Qos(Policy.EntityName(args.name)))
topic = Topic(participant, args.topic, Scan)

# Deadline on a writer is a PROMISE: "I write every instance at least every
# N ms". Leaving it out promises nothing (infinite deadline).
policies = [Policy.Reliability.Reliable(duration(seconds=1)), Policy.History.KeepLast(1)]
if args.deadline_ms:
    policies.append(Policy.Deadline(duration(milliseconds=args.deadline_ms)))
writer = DataWriter(participant, topic, qos=Qos(*policies))
promise = f"{args.deadline_ms} ms" if args.deadline_ms else "none"
print(
    f"{args.name} up on domain {args.domain}, topic {args.topic}, "
    f"deadline promised: {promise}, writing at {args.rate_hz:g} Hz",
    flush=True,
)

seq = 0
broken = 0
last_print = 0.0
while not stop.wait(1.0 / args.rate_hz):
    writer.write(Scan(seq=seq))
    if time.monotonic() - last_print >= 1.0:  # at most one line per second
        last_print = time.monotonic()
        print(f"wrote seq={seq}", flush=True)
    seq += 1

    # The writer knows when it breaks its own promise.
    missed = writer.get_offered_deadline_missed_status().total_count
    if missed > broken:
        broken = missed
        print(f"promise broken: offered deadline missed total={missed}", flush=True)
