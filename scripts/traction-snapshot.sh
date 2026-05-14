#!/usr/bin/env bash
# Collect TopicForge traction signals (PyPI installs + GitHub repo metrics)
# and write a daily snapshot under docs/projet-file/traction/<date>.json.
#
# Designed to run weekly (typically Monday 09:00). The script is idempotent
# for a given UTC day: re-running overwrites that day's snapshot.
#
# Sources are queried via the public APIs of pypistats.org and api.github.com
# so neither `gh` nor `pypistats` need to be installed. Both are unauthenticated
# read-only endpoints — rate limits are generous for a weekly call.
#
# See docs/projet-file/traction/README.md for setup (cron / Windows Task
# Scheduler) and for the decision-gate thresholds the snapshot informs.

set -u
# Deliberately NOT set -e: a partial source failure must still emit a snapshot
# with explicit nulls for the missing fields. The point of this monitoring is
# that bad numbers surface — including the absence of numbers.

PKG="topicforge"
GH_OWNER_REPO="yaniswav/TopicForge"
PRO_SLOTS=0  # hardcoded until a real mailing-list / Stripe lookup exists

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="$REPO_ROOT/docs/projet-file/traction"
mkdir -p "$OUT_DIR"

DATE_UTC="$(date -u +%Y-%m-%d)"
OUT_JSON="$OUT_DIR/$DATE_UTC.json"
OUT_SUMMARY="$OUT_DIR/latest-summary.md"

# ---- PyPI recent downloads ----------------------------------------------
pypi_last_day="null"
pypi_last_week="null"
pypi_last_month="null"
pypi_status="ok"

if pypi_json="$(curl -fsSL --max-time 10 "https://pypistats.org/api/packages/$PKG/recent" 2>/dev/null)" \
   && echo "$pypi_json" | jq -e '.data' >/dev/null 2>&1; then
  pypi_last_day="$(echo "$pypi_json"  | jq -r '.data.last_day')"
  pypi_last_week="$(echo "$pypi_json" | jq -r '.data.last_week')"
  pypi_last_month="$(echo "$pypi_json"| jq -r '.data.last_month')"
else
  pypi_status="unavailable"
fi

# ---- GitHub repo metrics ------------------------------------------------
stars="null"
issues="null"
forks="null"
gh_status="ok"

if gh_json="$(curl -fsSL --max-time 10 \
                -H "Accept: application/vnd.github+json" \
                "https://api.github.com/repos/$GH_OWNER_REPO" 2>/dev/null)" \
   && echo "$gh_json" | jq -e '.stargazers_count' >/dev/null 2>&1; then
  stars="$(echo  "$gh_json" | jq -r '.stargazers_count')"
  issues="$(echo "$gh_json" | jq -r '.open_issues_count')"
  forks="$(echo  "$gh_json" | jq -r '.forks_count')"
else
  gh_status="unavailable"
fi

# ---- Decision-gate evaluation -------------------------------------------
# G1 / G2 / G3 are defined in docs/product-plan.md §12. We only auto-evaluate
# what is measurable from this snapshot. G1 (10 Pro slots) and G3 (qualitative
# logos + inbound) stay manual — the snapshot exposes the inputs anyway.
g2_week_threshold_met="$(jq -n --argjson v "$pypi_last_week" \
  'if ($v != null) and ($v >= 100) then true else false end')"
g1_pro_slots_met="$(jq -n --argjson v "$PRO_SLOTS" \
  'if $v >= 10 then true else false end')"

