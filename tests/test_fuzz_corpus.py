"""Replay the redistributable fuzz corpus without an Atheris dependency."""

from __future__ import annotations

from pathlib import Path

import pytest

from fuzz.targets import TARGETS

_CORPUS_ROOT = Path(__file__).parents[1] / "fuzz" / "corpus"
_CASES = [
    (target_name, seed)
    for target_name in sorted(TARGETS)
    for seed in sorted((_CORPUS_ROOT / target_name).iterdir())
    if seed.is_file()
]


@pytest.mark.parametrize(
    ("target_name", "seed"),
    _CASES,
    ids=[f"{target}-{seed.name}" for target, seed in _CASES],
)
def test_fuzz_seed(target_name: str, seed: Path) -> None:
    TARGETS[target_name](seed.read_bytes())
