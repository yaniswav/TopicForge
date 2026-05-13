# `docs/projet-file/` — strategic context

This folder holds **non-public strategic material** for TopicForge and the broader robotics/CV MCP pack: action plans, MCP spec drafts, personal strategy notes, market analyses, and reference PDFs.

It is **deliberately excluded from the published sdist** (see `pyproject.toml` `[tool.hatch.build.targets.sdist].exclude`), and the contents are not intended for downstream users of the `topicforge` PyPI package. They live in git so that future Claude Code sessions and Claude Cowork sessions (across machines) can pick up the strategic context without re-explanation.

Canonical public documentation — what a user installing TopicForge should read — lives one level up in `docs/`:

- [`docs/product-plan.md`](../product-plan.md) — phased roadmap, monetization, risk register
- [`docs/pro.md`](../pro.md) — Pro tier early-access page
- [`docs/TESTING.md`](../TESTING.md) — testing paths for the user-facing release
- [`README.md`](../../README.md), [`CLAUDE.md`](../../CLAUDE.md), [`CHANGELOG.md`](../../CHANGELOG.md) at repo root

## What goes in here

- **MCP spec drafts** for the pack growth (`mcp-02-spec.md` and onward — drafted via Stream C of each release plan).
- **Strategic PDFs** (project files, market briefs, personal notes) — references that should not bleed into the public docs but inform decisions.
- **Pack-wide template extractions** — when a convention generalizes from TopicForge to the rest of the pack, the parameterized version lands here (`pack-template/`) before being lifted into a shared template repo.

Release action plans live at `docs/<version>-action-plan.md` (one directory up), not here — they are short-lived per-release coordination docs that the maintainer reaches for during a release window and discards afterward. Promote one into `projet-file/` only if it carries strategic weight beyond its release.

## What does **not** go in here

- Anything a downstream PyPI user needs to install or operate TopicForge. That belongs in `README.md` or `docs/`.
- Anything a contributor needs to make a clean PR. That belongs in `CLAUDE.md` or `.claude/`.
- Secrets, credentials, license keys. The file `TOPICFORGE_LICENSE_KEY` is documented in `CLAUDE.md` §4; actual keys live outside the repo.

## Why a separate folder

The split exists for two reasons. First, the published sdist stays lean — strategic context is internal-only and does not need to ship to PyPI users. Second, the sub-agents have clear lanes: `docs-curator` curates the user-facing docs above this folder; the contents of `projet-file/` are the maintainer's working notes that inform but do not constrain those docs.
