# 07 The deadline, declared and kept, in code

Concept: Deadline. A reader's Deadline is a demand ("a new sample at least
every N ms"). A writer's Deadline is a promise. Two separate questions: is the
promise declared (DDS checks it at connection time) and is it kept
(DDS only counts misses at runtime).

## The story

The navigation planner wants a new scan every 200 ms.

| Case | Writer | Reader | Outcome |
|---|---|---|---|
| A, `scan_a` | no deadline (no promise), 10 Hz | requires 200 ms | no match |
| B, `scan_b` | promises 100 ms, but writes at 2 Hz | requires 200 ms | match, deadlines missed |

## The code

[`publisher.py`](publisher.py) and [`subscriber.py`](subscriber.py) are about
60 lines each and commented line by line. The key lines:

```python
# publisher.py: the promise
policies.append(Policy.Deadline(duration(milliseconds=args.deadline_ms)))
...
missed = writer.get_offered_deadline_missed_status().total_count   # it knows when it breaks it
```

```python
# subscriber.py: the demand, and the status that counts misses
policies.append(Policy.Deadline(duration(milliseconds=args.deadline_ms)))
...
total = reader.get_requested_deadline_missed_status().total_count
```

The subscriber waits on a `WaitSet` (bounded to 500 ms), takes samples, and
polls its statuses each turn, printing `rx seq=<n>`, `matched writers: <n>`,
`incompatible QoS from a writer: <POLICY> (id <n>)` and
`deadline missed total=<n>`. No `from __future__ import annotations`:
`IdlStruct` reads the real annotations of the dataclass.

## Run it

By hand:

```
python publisher.py --name pub_a --topic scan_a
python subscriber.py --name sub_a --topic scan_a --deadline-ms 200

python publisher.py --name pub_b --topic scan_b --deadline-ms 100 --rate-hz 2
python subscriber.py --name sub_b --topic scan_b --deadline-ms 200
```

Each program takes `--domain N` (default 0). Or all four at once, with checks:

```
python run.py            # run and check
python run.py --hold     # keep the programs running, ask your own MCP client
```

## What you see inside

```
sub_a: matched writers: 0
sub_a: incompatible QoS from a writer: DEADLINE (id 4)

sub_b: rx seq=14
sub_b: deadline missed total=30
sub_b: rx seq=15
```

- A: the reader never receives anything and says why: DEADLINE, policy id 4.
- B: samples arrive, but every 500 ms gap is longer than the 200 ms the reader
  demanded, so the missed counter keeps climbing. The writer also notices
  (`promise broken: offered deadline missed total=<n>`).

## What TopicForge shows

```
detect_qos_mismatches
    scan_a: Deadline (incompatible): writer pub_a -> reader sub_a
```

TopicForge reports case A (same rule as example 13) and nothing for `scan_b`.

## What TopicForge cannot see

Case B: TopicForge reads what each side declares, not what it does. `scan_b` declares a 100 ms promise and a 200 ms demand: those
are compatible, so there is no mismatch to report, even though the writer
breaks its promise on every sample. Missed deadlines are runtime
behavior, visible only to the endpoints themselves (the status counters above).
TopicForge's `topic_metrics` frequency is its own peek cadence and is no
substitute.

## Ask your agent

> Two scan topics on DDS domain 0, scan_a and scan_b. The planner reads both and
> wants a new scan every 200 ms. Is each one really delivering on its deadline?

A correct answer finds the mismatch on `scan_a`, and says it cannot verify
that `scan_b` actually keeps its promise.

DDS checks a declared Deadline once, at connection time. Whether it is kept
is something only a counter inside the reader can tell you, so use the
reader's `deadline missed` status in your own node as the runtime watchdog. A
clean mismatch check proves nothing about timing.
