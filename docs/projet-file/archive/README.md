# `docs/projet-file/archive/`: superseded strategic artifacts

Historical material that no longer drives current decisions but is
preserved in git so the audit trail and marketing history remain
bisectable across releases.

## Subfolders

- **`audits-v0.1.2/`**: the two pre-v0.2.0 audit reports
  (`security-audit-v0.1.2.md`, `architecture-audit-v0.1.2.md`) that
  produced the 13-item follow-up triage. Source rapports referenced
  from `../audit-followup-triage-v0.2.0.md` ; moved here in the v0.5.0
  cleanup since the triage is now the canonical entry point.
- **`launch-posts-v0.3.0/`**: Reddit (r/ROS, r/ClaudeAI) and LinkedIn
  draft posts for the v0.3.0 release. Kept as templates for future
  release launches ; live drafts for the current release belong one
  level up in `../launch-posts/`.

## When to add something here

Move a doc into `archive/` when it (a) is no longer the canonical
source for current decisions, but (b) carries reusable signal: a
template, a historical baseline, an audit reference. If a doc has no
future signal, delete it instead.

## When to retrieve

Audit re-walks (the next time a B-classified item from the original
triage opens for work), launch-post template re-use, retrospective
release reviews.
