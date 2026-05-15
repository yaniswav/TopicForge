"""Real-bus integration scenarios (v0.4.0 Phase 2.2).

Two test entry points live in this package:

* `test_scenarios_schema.py` — pure-Python validation of the scenario
  JSON structure. Runs in the default `make check` pipeline ; no SDK,
  no Docker required.
* `test_real_bus.py` — parametrized over the scenario JSON files,
  marked `@pytest.mark.integration` so it is **skipped** in the
  default pytest run. Only the `pytest -m integration` invocation
  (or `.github/workflows/integration.yml` with the `integration-tests`
  PR label) exercises it.

See `scripts/integration/README.md` for the standalone runner and
the Docker compose option.
"""
