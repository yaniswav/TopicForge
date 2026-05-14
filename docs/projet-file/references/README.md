# `docs/projet-file/references/` — external reference material

This folder pins external documents that informed strategic decisions, kept in git so any future Claude Code or human contributor can reproduce the reasoning without depending on a live URL.

## Current references

| File | Source | Used for |
| --- | --- | --- |
| `omg-dds-interop-2025-05-08.xlsx` | [omg-dds/dds-rtps](https://omg-dds.github.io/dds-rtps/test_results.html) | Validation of the multi-vendor positioning in v0.3.0. See `docs/dds-interop-matrix.md` for the human-readable summary derived from this file. |

## Convention

External reference files (xlsx, pdf, json snapshots from standard bodies) live here. Sized to ~100 KB max each — if larger, link instead of vendoring. Each file is tracked in git via the `.gitignore` allowlist `!/docs/projet-file/references/**`. They are excluded from the PyPI sdist via `[tool.hatch.build.targets.sdist].exclude` so end users never download them.

When a reference becomes stale (new OMG report, updated spec), update both the file here and the markdown summary in `docs/` that derives from it. The date in the filename helps audit which snapshot informed which release.
