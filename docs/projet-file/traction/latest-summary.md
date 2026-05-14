# Traction snapshot — 2026-05-14

| Signal                       | Value                       | Gate context                                  |
| ---------------------------- | --------------------------- | --------------------------------------------- |
| PyPI installs / day          | 90     | rolling                                       |
| PyPI installs / week         | 90    | **G2 needs ≥ 100/week sustained over a month** |
| PyPI installs / month        | 90   | rolling baseline                              |
| GitHub stars                 | 2             | weak proxy; G3 needs *named OSS logos*, not stars |
| GitHub open issues           | 0            | hygiene signal                                |
| GitHub forks                 | 0             | weak proxy                                    |
| Pro early-access slots       | 0 / 10             | **G1 needs 10**                               |
| pypistats.org reachable      | ok                | data quality                                  |
| api.github.com reachable     | ok                  | data quality                                  |

## Gate verdicts (auto-computed where possible)

- **G1 — Phase 1 → Phase 2.** 0 / 10 Pro slots reserved; no Pro feature ships before threshold (per docs/pro.md).
- **G2 — TopicForge DDS module kickoff.** below 100/week — DDS module kickoff held by reach, not by surface area (per §12 G2).
- **G3 — DDS horizon activation.** Manual. Tracked off-snapshot — needs 3+ named OSS logos AND a credible enterprise inbound in the same quarter.

## Next action

- If G2 is **MET** and the TopicForge DDS module has not yet started: open a fresh session and kick off the DDS module implementation inside TopicForge from `docs/projet-file/mcp-02-spec.md` (TopicForge v0.2.0 `MiddlewareAdapter` protocol prep, then v0.3.0 `CycloneDdsAdapter` + 3 new tools).
- If G2 is **below** for two consecutive snapshots: the issue is reach, not surface area. Audit hook drift in README per §11 *Positioning collapse* risk, not pile on features.
- If G1 is **MET** but Phase 1 items are not all shipped: revisit the Phase 1 remaining list in `docs/product-plan.md §5` and re-sequence.

Latest snapshot file: `2026-05-14.json`. Full archive: `docs/projet-file/traction/`.
