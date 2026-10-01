# TopicForge integration rig

Intended purpose: real-bus validation of the TopicForge multi-vendor
OMG-DDS-RTPS claim.

**Current state, 0.5.3: the rig validates nothing.** The scenario files,
the schema tests and the dispatch shell exist. The part that would
spawn publishers, start TopicForge, call its tools and compare the
results does not. No DDS adapter in this repository has been exercised
against a live bus. Do not read a green CI run as evidence that the DDS
module works on a real domain.

What exists, precisely:

- `scenarios_runner.py` loads a scenario JSON, probes which vendor
  bindings are importable, and then, for every assertion, records
  "not evaluated" as a **failure**. It spawns no publisher, launches no
  TopicForge process, and calls no tool. It exits with code 1 whenever a
  scenario has at least one assertion, which is every shipped scenario.
- `tests/integration/test_real_bus.py` shells out to that runner. Its
  dispatch test is marked `xfail(strict=True)`: it is expected to fail
  today, and it will report an unexpected pass the day the runner
  validates something, which is the signal to remove the marker.
- `pyproject.toml` deselects the `integration` marker by default
  (`-m "not integration"` in `addopts`), so a plain `pytest` never runs
  these tests, whether or not a DDS binding is installed.
- `tests/integration/test_scenarios_schema.py` validates the structure
  of every scenario file. That is pure Python and runs in the default
  suite.
- `docker-compose.yml`, the Dockerfiles and `publishers/` are an
  unvalidated sketch of a multi-vendor bus.

---

## Running it anyway

```bash
pip install "topicforge[dds-cyclone]"

# Dispatch every scenario whose required vendors are importable
# (the others are reported as skipped). Expect exit code 1.
./scripts/integration/run-local.sh
# Windows:
.\scripts\integration\run-local.ps1

# Through pytest (deselected by default; the dispatch test is an xfail)
pytest -m integration -v
```

Only the Cyclone binding can be installed from PyPI. The Fast DDS
binding is not published there and has to be built from eProsima's
sources, so scenarios that require `fast` can only be dispatched on a
machine where you did that. TopicForge has no working OpenDDS or Dust
DDS adapter to test (both are permanent stubs; `pyopendds` exists on
PyPI, but the OpenDDS adapter never uses it), so a scenario that requires
`opendds` can only be dispatched, never satisfied by TopicForge itself.

The Docker route (`docker compose -f scripts/integration/docker-compose.yml
up -d`) is a sketch of the intended full-coverage setup. It has not been
validated, and the publisher images for Fast DDS and OpenDDS depend on
bindings that `pip` cannot fetch.

The `integration-tests` PR label triggers
`.github/workflows/integration.yml`, which runs the schema tests and
then `pytest -m integration`. With the runner as it is, that exercises
the xfail and nothing else.

---

## Scenarios

Six scenarios live under `tests/integration/scenarios/`. They describe
what a validation should check, and two groups of them no longer match
the behaviour of the code:

| Name                              | Required vendors            | Intended check | Status |
| --------------------------------- | --------------------------- | -------------- | ------ |
| `multi_vendor_basic`              | cyclone, fast, opendds      | `list_participants` returns >= 3 | Not affected by 0.5.3; needs a bus that includes an OpenDDS publisher |
| `lifecycle_tracking`              | cyclone                     | `participant_events` reports discovered + lost | Not affected by 0.5.3 |
| `qos_mismatch_detection`          | cyclone                     | `detect_qos_mismatches` returns a Reliability incompatibility | Not affected by 0.5.3 |
| `xtypes_decode`                   | cyclone                     | `peek_dds_samples` returns `_decode_status` payloads | **Outdated.** User-topic decoding is disabled since 0.5.3; the live result is a `"raw"` placeholder, which the scenario's `"raw"` allowance happens to accept, but it no longer tests decoding |
| `topic_metrics_frequency`         | cyclone                     | `topic_metrics(window=60)` returns ~10 Hz | **Outdated.** `topic_metrics` has no data for user topics since 0.5.3 and its frequency reflects peek cadence |
| `topic_metrics_sequence_gaps`     | cyclone                     | `topic_metrics` reports a sequence gap | **Outdated.** Same reason; builtin topics carry no sequence number |

Scenario JSON schema :

```json
{
  "name": "kebab_case_scenario_name",
  "description": "1-2 sentence purpose statement.",
  "required_vendors": ["cyclone", "fast", ...],
  "setup": {
    "domain_id": 0,
    "publishers": [{ "vendor": "...", "topic": "/...", "rate_hz": N, "duration_s": N }],
    "subscribers": [...],
    "discovery_wait_s": 5
  },
  "assertions": [
    { "tool": "list_participants", "args": {"domain_id": 0}, "expect": {...} }
  ]
}
```

See `tests/integration/test_scenarios_schema.py` for the pure-Python
schema validation that runs in default CI.

---

## What it would take to make this real

1. Implement the runner: spawn each publisher, start the TopicForge
   server with the right `TOPICFORGE_DDS_BACKEND`, call the tools over
   MCP stdio, evaluate each `expect` block, and exit 0 only when every
   assertion actually passed.
2. Run it against at least a Cyclone publisher and an independent
   vendor's publisher on one domain, and keep the output.
3. Rewrite the three outdated scenarios around what the tools do today
   (builtin-topic metrics, presence-only user topics).
4. Remove the `xfail(strict=True)` marker.

Until then, treat every claim about live-bus behaviour in this
repository's documentation as design intent. See
[`docs/DDS_QUICKSTART.md`](../../docs/DDS_QUICKSTART.md) and
`docs/projet-file/mcp-02-spec.md` for the intended scope.
