"""Markdown conversion option container."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["MarkdownSaveOptions"]


@dataclass(slots=True)
class MarkdownSaveOptions:
    """Options for saving PDF documents as Markdown.

    Passed to :meth:`aspose_pdf.Document.save`, each is honoured as the
    matching argument of :meth:`~aspose_pdf.Document.save_as_markdown`:

    * ``extract_images`` -- carry figures over (``embed_images``);
    * ``image_directory`` or ``resources_directory_name`` -- the folder the
      figures are written to as PNG files and linked from, a relative one
      taken from the folder of the Markdown file (the two name the same
      folder, so setting both is refused); unset, figures are embedded as
      ``data:`` URIs;
    * ``markdown_format`` -- ``"GFM"`` or ``"CommonMark"`` (any other value is
      refused).
    """

    resources_directory_name: str | None = None
    markdown_format: str = "GFM"
    extract_images: bool = True
    image_directory: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "resources_directory_name": self.resources_directory_name,
            "markdown_format": self.markdown_format,
            "extract_images": self.extract_images,
            "image_directory": self.image_directory,
        }
