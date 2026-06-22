# AGENTS.md

This file contains repository-specific guidance for coding agents and
contributors working on Aspose.PDF FOSS for Python.

## Project Overview

Aspose.PDF FOSS for Python is a Python 3.11+ library for reading, creating,
editing, rendering, validating, and securing PDF documents.

- Distribution name: `aspose-pdf-foss-for-python`
- Import package: `aspose_pdf`
- Source layout: `src/`
- License: MIT
- Current maturity: alpha

The implemented behavior is defined by the public API under `src/aspose_pdf`,
the regression suite under `tests`, and the documented boundaries in
`supported-features.md`.

## Repository Layout

| Path | Purpose |
| --- | --- |
| `src/aspose_pdf/` | Public package modules |
| `src/aspose_pdf/engine/` | COS model, parser, writer, filters, rendering, encryption, and signing internals |
| `src/aspose_pdf/generated/` | Compatibility modules for the supported API surface |
| `tests/` | Unit, regression, and integration tests |
| `supported-features.md` | Detailed feature coverage and limitations |
| `scripts/` | Local check and build wrappers |
| `.github/workflows/` | CI and package publishing workflows |

## Environment Setup

Always work in a virtual environment. Reuse `.venv` when it already exists.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

Optional runtime extras:

```bash
python -m pip install -e '.[images,woff2]'
```

## Validation Commands

Run the smallest relevant test selection while developing, then run the full
release gate before handing off a substantial change.

```bash
python -m pytest tests/test_relevant_area.py -q
python -m ruff check src/
python -m pytest -q
python -m compileall -q src/aspose_pdf
python -m build
python -m twine check dist/*
```

The repository wrappers run the standard package checks:

```bash
scripts/check.sh
scripts/build.sh
```

## Implementation Guidelines

- Write all code comments and docstrings in English.
- Keep the public API Pythonic and preserve established imports from
  `aspose_pdf`.
- Add or update tests for every behavior change and regression fix.
- Prefer public APIs in tests. Access internals only when the test specifically
  targets engine behavior.
- Treat PDF input as untrusted binary data. Reject malformed data with clear
  exceptions rather than silently producing a damaged document.
- Preserve byte-level correctness in cross-reference, encryption, incremental
  update, and digital-signature code.
- Keep lazy and streaming paths lazy; avoid loading complete files or all page
  streams unless the operation requires it.
- Avoid adding mandatory dependencies when a feature can be optional. Declare
  new dependencies and extras in `pyproject.toml`.
- Do not introduce network calls, external services, credentials, or
  environment-file requirements into normal package operation.
- Do not edit generated build artifacts such as `build/`, `dist/`,
  `*.egg-info/`, caches, or compiled bytecode.

## Tests and Fixtures

- Name tests `test_*.py` and keep them deterministic.
- Use `tmp_path` for generated files and clean up resources with context
  managers or `dispose()`.
- Add a focused regression test before or alongside a bug fix.
- Keep PDF fixtures minimal and free of confidential or third-party content
  that cannot be redistributed.
- When changing parsing or writing behavior, test both in-memory bytes and file
  round trips where practical.
- For optional dependencies, cover both the available and unavailable paths
  when feasible.

## Documentation and Claims

- Keep `README.md` concise and suitable for the public GitHub repository.
- Put detailed capability matrices and limitations in
  `supported-features.md`.
- Document unsupported or heuristic behavior explicitly. In particular, do not
  present the built-in PDF/A or PDF/UA checks as certification-grade validation.
- Update examples when public method signatures or defaults change.
- Base feature and compatibility claims on implemented, tested behavior.

## Change Discipline

- Keep changes scoped to the requested task.
- Preserve unrelated user changes in the working tree.
- Maintain compatibility unless a breaking change is intentional and clearly
  documented.
- Before completion, review the diff and report the checks that were actually
  run.
