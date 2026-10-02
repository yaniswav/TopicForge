# Contributing to TopicForge

TopicForge is a small project run by one maintainer, so the contribution loop is short.

## What contributions land easily

- Parser fixes for new ROS2 distros. Pure parsers in
  `src/topicforge/adapters/ros2_live/` are designed to be tweak-able
  on a new distro without touching the adapter shell. A failing
  parser test against Iron / Jazzy / Kilted with a 5-line fix is the
  ideal contribution.
- Bug reports with reproducers. See
  `.github/ISSUE_TEMPLATE/bug_report.yml`. A failing pytest case in
  the body of the issue is the most useful thing you can attach.
- Doc improvements. Typos, broken links, unclear quickstart
  steps. Open a PR directly: these merge fast.
- Cross-platform regressions. TopicForge is Windows-first ; if you
  hit a Mac / Linux-specific breakage, file it with the stack trace.
- Reports from a real DDS bus. The Cyclone adapter has run against
  Cyclone and Dust participants; the Fast adapter and other vendors on
  the bus have not been observed yet. What you see there (what worked,
  what raised, what looked wrong) helps more than anything else.
  Include the vendors on your bus and the `health_check` output.
  `python examples/dds/run_all.py` is a quick way to produce one.

## What contributions are harder to land

- New MCP tools. The tool surface is kept deliberately small (see
  `docs/product-plan.md section 11`, "Scope creep within the TopicForge
  umbrella"). File an issue describing the use case first; the
  maintainer will close, defer, or sponsor the work.
- New backends. The `RosAdapter` / `MiddlewareAdapter` protocol is
  designed to take new adapters, but each one comes with a long-term
  maintenance cost. File an issue with the use case ; expect a
  conversation before the PR.
- Refactors without a triggering bug or feature. Cosmetic
  refactors cost a solo maintainer more than they give. Tie any
  structural change to a concrete user need.

## Development setup

Requires Python 3.10+. CI runs Python 3.10 / 3.11 / 3.12 / 3.13 on
Ubuntu and Windows, so avoid syntax or dependencies newer than 3.10.

```bash
git clone https://github.com/yaniswav/TopicForge.git
cd TopicForge
python -m venv .venv
source .venv/bin/activate          # Linux / macOS
# .venv\Scripts\Activate.ps1       # Windows PowerShell
pip install -e ".[dev]"
```

## The contract before a PR

Every PR runs through CI on the matrix above. Locally:

```bash
make check       # lint + tests (Linux / macOS / WSL)
# or on Windows PowerShell:
python -m ruff check src tests
python -m ruff format --check src tests
python -m pytest -q
```

A green `make check` on Python 3.12 and your own platform is enough;
CI covers the rest.

### Mock-first development

Every test must run without a real ROS2 install. The `mock` adapter
covers the full tool surface with deterministic fixtures. Tests that
need a binding declare a `requires_*` pytest marker and auto-skip
when the binding is absent: see `tests/test_cyclone_adapter.py` for
the pattern. The `integration` marker (real-bus demo) is
deselected by default; it runs the multi-vendor demo driver and skips
unless its participants are built, see `scripts/integration/README.md`.

### Layer separation

The architecture is intentionally layered (`server/ -> tools/ ->
services/ -> adapters/`). Tool handlers never call `subprocess` ;
adapters never validate MCP-level inputs ; services never know which
backend they're talking to. PRs that violate this earn a "rework"
review.

### Pure parsers convention

Parsing of `ros2` CLI output lives in module-level functions named
`parse_<thing>` separated from subprocess wrappers. They take a
string in, return a typed value out, and are tested without ROS2,
so a new-distro fix is a 5-line patch instead of an adapter rewrite.

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
`publish.yml` workflow -> OIDC Trusted Publisher -> PyPI. The
maintainer's release checklist is internal and not part of this
repository.

## Security

Vulnerability disclosures go to the address in [SECURITY.md](SECURITY.md),
not to public issues.
