"""Publisher: sends a Battery message on the `battery` topic, 10 times a second.

python publisher.py [--domain 0] [--topic battery] [--rate-hz 10]
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


@dataclass
class Battery(IdlStruct, typename="Battery"):  # the type: its name goes on the wire
    seq: uint32
    voltage: float32


parser = argparse.ArgumentParser()
parser.add_argument("--domain", type=int, default=0)
parser.add_argument("--topic", default="battery")
parser.add_argument("--rate-hz", type=float, default=10.0)
parser.add_argument("--name", default="battery_monitor")
args = parser.parse_args()

stop = threading.Event()  # set by Ctrl+C or a termination request
for sig in ("SIGINT", "SIGTERM", "SIGBREAK"):  # SIGBREAK exists on Windows only
    if hasattr(signal, sig):
        signal.signal(getattr(signal, sig), lambda *_: stop.set())

# 1. Participant: our membership of one DDS domain. The EntityName is the
#    label tools like TopicForge show for this program.
participant = DomainParticipant(args.domain, qos=Qos(Policy.EntityName(args.name)))
# 2. Topic: a name plus a type. Writers and readers meet on both.
topic = Topic(participant, args.topic, Battery)
# 3. Writer: KeepLast(10) history, the default QoS otherwise.
writer = DataWriter(participant, topic, qos=Qos(Policy.History.KeepLast(10)))

print(f"publishing Battery on '{args.topic}' (domain {args.domain})", flush=True)
seq, last_log = 0, 0.0
while not stop.is_set():
    writer.write(Battery(seq=seq, voltage=12.6 - 0.001 * seq))  # 4. send one sample
    if time.monotonic() - last_log >= 1.0:  # log once a second, not every sample
        print(f"wrote seq={seq}", flush=True)
        last_log = time.monotonic()
    seq += 1
    stop.wait(1.0 / args.rate_hz)
