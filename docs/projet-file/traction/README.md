# Traction snapshots

Weekly snapshots of the signals that inform TopicForge's decision gates
(`docs/product-plan.md §12`). Snapshots are JSON; a human-readable summary
of the latest run is regenerated in `latest-summary.md` on every snapshot.

This folder **is** versioned (allowlisted in `.gitignore`). Everything
else under `docs/projet-file/` stays local, but the traction archive
benefits from being in git: it gives you a credible historical curve to
look at when a gate decision is on the table.

---

## Files in this folder

- `YYYY-MM-DD.json` — one snapshot per day the script runs. Re-running on
  the same UTC date overwrites that day's file (idempotent).
- `latest-summary.md` — markdown summary of the most recent snapshot,
  regenerated every run. Read this first; drill into the JSON when you
  need history.
- `README.md` — this file.

---

## How to read a snapshot

```json
{
  "date": "2026-05-14",
  "pypi":   { "status": "ok", "last_day": 90,  "last_week": 90,  "last_month": 90 },
  "github": { "status": "ok", "stars": 2, "open_issues": 0, "forks": 0 },
  "pro":    { "slots_reserved": 0, "slots_target": 10 },
  "gates":  { "G1_pro_slots_met": false, "G2_week_threshold_met": false, "G3_dds_activation": "manual" }
}
```

- `pypi.status` / `github.status` are `"ok"` when the API responded with a
  parseable payload; `"unavailable"` if the call failed (network, rate
  limit, transient 5xx). When a status is `unavailable` the corresponding
  numeric fields are `null`, not zero — **never read `null` as "zero
  installs"**.
- `pypi.last_week` is the rolling 7-day installs from `pypistats.org` —
  what G2 thresholds on.
- `pro.slots_reserved` is hardcoded to `0` until a real lookup exists
  (mailing list / Stripe metadata). Update the constant in
  `scripts/traction-snapshot.sh` when this stops being a stub.
- `gates.G3_dds_activation` is permanently `"manual"` — it depends on
  qualitative signals (named OSS logos, enterprise inbound) that no
  weekly script can evaluate honestly. The snapshot exposes the inputs;
  the verdict lives in your head.

---

## Decision-gate thresholds (mirror of `product-plan.md §12`)

| Gate | Trigger                                                                                                                                              | Auto-evaluated by snapshot? |
| ---- | ---------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------- |
| G1   | All Phase 1 items shipped **and** 10 Pro early-access slots reserved                                                                                 | Pro slots: yes. Phase 1: no |
| G2   | PyPI weekly install count ≥ 100 sustained over a month **and** MCP 02 spec ratified                                                                  | Week threshold: yes. Sustained: needs 4 consecutive weekly snapshots ≥ 100. Spec ratification: manual |
| G3   | ≥ 3 named OSS logos (teams using TopicForge / a pack MCP and willing to be cited) **and** a credible enterprise inbound — same quarter               | No, qualitative only         |

The snapshot prints `G2_week_threshold_met: true` the moment a single
week hits 100. **That is not the gate**: the gate requires four
consecutive snapshots ≥ 100. Check the last four JSON files manually
before declaring G2 cleared. The single-week flag is a "watch from here"
trigger, not a green light.

---

## When to act on a snapshot

- **G2 weekly threshold cleared (single snapshot)** → start watching the
  next three weeks. Don't kick off the TopicForge DDS module yet.
- **G2 weekly threshold cleared for 4 consecutive weeks** → kick off the
  TopicForge DDS module implementation from `docs/projet-file/mcp-02-spec.md`
  (v0.2.0 = `MiddlewareAdapter` protocol prep, v0.3.0 = `CycloneDdsAdapter`
  + the 3 new tools).
- **G2 below 100 for 8+ weeks** → the issue is reach, not surface area.
  Re-read `product-plan.md §11 Positioning collapse` risk. Audit hook
  drift in the README before adding any feature.
- **G1 Pro slots ≥ 10** → start Phase 2 Pro feature work (URDF
  Inspector, Bag Anomaly Detector, Multi-bag Diff) per `docs/pro.md`.
- **Snapshot `pypi.status = unavailable` for 2+ consecutive runs** →
  pypistats.org or our outbound network is broken; investigate before
  trusting the next snapshot.

---

## Archiving an exceptional snapshot

Routine snapshots stay in this folder under their date. For a snapshot
you want to call out (a milestone, a launch-week spike, a regression),
copy it next to a memo:

```bash
DATE=2026-08-04
cp docs/projet-file/traction/$DATE.json \
   docs/projet-file/traction/milestone-$DATE-first-100wk.json
```

Add a sibling `milestone-<date>-<slug>.md` explaining what was
happening that week. Both files are tracked by the allowlist.

---

## Setup — make the script run weekly

The script itself is hand-runnable any time:

```bash
bash scripts/traction-snapshot.sh
```

To automate it on Monday 09:00 local time:

### Linux / macOS — cron

```bash
crontab -e
# Then add:
0 9 * * 1 cd /path/to/TopicForge && bash scripts/traction-snapshot.sh >> /tmp/topicforge-traction.log 2>&1
```

### Windows — Task Scheduler

Open *Task Scheduler* → *Create Basic Task*:

- **Name**: `TopicForge — weekly traction snapshot`
- **Trigger**: Weekly, every Monday at 09:00
- **Action**: Start a program
  - Program/script: `C:\Program Files\Git\bin\bash.exe`
  - Add arguments: `-lc "cd /c/Users/Yanis/Documents/TopicForge && bash scripts/traction-snapshot.sh"`
  - Start in: `C:\Users\Yanis\Documents\TopicForge`

Verify the first run by checking that a fresh `YYYY-MM-DD.json` appears
in this folder the next Monday. If it doesn't, run the script manually
and inspect Task Scheduler's *Last Run Result* for the failing task.

The script intentionally does not push to git. Commit the new snapshot
when you read the summary:

```bash
git add docs/projet-file/traction/
git commit -m "chore(traction): weekly snapshot $(date -u +%Y-%m-%d)"
```

### Notify channel

There is no Slack MCP available in this workspace, so the script does
not push notifications. Reading `latest-summary.md` after a run is the
notification. If a Slack / Discord MCP later ships, the right place to
add the hook is at the end of `scripts/traction-snapshot.sh`, after the
JSON and summary are written.

---

## Prerequisites

- `bash`, `curl`, `jq` — present by default on Linux / macOS; on Windows
  installed by Git for Windows (`bash`, `curl`) and via `winget install
  jqlang.jq` (`jq`).
- No PyPI / GitHub credentials needed. The script uses the public
  unauthenticated endpoints (`pypistats.org` and `api.github.com`), which
  is enough for one call per week.
- Authenticated `gh` would lift the GitHub API rate limit from 60/h to
  5000/h, but is overkill at this cadence — keep the script credentials-
  free for portability across the future MCP pack.
