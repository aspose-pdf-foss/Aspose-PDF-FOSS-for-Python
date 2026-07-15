"""Reproducibility checks for the bundled CMap data generator."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.build_cmap_data import _git_blob, _verify_checkout


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
    )


def test_generator_reads_canonical_blob_from_clean_crlf_checkout(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "source"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "core.autocrlf", "true")
    source = repo / "mapping.txt"
    source.write_bytes(b"first\nsecond\n")
    _git(repo, "add", "mapping.txt")
    _git(repo, "commit", "-q", "-m", "fixture")
    revision = _git(repo, "rev-parse", "HEAD").stdout.decode().strip()

    source.unlink()
    _git(repo, "checkout", "--", "mapping.txt")
    assert source.read_bytes() == b"first\r\nsecond\r\n"
    assert _git(repo, "status", "--porcelain").stdout == b""
    _verify_checkout(repo, revision)
    assert _git_blob(repo, Path("mapping.txt")) == b"first\nsecond\n"


def test_generator_rejects_untracked_checkout_content(tmp_path: Path) -> None:
    repo = tmp_path / "source"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    tracked = repo / "tracked.txt"
    tracked.write_text("tracked\n", encoding="ascii")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-q", "-m", "fixture")
    revision = _git(repo, "rev-parse", "HEAD").stdout.decode().strip()
    (repo / "untracked.txt").write_text("untracked\n", encoding="ascii")

    with pytest.raises(ValueError, match="is not clean"):
        _verify_checkout(repo, revision)


def test_sdist_manifest_includes_cmap_generator() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = (root / "MANIFEST.in").read_text(encoding="utf-8")

    assert "include scripts/build_cmap_data.py" in manifest.splitlines()
