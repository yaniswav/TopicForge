# TopicForge integration rig

Real-bus validation of the TopicForge multi-vendor OMG-DDS-RTPS claim.
Two ways to run :

1. **Locally with whatever SDK you have installed** — fastest, partial
   coverage, no Docker required.
2. **Via Docker compose** — full multi-vendor coverage, requires
   Docker + bandwidth to pull the publisher images.

The integration tests are **gated behind `pytest.mark.integration`** so
the default `make check` / `pytest` invocation does NOT run them. Only
`pytest -m integration` (or the `integration-tests` PR label which
triggers `.github/workflows/integration.yml`) exercises them.

---

## Quick start — local

```bash
# Install whichever SDK you have a license for / want to test against.
pip install "topicforge[dds-cyclone]"
# Or:
pip install "topicforge[dds-fast]"

# Run all scenarios — those whose `required_vendors` is not installed
# locally are skipped with a clear log line.
./scripts/integration/run-local.sh
# Windows:
.\scripts\integration\run-local.ps1
```

---

## Quick start — Docker (maintainer's full-coverage path)

```bash
docker compose -f scripts/integration/docker-compose.yml up -d
# Wait ~30 s for discovery to settle
sleep 30
pytest -m integration -v
docker compose -f scripts/integration/docker-compose.yml down
```

---

## Scenarios

Six scenarios ship under `tests/integration/scenarios/` :

| Name                              | Required vendors            | Tests |
| --------------------------------- | --------------------------- | ----- |
| `multi_vendor_basic`              | cyclone, fast, opendds      | `list_participants` returns ≥ 3 |
| `lifecycle_tracking`              | cyclone                     | `participant_events` reports discovered + lost |
| `qos_mismatch_detection`          | cyclone                     | `detect_qos_mismatches` returns Reliability incompatibility |
| `xtypes_decode`                   | cyclone                     | `peek_dds_samples` returns `_decode_status` payloads |
| `topic_metrics_frequency`         | cyclone                     | `topic_metrics(window=60)` returns ≈ 10 Hz |
| `topic_metrics_sequence_gaps`     | cyclone                     | `topic_metrics` reports a sequence gap |

Scenario JSON schema :

```json
{
  "name": "kebab_case_scenario_name",
  "description": "1–2 sentence purpose statement.",
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

## Adding a new scenario

1. Create `tests/integration/scenarios/<name>.json` following the
   schema above.
2. Run `pytest tests/integration/test_scenarios_schema.py` — every
   structural check must pass before the scenario is dispatched.
3. (Optional, for live validation) extend `scenarios_runner.py` if
   your scenario needs a new assertion verb beyond the existing
   `count_at_least`, `samples_observed_at_least`, etc. shapes.

---

## Validation reality (v0.4.0 Phase 2.2)

The OSS-CI default pipeline lint-validates :

- The YAML structure of `docker-compose.yml` (PyYAML parser)
- The bash syntax of `run-local.sh` (`bash -n`)
- The PowerShell syntax of `run-local.ps1` (PowerShell parser
  when available)
- The schema of every `scenarios/*.json` (pure-Python tests)

The OSS-CI default pipeline does NOT :

- Pull or build Docker images
- Spin up containers
- Run the actual publishers against a live bus

The maintainer validates the live path on their workstation before
merging Phase 2.2. The `.github/workflows/integration.yml` workflow
exists for the `integration-tests` PR label path — runs are
intentional and cost-bound to avoid compounding CI minutes.

---

## Versioning

Phase 2.2 ships the structural rig. Real-bus assertion evaluation
(the part of `scenarios_runner.py` that spawns publishers, polls
TopicForge, and reports per-assertion pass/fail) is wired enough to
dispatch — full live validation is the maintainer's follow-up
before the v0.4.0 tag. See `docs/projet-file/mcp-02-spec.md` for
the canonical scope.