# ---- Compose JSON snapshot ----------------------------------------------
jq -n \
  --arg     date              "$DATE_UTC" \
  --arg     pypi_status       "$pypi_status" \
  --argjson pypi_last_day     "$pypi_last_day" \
  --argjson pypi_last_week    "$pypi_last_week" \
  --argjson pypi_last_month   "$pypi_last_month" \
  --arg     gh_status         "$gh_status" \
  --argjson stars             "$stars" \
  --argjson issues            "$issues" \
  --argjson forks             "$forks" \
  --argjson pro_slots         "$PRO_SLOTS" \
  --argjson g1_pro            "$g1_pro_slots_met" \
  --argjson g2_week           "$g2_week_threshold_met" \
  '{
     date: $date,
     pypi: {
       status: $pypi_status,
       last_day: $pypi_last_day,
       last_week: $pypi_last_week,
       last_month: $pypi_last_month
     },
     github: {
       status: $gh_status,
       stars: $stars,
       open_issues: $issues,
       forks: $forks
     },
     pro: {
       slots_reserved: $pro_slots,
       slots_target: 10
     },
     gates: {
       G1_pro_slots_met: $g1_pro,
       G2_week_threshold_met: $g2_week,
       G3_dds_activation: "manual"
     }
   }' > "$OUT_JSON"

# ---- Human-readable summary ---------------------------------------------
fmt() { if [ "$1" = "null" ]; then echo "—"; else echo "$1"; fi; }

verdict_g2() {
  if [ "$g2_week_threshold_met" = "true" ]; then
    echo "**MET** — TopicForge DDS module kickoff unblocked on the install-count side"
  else
    echo "below 100/week — DDS module kickoff held by reach, not by surface area (per §12 G2)"
  fi
}

verdict_g1() {
  if [ "$g1_pro_slots_met" = "true" ]; then
    echo "**MET** — 10 Pro early-access slots reserved; Phase 2 Pro feature work can start"
  else
    echo "$PRO_SLOTS / 10 Pro slots reserved; no Pro feature ships before threshold (per docs/pro.md)"
  fi
}

cat > "$OUT_SUMMARY" <<EOF
# Traction snapshot — $DATE_UTC

| Signal                       | Value                       | Gate context                                  |
| ---------------------------- | --------------------------- | --------------------------------------------- |
| PyPI installs / day          | $(fmt "$pypi_last_day")     | rolling                                       |
| PyPI installs / week         | $(fmt "$pypi_last_week")    | **G2 needs ≥ 100/week sustained over a month** |
| PyPI installs / month        | $(fmt "$pypi_last_month")   | rolling baseline                              |
| GitHub stars                 | $(fmt "$stars")             | weak proxy; G3 needs *named OSS logos*, not stars |
| GitHub open issues           | $(fmt "$issues")            | hygiene signal                                |
| GitHub forks                 | $(fmt "$forks")             | weak proxy                                    |
| Pro early-access slots       | $PRO_SLOTS / 10             | **G1 needs 10**                               |
| pypistats.org reachable      | $pypi_status                | data quality                                  |
| api.github.com reachable     | $gh_status                  | data quality                                  |

## Gate verdicts (auto-computed where possible)

- **G1 — Phase 1 → Phase 2.** $(verdict_g1).
- **G2 — TopicForge DDS module kickoff.** $(verdict_g2).
- **G3 — DDS horizon activation.** Manual. Tracked off-snapshot — needs 3+ named OSS logos AND a credible enterprise inbound in the same quarter.

## Next action

- If G2 is **MET** and the TopicForge DDS module has not yet started: open a fresh session and kick off the DDS module implementation inside TopicForge from \`docs/projet-file/mcp-02-spec.md\` (TopicForge v0.2.0 \`MiddlewareAdapter\` protocol prep, then v0.3.0 \`CycloneDdsAdapter\` + 3 new tools).
- If G2 is **below** for two consecutive snapshots: the issue is reach, not surface area. Audit hook drift in README per §11 *Positioning collapse* risk, not pile on features.
- If G1 is **MET** but Phase 1 items are not all shipped: revisit the Phase 1 remaining list in \`docs/product-plan.md §5\` and re-sequence.

Latest snapshot file: \`$DATE_UTC.json\`. Full archive: \`docs/projet-file/traction/\`.
EOF

echo "Snapshot written: $OUT_JSON"
echo "Summary  written: $OUT_SUMMARY"
