"""The files an HTML, Markdown or SVG export writes, and the options that shape them.

An export can be several files -- a document per page, the images it links to
-- so everything is produced in memory first, the targets are checked against
``overwrite`` all together, and only then is anything written, each file
atomically (:func:`~aspose_pdf.engine.file_output.write_file_atomically`). A
refused save writes nothing; a failed one leaves every file it had not reached
as it was, and every file it had reached complete.
"""

from __future__ import annotations

import os
import urllib.parse
from pathlib import Path
from typing import Any

from aspose_pdf.engine.file_output import write_file_atomically
from aspose_pdf.exceptions import PdfValidationException, UnsupportedFeatureException


def directory_beside(target: Path, directory: str | os.PathLike[str]) -> Path:
    """*directory*, a relative one taken from the folder *target* is written to."""
    path = Path(directory)
    return path if path.is_absolute() else target.parent / path


def relative_url(path: Path, base: Path) -> str:
    """The URL of *path* as a document in folder *base* refers to it."""
    try:
        relative = os.path.relpath(path, base)
    except ValueError:  # another drive (Windows): no relative path exists
        return path.resolve().as_uri()
    return urllib.parse.quote(Path(relative).as_posix())


class ExportImages:
    """Figures written as PNG files in *directory*, each distinct image once.

    Called with a figure's PNG bytes, it names the file the image goes to and
    returns the URL the document at *base* refers to it by.
    """

    def __init__(self, directory: Path, stem: str, base: Path) -> None:
        self.directory = directory
        self.stem = stem
        self.base = base
        self.files: dict[bytes, Path] = {}

    def __call__(self, png: bytes) -> str:
        path = self.files.get(png)
        if path is None:
            path = self.directory / f"{self.stem}-image-{len(self.files) + 1}.png"
            self.files[png] = path
        return relative_url(path, self.base)

    def outputs(self) -> list[tuple[Path, bytes]]:
        return [(path, png) for png, path in self.files.items()]


def check_targets(paths: list[Path], overwrite: bool) -> None:
    """Refuse the whole export when one of its files exists and may not be replaced."""
    if overwrite:
        return
    for path in paths:
        if path.exists():
            raise FileExistsError(f"File already exists: {path}")


def write_files(files: list[tuple[Path, bytes]], overwrite: bool) -> None:
    """Write every file of an export, after checking all of them."""
    check_targets([path for path, _ in files], overwrite)
    for path, data in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_file_atomically(path, data)


def html_arguments(options: Any) -> dict[str, Any]:
    """``Document.save_as_html`` keywords for an ``HtmlSaveOptions``."""
    for name in ("xps_intermediate_file", "aps_intermediate_file"):
        if getattr(options, name, None) is not None:
            raise UnsupportedFeatureException(
                f"HtmlSaveOptions.{name} names an intermediate file this export "
                "does not produce: it converts the page structure directly"
            )
    gap = getattr(options, "max_distance_between_text_lines", None)
    return {
        "split_into_pages": bool(getattr(options, "split_into_pages", False)),
        "resources_directory": getattr(options, "resources_directory", None),
        "clip_to_page": bool(getattr(options, "use_area_clipping", True)),
        "paragraph_gap": None if gap is None else float(gap),
    }


def markdown_arguments(options: Any) -> dict[str, Any]:
    """``Document.save_as_markdown`` keywords for a ``MarkdownSaveOptions``."""
    image_directory = getattr(options, "image_directory", None)
    resources_name = getattr(options, "resources_directory_name", None)
    if image_directory is not None and resources_name is not None:
        raise PdfValidationException(
            "MarkdownSaveOptions: set image_directory or resources_directory_name, "
            "not both -- they name the same folder"
        )
    return {
        "embed_images": bool(getattr(options, "extract_images", True)),
        "image_directory": image_directory if image_directory is not None else resources_name,
        "markdown_format": getattr(options, "markdown_format", "GFM"),
    }
