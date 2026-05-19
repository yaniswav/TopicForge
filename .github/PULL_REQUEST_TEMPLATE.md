<!--
Thanks for opening a PR. Three short sections below — keep it tight. The
maintainer's review goes faster when the diff matches a concrete need
described here. See CONTRIBUTING.md for the full contract.
-->

## Summary

<!-- 1-3 sentences. What does this PR change, and why. Link the issue
     it addresses if any (`Closes #N` works). -->

## Test plan

<!-- How you verified the change. At minimum:
     - [ ] `make check` is green (or the underlying ruff + pytest commands on Windows)
     - [ ] Added / updated tests for the new behavior — list them
     - [ ] If the PR touches docs only, say so

     For a non-trivial change, include a paste of the manual scenario you
     ran end-to-end (mock-mode tool calls, expected payloads, etc.). -->

## Backward-compatibility checklist

<!-- Tick what applies. If anything is unchecked, explain in a paragraph why. -->

- [ ] No new MCP tool added — or, if added : see `docs/product-plan.md §11` (the 11-tool cap requires a scope discussion)
- [ ] No change to Pydantic schemas — or, if changed : every new field has a safe default (`extra="forbid"` + additive optional only)
- [ ] No change to the telemetry 6-field contract pinned by `tests/test_telemetry.py::test_payload_contains_only_whitelisted_keys`
- [ ] No new environment variable name — or, if added : documented in README "Configuration reference" and `.env.example`
- [ ] No removal of an existing public API symbol — or, if removed : CHANGELOG `### Removed` line in `[Unreleased]`

## Notes for the maintainer

<!-- Anything that needs context: a tradeoff you weighed, a follow-up you're not
     bundling here, an external service to check (PyPI metadata, README rendering).
     Optional. -->
