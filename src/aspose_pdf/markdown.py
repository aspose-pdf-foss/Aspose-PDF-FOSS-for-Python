"""Markdown conversion option container."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["MarkdownSaveOptions"]


@dataclass(slots=True)
class MarkdownSaveOptions:
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
