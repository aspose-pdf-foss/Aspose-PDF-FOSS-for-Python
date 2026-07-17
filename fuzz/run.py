"""Atheris command-line runner for the repository fuzz targets."""

from __future__ import annotations

import sys

try:
    import atheris
except ImportError as exc:
    raise SystemExit("Install the fuzz extra with: pip install -e '.[fuzz]'") from exc

with atheris.instrument_imports():
    if __package__:
        from .targets import TARGETS
    else:
        from targets import TARGETS


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in TARGETS:
        names = ", ".join(sorted(TARGETS))
        raise SystemExit(f"Usage: python fuzz/run.py <target> [corpus] [flags]\nTargets: {names}")
    target_name = sys.argv.pop(1)
    atheris.Setup(sys.argv, TARGETS[target_name])
    atheris.Fuzz()


if __name__ == "__main__":
    main()
