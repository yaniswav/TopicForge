# 06 Durability and the late joiner, in code

**Concept:** Durability, written as code you can read. Example 04 showed the
mismatch from the outside; here you see the two programs, what the late
subscriber prints, and the one variant TopicForge cannot see.

## The story

Mission control writes the robot's mission **once**, then idles. The
navigation planner boots afterwards. Whether it ever gets the mission depends
on what the writer kept, and on what the reader asked for. Three topics play
the three cases:

| Topic | Writer | Reader | Outcome |
|---|---|---|---|
| `mission_tl` | TRANSIENT_LOCAL | TRANSIENT_LOCAL | mission delivered |
| `mission_volatile_writer` | VOLATILE | TRANSIENT_LOCAL | no match (incompatible) |
| `mission_volatile_both` | VOLATILE | VOLATILE | match, but nothing to receive |

This is the only example where start order matters: the publishers must have
written before the subscribers exist.

## The code

[`publisher.py`](publisher.py) and [`subscriber.py`](subscriber.py) are about
50 lines each and commented line by line. The key lines:

```python
# publisher.py: keep the last sample and hand it to readers that join later
qos = Qos(Policy.Reliability.Reliable(duration(seconds=1)), durability, Policy.History.KeepLast(1))
writer = DataWriter(participant, topic, qos=qos)
writer.write(Mission(mission=42))            # written once, before anyone listens
```

```python
# subscriber.py: a TRANSIENT_LOCAL reader asks for the writer's kept samples
waitset.attach(ReadCondition(reader, SampleState.NotRead | ViewState.Any | InstanceState.Any))
...
status = reader.get_requested_incompatible_qos_status()   # DDS's verdict on the match
```

Every sample the reader takes is printed as `rx mission=42`. Each turn it also
polls two statuses and prints a line when they change: `matched writers: <n>`
and `incompatible QoS from a writer: <POLICY> (id <n>)`. The `--delay` option
of the subscriber only exists for `--hold`; check mode starts the subscribers
itself once the publishers have printed `wrote mission=42`.

The program has no `from __future__ import annotations`: `IdlStruct` reads the
real annotations of the dataclass, and string annotations would break it.

## Run it

By hand, in separate terminals (publishers first, wait for `wrote mission=42`,
then the subscribers):

```
python publisher.py --topic mission_tl --durability transient_local
python publisher.py --topic mission_volatile_writer --durability volatile --name pub_1
python publisher.py --topic mission_volatile_both --durability volatile --name pub_2

python subscriber.py --topic mission_tl --durability transient_local --name sub_0
python subscriber.py --topic mission_volatile_writer --durability transient_local --name sub_1
python subscriber.py --topic mission_volatile_both --durability volatile --name sub_2
```

Each program takes `--domain N` (default 0) and `--name`. Or all at once, with
checks:

```
python run.py            # run and check
python run.py --hold     # keep the programs running, ask your own MCP client
```

## What you see inside

```
sub_0: rx mission=42
sub_0: matched writers: 1
sub_1: matched writers: 0
sub_1: incompatible QoS from a writer: DURABILITY (id 2)
sub_2: matched writers: 1
```

- `sub_0` gets the mission although it joined after it was written: the
  writer kept it, DDS handed it over on match.
- `sub_1` never matches. DDS tells the reader why: DURABILITY, policy id 2.
- `sub_2` matches, and receives nothing. No error, no status: the writer
  simply had nothing to hand over.

## What TopicForge shows

```
detect_qos_mismatches
    mission_volatile_writer: Durability (incompatible): writer pub_1 -> reader sub_1
```

- `mission_tl` is compatible and not reported. Same for `mission_volatile_both`.
- The Durability mismatch is the same request/offered rule as in example 04.

## What this does not do

TopicForge **cannot see the third case**. `mission_volatile_both` is not a QoS
incompatibility: the endpoints are compatible, they are matched, and the
subscriber simply joined too late for a writer that keeps nothing. TopicForge
reads declared QoS, and the declarations here are consistent. Only the
subscriber, from inside, knows it received nothing. It also cannot tell that
a writer already wrote and finished.

## Ask your agent

> Three mission topics on DDS domain 0 (mission_tl, mission_volatile_writer,
> mission_volatile_both): their subscribers joined late. Which ones can never
> get the mission, and why?

A correct answer names `mission_volatile_writer` and its Durability mismatch,
and should not claim anything about `mission_volatile_both` from TopicForge
alone.

## Remember

- Configuration-like data (missions, maps, parameters) wants TRANSIENT_LOCAL on
  the **writer**, with a KeepLast depth big enough to hold what a latecomer
  needs.
- A reader can only ask for what the writer offers. Asking for more means no
  match at all (example 04).
- Both VOLATILE is legal and silent: a late joiner just misses the past.
  "Matched" does not mean "received everything".
