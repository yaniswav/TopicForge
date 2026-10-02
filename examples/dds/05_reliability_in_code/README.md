# 05 Reliability, in code

This is the RELIABLE / BEST_EFFORT mismatch of example 02, seen from the code
that causes it. A writer offers a reliability, a reader requests one, and the
request must not exceed the offer.

Read it together with [02 Why can't they talk?](../02_why_cant_they_talk/README.md):
02 finds the problem from outside, 05 shows the lines of code behind it and
what the program itself can observe.

## The programs

The LIDAR driver publishes `scan` with BEST_EFFORT (fine for a sensor: the
next scan replaces a lost one). The navigation planner subscribes with
RELIABLE. A third program, the scan logger, subscribes with the default.

| Program | Writes | Reads |
|---|---|---|
| `lidar_driver` (`publisher.py --best-effort`) | `scan` BEST_EFFORT | |
| `nav_planner` (`subscriber.py --reliable`) | | `scan` RELIABLE |
| `scan_logger` (`subscriber.py --best-effort`) | | `scan` BEST_EFFORT |

## The code

The only difference between the two subscribers is one QoS policy:

```python
# publisher.py: what the writer OFFERS
reliability = Policy.Reliability.BestEffort  # or Policy.Reliability.Reliable(duration(seconds=1))
writer = DataWriter(participant, topic, qos=Qos(reliability, Policy.History.KeepLast(10)))

# subscriber.py: what the reader REQUESTS
reader = DataReader(participant, topic, qos=Qos(reliability))

# subscriber.py: how DDS reports a refusal, polled each turn
status = reader.get_requested_incompatible_qos_status()
if status.total_count != refused:
    print(f"incompatible QoS from a writer: {POLICIES[status.last_policy_id]} ...")
```

Here `reliability` is `Policy.Reliability.BestEffort` or
`Policy.Reliability.Reliable(duration(seconds=1))` depending on
`--best-effort` / `--reliable`. The publisher defaults to `--reliable` and the
subscriber to `--best-effort`, so the programs work together unless you ask
for the broken combination.

The status tells the program that some writer was refused and which policy
was at fault (an id: 11 is Reliability), but not which writer.

## Run it

Two terminals:

```
python publisher.py --best-effort
python subscriber.py --reliable
```

Then try `python subscriber.py` (default, BEST_EFFORT) in a third terminal.
Or everything at once:

```
python run.py            # run and check
python run.py --hold     # keep the programs running, ask your own MCP client
```

## What you see inside

`python subscriber.py --reliable`, against the BEST_EFFORT publisher:

```
listening on 'scan' asking RELIABLE (domain 90)
matched writers: 0
incompatible QoS from a writer: RELIABILITY (id 11)
```

No `rx` line, ever. The default subscriber, on the same bus, prints one
`rx seq=... range_m=...` line per scan.

## What TopicForge adds

```
[1] Which writer and which reader are incompatible?
    -> detect_qos_mismatches
    scan: Reliability (incompatible): writer lidar_driver -> reader nav_planner
```

TopicForge adds what the DDS status cannot: the writer and the reader, by
name. The `scan_logger` is not reported, because a BEST_EFFORT request is
compatible with any offer.

## Fix

Make the driver `--reliable`, or the planner `--best-effort`. In your own
code, poll the incompatible-QoS status or attach a listener when a reader is
silent: it is the only local clue, and it names a policy id, not a peer.

Prompt to try:

> nav_planner never receives the scan topic on DDS domain 0. Why?
