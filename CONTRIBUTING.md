# Contributing to TopicForge

Thanks for the interest. TopicForge is a small indie project run by one
maintainer ; the contribution loop is intentionally tight.

## What contributions land easily

- **Parser fixes for new ROS2 distros.** Pure parsers in
  `src/topicforge/adapters/ros2_live/` are designed to be tweak-able
  on a new distro without touching the adapter shell. A failing
  parser test against Iron / Jazzy / Kilted with a 5-line fix is the
  ideal contribution shape.
- **Bug reports with reproducers.** See
  `.github/ISSUE_TEMPLATE/bug_report.yml`. A failing pytest case in
  the body of the issue is gold.
- **Doc improvements.** Typos, broken links, unclear quickstart
  steps. Open a PR directly: these merge fast.
- **Cross-platform regressions.** TopicForge is Windows-first ; if you
  hit a Mac / Linux-specific breakage, file it with the stack trace.

## What contributions are harder to land

- **New MCP tools.** The tool surface is intentionally capped (11 as
  of v0.4.0): any expansion is a strategy decision documented in
  `docs/product-plan.md section 11` "Scope creep within the TopicForge
  umbrella". File an issue describing the use case first ; the
  maintainer will close, defer, or sponsor the work.
- **New backends.** The `RosAdapter` / `MiddlewareAdapter` protocol is
  designed to take new adapters, but each one comes with a long-term
  maintenance cost. File an issue with the use case ; expect a
  conversation before the PR.
- **Refactors without a triggering bug or feature.** Cosmetic
  refactors are a net cost for a solo maintainer. Tie any structural
  change to a concrete user need.

## Development setup

Requires Python 3.11+. Tested on Python 3.11 / 3.12 / 3.13 on Ubuntu
and Windows.

```bash
git clone https://github.com/yaniswav/TopicForge.git
cd TopicForge
python -m venv .venv
source .venv/bin/activate          # Linux / macOS
# .venv\Scripts\Activate.ps1       # Windows PowerShell
pip install -e ".[dev]"
```

## The contract before a PR

Every PR runs through CI on the matrix above. Locally, the bundle is :

```bash
make check       # lint + tests (Linux / macOS / WSL)
# or on Windows PowerShell:
python -m ruff check src tests
python -m ruff format --check src tests
python -m pytest -q
```

A green `make check` on Python 3.12 + the platform you developed on is
the baseline. CI catches the rest.

### Mock-first development

Every test must run without a real ROS2 install. The `mock` adapter
covers the full tool surface with deterministic fixtures. Tests that
need a binding declare a `requires_*` pytest marker and auto-skip
when the binding is absent: see `tests/test_cyclone_adapter.py` for
the pattern.

### Layer separation

The architecture is intentionally layered (`server/ -> tools/ ->
services/ -> adapters/`). Tool handlers never call `subprocess` ;
adapters never validate MCP-level inputs ; services never know which
backend they're talking to. PRs that violate this earn a "rework"
review.

### Pure parsers convention

Parsing of `ros2` CLI output lives in module-level functions named
`parse_<thing>` separated from subprocess wrappers. They take a
string in, return a typed value out, and are tested without ROS2.
This is what makes new-distro fixes a 5-line patch instead of an
adapter rewrite.

## Commit conventions

- Short, imperative subject (under 70 chars). Lowercase initial verb is
  fine. `fix: parse_csv_echo handles empty stamp` is the typical
  shape.
- Body explains the *why*, not the *what* (the diff is the what).
- One concern per commit: no "fix tests + add feature + docs" bundles.
- We do not use the `Co-Authored-By: Claude` trailer, even when an AI
  assistant was used. The contributor's authorship line is the
  contract.

## Releasing

The maintainer handles releases. Tagging `v*` on `main` triggers the
`publish.yml` workflow -> OIDC Trusted Publisher -> PyPI. See
`.claude/skills/topicforge/release-checklist/` for the internal
checklist (not shipped to PyPI sdist).

## Security

Vulnerability disclosures go to the address in [SECURITY.md](SECURITY.md),
not to public issues.
