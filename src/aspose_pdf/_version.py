"""Version helpers for the ``aspose_pdf`` package."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version


__release_version__ = "0.1.0a0"


def _detect_installed_version() -> str:
    try:
        return version("aspose-pdf-foss-for-python")
    except PackageNotFoundError:
        return __release_version__


__version__ = _detect_installed_version()
