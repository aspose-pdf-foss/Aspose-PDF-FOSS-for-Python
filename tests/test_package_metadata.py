import tomllib
from pathlib import Path

from aspose_pdf import __version__
from aspose_pdf._version import __release_version__

ROOT = Path(__file__).resolve().parents[1]


RELEASE_VERSION = "0.1.0"


def test_package_declares_the_release_version() -> None:
    """The declared version is the source of truth for a release.

    ``__version__`` reports whatever distribution is *installed*, which lags an
    edited checkout until the package is reinstalled — so asserting on it would
    silently pass over a version bump.
    """
    assert __release_version__ == RELEASE_VERSION
    assert isinstance(__version__, str)


def test_release_metadata_contract() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())

    project = pyproject["project"]
    assert project["name"] == "aspose-pdf-foss-for-python"
    assert project["license"] == "MIT"
    assert project["requires-python"] == ">=3.11"
    assert project["authors"] == [{"name": "Aspose Pty Ltd"}]
    assert "Development Status :: 4 - Beta" in project["classifiers"]
    assert "Programming Language :: Python :: 3.11" in project["classifiers"]
    assert "Programming Language :: Python :: 3.12" in project["classifiers"]
    assert "Programming Language :: Python :: 3.13" in project["classifiers"]

    setuptools = pyproject["tool"]["setuptools"]
    assert setuptools["package-data"]["aspose_pdf"] == ["py.typed"]
    assert (
        setuptools["dynamic"]["version"]["attr"]
        == "aspose_pdf._version.__release_version__"
    )


def test_readme_documents_public_project_contract() -> None:
    readme = (ROOT / "README.md").read_text()

    assert readme.startswith("# Aspose.PDF FOSS for Python")
    assert "## Installation" in readme
    assert "## Quick Start" in readme
    assert "supported-features.md" in readme
    assert "Aspose Pty Ltd" in readme


def test_changelog_documents_the_declared_version() -> None:
    """A release must carry a dated changelog section and a link reference.

    This ties the version bump and the changelog together, so neither can move
    without the other. Work landing after the cut belongs under ``[Unreleased]``
    and is expected there — only the *released* section must be dated and
    complete.
    """
    changelog = (ROOT / "CHANGELOG.md").read_text()

    assert f"## [{RELEASE_VERSION}] - " in changelog
    assert f"[{RELEASE_VERSION}]: https://" in changelog

    # The released section sits below [Unreleased] and is not empty.
    _preamble, _unreleased, released = changelog.partition(
        f"## [{RELEASE_VERSION}] - "
    )
    assert "## [Unreleased]" in _preamble, "the release must follow [Unreleased]"
    assert released.lstrip().splitlines()[1:], "the released section is empty"
    assert "\n- " in released
