from pathlib import Path
import tomllib

from aspose_pdf import __version__


ROOT = Path(__file__).resolve().parents[1]


def test_package_exposes_version() -> None:
    assert isinstance(__version__, str)
    assert __version__ == "0.1.0a0"


def test_release_metadata_contract() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())

    project = pyproject["project"]
    assert project["name"] == "aspose-pdf-foss-for-python"
    assert project["license"] == "MIT"
    assert project["requires-python"] == ">=3.11"
    assert project["authors"] == [{"name": "Aspose Pty Ltd"}]
    assert "Development Status :: 3 - Alpha" in project["classifiers"]
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
