"""Publisher of example 06: writes the mission ONCE, then stays alive.

python publisher.py --topic mission_tl --durability transient_local
"""

# No `from __future__ import annotations` here: IdlStruct reads the real
# annotations of the dataclass, and string annotations would break it.
import argparse
import signal
import threading
from dataclasses import dataclass

from cyclonedds.core import Policy, Qos
from cyclonedds.domain import DomainParticipant
from cyclonedds.idl import IdlStruct
from cyclonedds.idl.types import int32
from cyclonedds.pub import DataWriter
from cyclonedds.topic import Topic
from cyclonedds.util import duration


@dataclass
class Mission(IdlStruct, typename="Mission"):  # the message: one number
    mission: int32


parser = argparse.ArgumentParser()
parser.add_argument("--domain", type=int, default=0)  # DDS domain id
parser.add_argument("--name", default="mission_control")  # shown by TopicForge
parser.add_argument("--topic", default="mission_tl")
parser.add_argument(
    "--durability", choices=["transient_local", "volatile"], default="transient_local"
)
parser.add_argument("--mission", type=int, default=42)
args = parser.parse_args()

stop = threading.Event()  # set by Ctrl+C or a termination request
for sig in ("SIGINT", "SIGTERM", "SIGBREAK"):  # SIGBREAK exists on Windows only
    if hasattr(signal, sig):
        signal.signal(getattr(signal, sig), lambda *_: stop.set())

# The participant is this program's membership of the domain; its name is
# what TopicForge's list_participants shows.
participant = DomainParticipant(args.domain, qos=Qos(Policy.EntityName(args.name)))
topic = Topic(participant, args.topic, Mission)

# The writer's QoS. TRANSIENT_LOCAL: keep the last sample (KeepLast 1) and
# hand it to every reader that joins later. VOLATILE: keep nothing.
durability = (
    Policy.Durability.TransientLocal
    if args.durability == "transient_local"
    else Policy.Durability.Volatile
)
qos = Qos(Policy.Reliability.Reliable(duration(seconds=1)), durability, Policy.History.KeepLast(1))
writer = DataWriter(participant, topic, qos=qos)
print(f"{args.name} up on domain {args.domain}, topic {args.topic}, {args.durability}", flush=True)

writer.write(Mission(mission=args.mission))  # written once, before anyone listens
print(f"wrote mission={args.mission}", flush=True)

while not stop.wait(0.5):  # stay alive so the writer keeps the sample
    pass
