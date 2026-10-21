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

TopicForge: branch `feat/v0.7.0` (contract 2, 14 tools). Windows 11, one machine.
The runs were made on `1929d8f`. One description edit (`list_participants`, unnamed
participants) landed after it, in `84b11ef`, and the healthy-bus run was repeated after
that edit. Codex: 9 runs (two per scenario plus one extra healthy-bus run after the
edit), 9/9 correct.

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

Addressed in 0.7.1 (changes made after the runs above, not re-measured by a new
evaluation round):

- Listing size: `tools/list` went from about 178 KB to about 113 KB compact
  (`docs/CONTRACT.md` section 9.1).
- Empty `reports` next to `not_matched`: `MismatchScan.summary` states the counts
  (section 9.5).
- `domain_id`: the description opens with "Ignored" and the response `note` flags a
  differing non-zero value (section 9.3).
- `*_ns` fields: `age_s` on participant events (section 9.6).
- `seen_count` and restarts: `restarts_by_name` on `list_participants` and
  `participant_events` (section 9.2).
- Verbose `list_endpoints`: `fields` `compact` (section 9.4).

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

## 0.8.0 campaign (v2)

**Status: run on 2026-10-21** (branch `release/0.8.0`, 14 runs: 7 scenarios x 2 variants, one
Sonnet agent per run, ROS 2 Jazzy in WSL Ubuntu 24.04, Fast DDS for the robot, Cyclone for the
observer). Results below.

### What it is for

The first round (above) was DDS only. The second one runs on a ROS 2 graph and is the exit gate
of 0.8.0. It has to produce:

1. the proof of `graph_diff` (flagship 1): an agent names the three changes between two launch
   versions and reports nothing else;
2. the proof of the declared-against-real checks (flagship 3): an agent names the URDF link
   nothing publishes and the parameters a file states differently;
3. no invented `critical` on a robot that has nothing wrong;
4. the measure that decides 0.9.0, "`diagnose` as a tool or as a prompt": how many tool calls an
   agent needs with a TopicForge prompt alone. The threshold (at most 6 calls with the prompt
   alone) is a proposal, to be confirmed before the first run.

### Method

Same blindness as the first round: the agent gets a user question and `tf.py`, nothing else (no
repository, no `TRUTH_ROS2.md`, no scenario code). Changes from the first round:

- **Catalogue.** The agent starts with `python tf.py <port> list --brief`: one line per tool
  (name, inputs, first sentence), 2,309 bytes on the mock server against 35,514 bytes for the
  full `list`, which the first round's agents found too long. `list --tool NAME` prints one
  tool in full.
- **Calls are counted by the broker**, not reported by the agent (`calls.jsonl`, summarized in
  `summary.json`): `tool_calls` (every request that is not a catalogue listing), `list_calls`,
  `errors`, the sequence of tools and the bytes returned.
- **Scenarios** run on the bench robot (`publisher.py --fault NAME`), as a clean robot with one
  fault, never on the CI bench itself (which has seeded faults of its own and cannot stand for a
  healthy robot). The faults live in the bench publisher; TopicForge stays read-only.
- **Two variants** of every run. `tools`: the agent knows only the tools. `prompt`: the same
  agent, question and tools, plus the text of an MCP prompt that the broker fetches from the
  server (`inspect-ros2-robot`, or `diagnose-nav2` with `--prompt diagnose-nav2`). The number of
  tool calls of the two variants is the measure for item 4.

### Scenarios

| Scenario | Fault | Decisive tool | Evidence it supports |
|---|---|---|---|
| `healthy` | none | any | no invented `critical` |
| `blocked_executor` | `/bench_blocked` is on the graph and its executor never runs again | `get_node_info` | |
| `tf_stale` | `map -> odom` stops after 3 s (the capture-window mechanism of the CI bench) | `inspect_tf` | |
| `qos_mismatch` | `/scan_edge` writer best_effort, `/edge_viewer` reader reliable | `detect_qos_mismatches`, `get_topic_info` | |
| `restart_loop` | `/path_planner` dies every 6 s with an ERROR and is started again | `participant_events`, `sample_messages` on `/rosout` | |
| `declared_drift` | URDF link `arm_link` never published; a parameter file with `max_range` and `extra_gain` that differ | `inspect_tf`, `get_node_info` with `params_file` | flagship 3 |
| `graph_v1_v2` | after a baseline snapshot, node `/bench_v2`, topic `/scan_v2` and a second `/cmd_vel_out` writer appear | `graph_diff` | flagship 1 |

What a correct answer names, and what counts as a false alarm, is in
`scripts/agent_eval/TRUTH_ROS2.md`, in the style of `TRUTH.md`. The user question of each
scenario is in `scripts/agent_eval/ros2_scenarios.py` (`QUESTIONS`) and is printed by the runner.

The `diagnose-nav2` prompt is optional in this campaign (`tf_stale` and `restart_loop` are the
two scenarios whose steps it contains). The bench has no Nav2 stack, so that prompt is measured
on its primitives (`/bond`, the latched `_action/status` topic, `inspect_tf`, `/rosout`), not on
Nav2 itself, and its call counts say nothing about a real stack.

### Running one scenario

The runner needs a Linux or WSL host (or the bench image) with ROS 2 sourced. It starts the
broker first, then the robot and the fault, waits for the fault to be in place, writes the
agent's workspace and prompt, and prints `READY` with the question. On a host:

```
source /opt/ros/humble/setup.bash
python3 -m venv ~/tfenv && ~/tfenv/bin/pip install -e ".[bags]"
~/tfenv/bin/python scripts/agent_eval/ros2_scenarios.py tf_stale --out eval_runs
```

