# 08 A crash, seen from inside and from outside

Concept: two clocks for the same crash. A writer's Liveliness lease is
chosen by the application and tells its readers within that lease. The
participant lease of discovery is coarse (10 s by default in Cyclone DDS)
and is what TopicForge, an outside observer, relies on.

## The story

Two heartbeat publishers get killed: no goodbye on the bus.

| Program | Topic | Writer liveliness lease |
|---|---|---|
| `pub_leased` | `hb_leased` | AUTOMATIC, 1 s |
| `pub_default` | `hb_default` | default (infinite) |

Each has its own subscriber (`sub_leased`, `sub_default`).

## The code

[`publisher.py`](publisher.py) and [`subscriber.py`](subscriber.py) are short
(about 70 and 100 lines) and commented line by line. The key lines:

```python
# publisher.py: promise "you will hear from me within 1 s while I am alive"
policies.append(Policy.Liveliness.Automatic(duration(milliseconds=args.lease)))
```

```python
# subscriber.py: DDS tells the reader when a writer's lease runs out
liveliness = reader.get_liveliness_changed_status()
if liveliness.not_alive_count > 0 and not lost:
    print(f"writer lost liveliness after {time.monotonic() - last_rx:.1f} s", flush=True)
```

The subscriber waits on a `WaitSet` (bounded to 500 ms) and polls its statuses
each turn. It prints `rx seq=<n>`, `matched writers: <n>` and the liveliness
line. No `from __future__ import annotations`: `IdlStruct` reads the real
annotations of the dataclass. When a writer disappears, `take` also returns
no-data notifications, which the loop skips with an `isinstance` check.

## Run it

By hand. Ctrl+C is a clean exit; to see a crash, kill the process instead, for example `taskkill /F /PID <pid>` on Windows or
`kill -9 <pid>` on Linux:

```
python publisher.py --name pub_leased --topic hb_leased --lease 1000
python subscriber.py --name sub_leased --topic hb_leased

python publisher.py --name pub_default --topic hb_default
python subscriber.py --name sub_default --topic hb_default
```

Each program takes `--domain N` (default 0). Or all four at once, with checks
(about 30 s, it waits for the participant lease):

```
python run.py            # run and check
python run.py --hold     # keep the programs running, ask your own MCP client
```

## What you see inside

```
sub_leased:  writer lost liveliness after 1.0 s
sub_default: matched writers: 0
```

- `sub_leased` is told 1.0 s after the last heartbeat: the lease it was
  promised.
- `sub_default` prints no liveliness event (not_alive_count stays 0, checked
  live). Its only sign is
  `matched writers: 0`, once the participant lease expires, about 10 s after
  the crash. Nothing in its output says "the writer crashed".

## What TopicForge shows

```
[3] How long until TopicForge sees it?
    -> list_participants
    both publishers reported as left 12 s after the crash

[4] What does the timeline say?
    -> participant_events
    lost       pub_leased
    lost       pub_default
```

TopicForge sees both crashes, because the participant lease covers every
writer of the process, even one that never set a liveliness lease. It sees
them late: after the 10 s lease (12 s here), not the 1 s the application
chose. It cannot tell the crash from a clean leave.

## What TopicForge cannot see

TopicForge checks Liveliness compatibility: a reader that demands a shorter
lease than the writer offers is reported by `detect_qos_mismatches` (see also
example 12). But it sees the discovery lease, not application liveliness, so it
cannot say "this writer is silent but its process is up". The `writer lost
liveliness` event exists only inside the subscriber.

## Ask your agent

> Two heartbeat publishers, pub_leased and pub_default, are about to be stopped on
> DDS domain 0. Tell me which participants left and when.

(Stop them from another terminal, then ask again.) A good answer says the
timeline is a lease behind, not instantaneous.

For a safety function, do not wait for discovery: give critical writers a
Liveliness lease sized to your reaction time, and react to the reader's
`liveliness changed` status. Without it, a crash is visible only at the
participant lease, about 10 s here (see examples 03 and 12). An outside
observer is a diagnostic tool, not a watchdog.
