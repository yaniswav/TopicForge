# Blind agent evaluation

This page records how well an AI agent can diagnose a DDS bus when it has only
TopicForge's tools and a user question, and how that compares across models.
It is an internal quality check on the author's own scenarios, not an
independent benchmark.

## Method

- **Blind.** The agent gets the user question and one instruction: use
  TopicForge through `tf.py`, a command line client that relays calls to one
  persistent TopicForge server (`scripts/agent_eval/broker.py`). It has no
  access to the repository, `TRUTH.md` or the scenario code. The working folder
  holds only `tf.py` and a short `README.md` on its usage.
- **Scenarios** (`scripts/agent_eval/scenarios.py`): live Cyclone DDS and Dust
  DDS nodes on a local domain, with failures injected on a schedule. The server
  is started first so it sees the whole history. The expected diagnosis of each
  scenario is in `scripts/agent_eval/TRUTH.md`.

  | Scenario | What is wrong |
  |---|---|
  | `crash_live` | `safety_monitor` is killed 25 s in; `estop` loses its only writer, `cmd_vel` loses a reader |
  | `restart_loop` | `nav_planner` is restarted three times (four lives) |
  | `partition_split` | `camera_driver` writes in partition `front`, `front_viewer` reads in `rear`; the Reliability difference is secondary |
  | `healthy_bus` | nothing; mixed Cyclone and Dust, consistent partitions, compatible deadlines |

- **Grading** against `TRUTH.md`: *correct* (the real cause and the affected
  components identified, nothing invented), *partially correct* (right
  direction but a wrong or missing element), *wrong*, or *false alarm* (a
  problem reported on the healthy bus). Remarks `TRUTH.md` lists as acceptable
  (for example an unnamed Dust participant) do not count as a false alarm.
- **Runner.** `scripts/agent_eval/run_external.py` starts a scenario and its
  broker, waits until the failures have happened, runs the agent CLI headless in
  the scratch folder with a private home (credentials copied, the user's own MCP
  servers, plugins and instruction files not loaded), saves the transcript and
  final answer, and stops everything. The agent is also asked for feedback on
  the tools, which is where the friction notes below come from.
- The user questions for these external runs were written for this round and
  are in `run_external.py`. The questions used in the earlier Claude runs were
  not archived, so the two sets are comparable in kind, not word for word.

## Results

TopicForge: branch `feat/v0.7.0` at `1929d8f` (contract 2, 14 tools). Windows 11,
one machine. Two runs per scenario for Codex, plus one extra healthy-bus run
after a description edit.

| Agent | crash_live | restart_loop | partition_split | healthy_bus |
|---|---|---|---|---|
| Claude Sonnet (earlier round, one run each; exact version not recorded) | correct | correct | correct | correct, no false alarm |
| OpenAI Codex CLI 0.157.1, the account's default model (name not reported by `codex exec --json`) | correct, correct | correct, correct | correct, correct (note 1) | correct, correct (note 2), correct |
| Google Gemini CLI 0.56.0 | not run | not run | not run | not run |

**Gemini CLI could not run.** Every call fails at authentication with
`IneligibleTierError: This client is no longer supported for Gemini Code Assist
for individuals`, so no model was ever reached. This is an account and product
limit, not a TopicForge result. The Gemini column stays empty until the CLI is
used with an API key (`GEMINI_API_KEY`) or the owner migrates to Google's
Antigravity tooling.

Codex effort per run (TopicForge calls through `tf.py`, one of which is the tool
listing; model tokens as reported by the CLI; agent time excludes the 5 to 40 s
wait for the failures):

| Scenario | Calls | Input tokens (cached) | Output tokens | Agent time |
|---|---|---|---|---|
| crash_live | 7, 6 | 219k (186k), 181k (146k) | 3.9k, 2.5k | 53 s, 39 s |
| restart_loop | 5, 7 | 121k (86k), 151k (100k) | 3.0k, 2.6k | 41 s, 38 s |
| partition_split | 4, 5 | 92k (52k), 120k (95k) | 2.4k, 1.6k | 36 s, 28 s |
| healthy_bus | 7, 8, 7 | 135k, 224k, 222k | 2.9k, 3.5k, 3.4k | 44 s, 46 s, 52 s |

Every run started with the tool listing, about 40,000 characters of JSON, and
ended with a written diagnosis plus confidence. All commands were audited from
the transcripts: only `tf.py` calls and a read of `README.md`.

Notes on grading:

1. `partition_split`, run 1 presented the Reliability difference as a second
   reason the viewer cannot receive, next to the partition. The tool reports it
   as latent and the partition as the failed match, and run 1 named the
   partition correctly, so it is graded correct with an imprecise emphasis. Run
   2 stated the order exactly: the partition blocks now, Reliability would block
   after the fix.
2. `healthy_bus`, run 2 concluded "healthy, with one participant to identify"
   (the unnamed Dust participant, an acceptable remark) and hedged that it could
   be a configuration issue. No real fault was invented, so it is not counted as
   a false alarm, but it is a soft one and the most noticeable weakness seen.

**Zero-false-alarm check:** 3 of 3 Codex healthy-bus runs reported no fault
(2 with an explicit "identify the unnamed participant" caveat), and the earlier
Claude run reported none.

## What the agents found hard

- **Tool listing size (all Codex runs).** `list` returns about 40,000
  characters, and Codex's shell truncated it. The agents still found the right
  tools, but they said the long descriptions made the listing hard to scan.
- **Empty `reports` next to a non-empty `not_matched`** (partition_split, both
  runs): the agents saw that the cause was under `not_matched` and called the
  empty `reports` easy to misread as an all-clear. The description already says
  so and both runs read it correctly.
- **`domain_id` parameters** that default to 0 but do not select a domain (3
  runs): understood, called misleading as a default.
- **`*_ns` fields:** nanosecond values hard to read; the clock caveat on
  `announced_ns` and the upper-bound meaning of `lost_ns` were understood and
  restated in the answers.
- **`seen_count` 1 on each of three same-named participants** (restart_loop,
  run 2): grouped by name and counted distinct GUIDs to get three departures.
- **Verbose `list_endpoints`** (about 18,000 characters for seven topics).
- **Unnamed or unknown-vendor participant** read as a possible problem in 2 of
  3 healthy-bus runs.

## Description edit made

One, description only, no contract change: `list_participants` now states that
a participant with no `name` (Dust DDS announces none) is normal and should be
identified by its topics. Snapshots and `docs/TOOLS.md` were regenerated, the
suite is green (1428 passed). The healthy-bus rerun after the edit still asked
the engineer to confirm the participant, but called it an identity gap and "not
evidence of a failing heartbeat". One run is not enough to call the edit
effective. The other friction above is already covered by the descriptions or is
a size question that a wording change would not fix.

## Limits

- Four scenarios, DDS only, on synthetic buses the author built and whose
  answers the author knows. No ROS 2 CLI scenario and no bag scenario.
- One machine (Windows 11), one run per Claude scenario, two per Codex scenario.
- Codex ran with its sandbox off (its Windows sandbox rejected every command in
  a private home), so blindness rests on the empty working folder, the prompt
  rules and the transcript audit, not on an enforced boundary.
- Model names are what the CLIs report; Codex reported none, and the Claude
  version of the earlier round was not recorded. No Gemini result exists.
- The questions differ between the Claude and Codex rounds.
- Raw transcripts are kept locally and are not published.

## Reproduce

```
python scripts/agent_eval/run_external.py --cli codex --scenario healthy_bus \
    --out <runs dir> --bus-python <venv python with topicforge[dds] and dust-dds==0.16.0>
```
