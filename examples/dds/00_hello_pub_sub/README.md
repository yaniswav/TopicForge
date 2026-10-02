# 00 Hello, publisher and subscriber

**Concept:** the five DDS objects every program creates: participant, topic,
type, writer, reader. Writers and readers meet only if they are in the same
domain, on the same topic name, with the same type (and compatible QoS, see
example 02 and example 05).

## The story

A battery monitor publishes the battery voltage ten times a second. Two
dashboards subscribe. One has a typo in the topic name (`batery`). Both run,
neither prints an error, and the second one shows nothing.

| Program | Writes | Reads |
|---|---|---|
| `battery_monitor` (`publisher.py`) | `battery` | |
| `dashboard` (`subscriber.py`) | | `battery` |
| `dashboard_typo` (`subscriber.py --topic batery`) | | `batery` |

## The code

`publisher.py` and `subscriber.py` are about 50 lines each and commented line
by line. The key lines:

```python
@dataclass
class Battery(IdlStruct, typename="Battery"):   # the type
    seq: uint32
    voltage: float32

participant = DomainParticipant(args.domain, qos=Qos(Policy.EntityName(args.name)))
topic = Topic(participant, args.topic, Battery)       # name + type
writer = DataWriter(participant, topic, qos=Qos(Policy.History.KeepLast(10)))
writer.write(Battery(seq=seq, voltage=12.6 - 0.001 * seq))
```

The subscriber builds the same participant and topic, then a `DataReader`,
and waits for data with a `WaitSet` (sleep until a new sample exists, at most
500 ms) before `take()`-ing it. Each turn it also reads the reader's
**status**: `get_subscription_matched_status()` says how many writers the
reader is connected to.

Two things worth knowing:

- Do not put `from __future__ import annotations` in a file that defines an
  `IdlStruct`: Cyclone needs the real types, not strings.
- `EntityName` is optional, but it is what makes `dashboard` show up with its
  name instead of a GUID in TopicForge.

## Run it

Two terminals:

```
python publisher.py
python subscriber.py
```

Or all three programs and the TopicForge checks at once:

```
python run.py            # run and check
python run.py --hold     # keep the programs running, ask your own MCP client
```

## What you see inside

`python subscriber.py` (the first `seq` depends on when you start it: the publisher
was already running):

```
listening on 'battery' (domain 90)
matched writers: 1
rx seq=41 voltage=12.559
rx seq=42 voltage=12.558
rx seq=43 voltage=12.557
...
```

`python subscriber.py --topic batery`:

```
listening on 'batery' (domain 90)
matched writers: 0
```

Then nothing, forever. `matched writers: 0` is the only hint the program has.

## What TopicForge shows

```
[1] Who writes and who reads each topic?
    -> peek_dds_samples
    batery       writers: NOBODY
                 readers: dashboard_typo (Battery)
    battery      writers: battery_monitor (Battery)
                 readers: dashboard (Battery)
```

From outside, the typo is obvious: a topic with a reader and no writer.
Neither program knows about the other, but the discovery traffic on the bus
lists every reader and writer, and TopicForge only reads that.

## Ask your agent

> dashboard_typo shows nothing on DDS domain 0. Which topics have a reader but no writer?

## Remember

- A subscriber to a topic nobody writes is not an error in DDS. Check
  `matched writers` first.
- Topic names are case-sensitive strings. A typo creates a brand new topic.
- Next: example 05 (same programs, but the topic is right and the QoS is not).
