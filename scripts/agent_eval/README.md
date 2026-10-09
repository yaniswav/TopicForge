# Agent evaluation

Measures what matters for an MCP server: can an AI agent, given only
TopicForge's tools and a user's question, find the real cause of a problem on
a live DDS bus? The agent has no access to the code, the scenario or the
expected answer.

Pieces:

- `scenarios.py`: builds a bus per scenario from the example role nodes
  (`examples/dds/nodes/`), starts a broker first so TopicForge watches from
  the beginning, then injects failures on a schedule (crash, restart loop).
  `TRUTH.md` holds the expected diagnosis of every scenario.
- `broker.py`: keeps one TopicForge MCP server alive over stdio and relays
  tool calls from a local TCP port, so lifecycle history survives between
  the agent's calls.
- `tf.py`: what the agent runs. `python tf.py <port> list` shows the tools
  exactly as an MCP client sees them; `python tf.py <port> <tool> '<json>'`
  calls one.

Run with the demo venv (`examples/dds/README.md`, section Setup):

```
python scripts/agent_eval/scenarios.py big_bus        # leave it running
python scripts/agent_eval/tf.py 8786 list             # port = 8700 + domain
```

Then give an agent the scenario's question, the `tf.py` command, and the rule
that it may run nothing else. Score its answer against `TRUTH.md`, and collect
its feedback on the tools: the call sequence, what misled it, how often it had
to parse free text or join GUIDs by hand. Stop a scenario by creating
`stop_<scenario>` in this directory.

`run_external.py` runs one scenario against an external agent CLI (Codex, Gemini)
headless and saves the transcript; results are in `docs/EVAL.md`.

`RESULTS_2026-10-02.md` records the three rounds that shaped 0.5.5, run with LLM
agents on the author's 16 test scenarios (the author wrote them and knows the
answers). On 0.5.4 the agents reached 10 of 11 correct diagnoses, mostly by
parsing raw text by hand, and reported a restart loop as a single restart. On
the 0.5.5 branch they reached 16 of 16, with no false alarm on a healthy bus.
