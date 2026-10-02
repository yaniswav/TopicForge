"""Publisher of example 08: a heartbeat at 10 Hz, optionally with a short lease.

python publisher.py --topic hb_leased --lease 1000
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
class Heartbeat(IdlStruct, typename="Heartbeat"):  # the message: a sequence number
    seq: uint32


parser = argparse.ArgumentParser()
parser.add_argument("--domain", type=int, default=0)  # DDS domain id
parser.add_argument("--name", default="safety_monitor")  # shown by TopicForge
parser.add_argument("--topic", default="hb_leased")
parser.add_argument("--rate-hz", type=float, default=10.0)
parser.add_argument("--lease", type=int, default=0)  # writer liveliness lease in ms; 0 = default
args = parser.parse_args()

stop = threading.Event()  # set by Ctrl+C or a termination request
for sig in ("SIGINT", "SIGTERM", "SIGBREAK"):  # SIGBREAK exists on Windows only
    if hasattr(signal, sig):
        signal.signal(getattr(signal, sig), lambda *_: stop.set())

participant = DomainParticipant(args.domain, qos=Qos(Policy.EntityName(args.name)))
topic = Topic(participant, args.topic, Heartbeat)

# Liveliness on a writer is a promise made to its readers: "if I am alive, you
# will hear from me within this lease". AUTOMATIC means DDS itself renews it
# while the process runs; when the process dies, the renewals stop. Without
# the policy the lease is infinite: readers are never told the writer is gone.
policies = [Policy.Reliability.Reliable(duration(seconds=1)), Policy.History.KeepLast(1)]
if args.lease:
    policies.append(Policy.Liveliness.Automatic(duration(milliseconds=args.lease)))
writer = DataWriter(participant, topic, qos=Qos(*policies))
lease = f"{args.lease} ms" if args.lease else "default (infinite)"
print(
    f"{args.name} up on domain {args.domain}, topic {args.topic}, writer lease: {lease}",
    flush=True,
)

seq = 0
last_print = 0.0
while not stop.wait(1.0 / args.rate_hz):
    writer.write(Heartbeat(seq=seq))
    if time.monotonic() - last_print >= 1.0:  # at most one line per second
        last_print = time.monotonic()
        print(f"wrote seq={seq}", flush=True)
    seq += 1
