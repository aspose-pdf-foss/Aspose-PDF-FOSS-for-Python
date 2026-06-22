#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -d ".venv" ]]; then
  source .venv/bin/activate
elif [[ -d "../.venv" ]]; then
  source ../.venv/bin/activate
else
  echo "Missing .venv. Create it before running package checks." >&2
  exit 1
fi

python -m pip install -e .[dev]
python -m ruff check src/
python -m pytest -q
python -m compileall -q src/aspose_pdf
python -m build
python -m twine check dist/*