In the bench image (the broker listens on `0.0.0.0` inside the container, the port is published
on the host's loopback only, and `eval_runs` on the host receives the workspace):

```
docker build -f tests/integration/ros2/Dockerfile --build-arg DISTRO=humble -t topicforge-ros2:humble .
docker run --rm -p 127.0.0.1:8801:8801 -v "$PWD/eval_runs:/bench/eval" topicforge-ros2:humble \
    bash -c "source /opt/ros/humble/setup.bash && /opt/tfenv/bin/python scripts/agent_eval/ros2_scenarios.py tf_stale --host 0.0.0.0 --out /bench/eval"
```

Options: `--variant prompt` (with `--prompt diagnose-nav2` for the other prompt), `--port`,
`--domain`, `--dds-backend`, `--max-minutes`; `--print-only` prints the plan and the question
without starting anything. The scenario names are `healthy`, `blocked_executor`, `tf_stale`,
`qos_mismatch`, `restart_loop`, `declared_drift` and `graph_v1_v2`.

For each run the orchestrator:

1. starts the runner and waits for the `READY` line;
2. gives an agent `eval_runs/<scenario>-<variant>/prompt.txt` as its instructions and
   `eval_runs/<scenario>-<variant>/work/` as its working folder (it holds `tf.py` and a README
   only), and tells it that it may run nothing but `tf.py`;
3. when the agent has answered, creates `eval_runs/<scenario>-<variant>/stop`; the runner stops
   every process and writes `summary.json` next to the transcript;
4. grades the answer against `TRUTH_ROS2.md` and records the row below.

`restart_loop` is graded against `restarts_at_question` in `summary.json` (plus or minus one).

### What is recorded per run

| Field | Source |
|---|---|
| grade (correct, partially correct, wrong, false alarm) | the grader, against `TRUTH_ROS2.md` |
| `tool_calls`, `list_calls`, `errors`, call sequence, bytes returned | `summary.json` (broker log) |
| agent time and tokens | the agent runtime |
| the agent's feedback on the tools | the closing section the prompt asks for |

### Results

Calls are `tool_calls` from the broker (catalogue listings not counted). Graded against
`TRUTH_ROS2.md`.

| Scenario | `tools`: grade, calls | `prompt` (`inspect-ros2-robot`): grade, calls |
|---|---|---|
| `healthy` | correct, no false alarm, 19 | correct, no false alarm, 18 |
| `blocked_executor` | correct, 14 | correct, 18 |
| `tf_stale` | correct (`map -> odom`, `/bench_robot`), 10 | correct, 13 |
| `qos_mismatch` | correct, 2 | correct, 3 |
| `restart_loop` | partially correct: loop and count (25 at its call), no cause, 15 | correct: loop, count (11 at its call), cause "costmap not received" from `/rosout`, 12 |
| `declared_drift` | correct, 2 | correct, 7 |
| `graph_v1_v2` | correct, 1 (`graph_diff` alone) | correct, 2 |

| Criterion | Result |
|---|---|
| flagship 1: three changes named, no false alarm (`graph_v1_v2`) | met in both variants, one `graph_diff` call |
| flagship 3: `arm_link`, `max_range`, `extra_gain` named, no false alarm (`declared_drift`) | met in both variants |
| no invented `critical` on `healthy` | met in both variants (observations only: `/scan_edge` degenerate test scan, no subscribers, security off) |
| calls with the prompt alone, over the scenarios (threshold proposed: at most 6) | not met: median 12, 2 of 7 runs at 6 or fewer; the prompt did not lower the call count (sum 73 against 63) |

What the runs taught:

- The prompt helped on content, not on effort: it is what sent the agent to `/rosout` with
  `min_log_level` and `node`, which found the cause in `restart_loop`. On open questions
  (`healthy`, `blocked_executor`) both variants sampled every topic (9 `sample_messages`).
- `inspect_tf` with its defaults (5 s window, `stale_after_s` 2) reported `tf_stale` as healthy
  on the first call in both variants; the agents found the stale edge only with a longer window.
- Repeated feedback across runs: `restarts_by_name` keyed on `/` lumps every node together
  (`restarts_by_node` is the useful field and sits last); participant `name` is `/` for every
  Fast DDS participant; `min_log_level` rejects upper case (`WARN`) and the prompt does not say
  so; `detect_qos_mismatches` is noisy on a healthy bus (`ros_discovery_info` pairs, one "no
  reader" hint per topic) and its reports carry GUID prefixes, not node names; the `/rosout`
  note mixes "0 of 0 log messages" with the latched-topic hint; `param_diff_summary` counts
  `same` and `live_only` entries it does not name.
- Harness: `restarts_at_question` is counted at `READY`, not at the agent's first call, so it
  under-counts when the agent starts late (5 at `READY`, 25 at the agent's call in the `tools`
  run). The `restart_loop` grades above use the count at the agent's call, from the cycle.

### Limits

- The scenarios and their answers were written by the author, on a simulated robot that is
  one node plus a few small ones. The bench is not a real robot and has no Nav2.
- `tf_stale` stops `map -> odom` after each new `/tf` subscriber, because TopicForge sees `/tf`
  only through a capture window: an edge that stopped before the call would not be seen at all
  and would read as a missing frame.
- `graph_v1_v2` depends on a baseline snapshot taken by the runner before the change; the
  agent's own first `graph_diff` call is compared with it.
- Claude agents only, a few runs per cell: a count, not a rate. The blindness rests on the
  working folder and the prompt rules, not on an enforced boundary.
- The first round's questions and these ones are different, so the two are comparable in kind
  only.
