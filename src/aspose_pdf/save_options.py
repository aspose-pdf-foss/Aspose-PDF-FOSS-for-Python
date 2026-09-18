"""Convenience re-exports for save-option objects."""

from __future__ import annotations

from enum import Enum

from aspose_pdf.html import HtmlSaveOptions
from aspose_pdf.markdown import MarkdownSaveOptions

__all__ = ["DocFormat", "HtmlSaveOptions", "MarkdownSaveOptions"]


class DocFormat(str, Enum):  # noqa: UP042
    """Target format for a save operation.

    :attr:`PDF` writes PDF; :attr:`HTML`, :attr:`MARKDOWN` and :attr:`SVG`
    export through :meth:`aspose_pdf.Document.save_as_html`,
    :meth:`~aspose_pdf.Document.save_as_markdown` and
    :meth:`~aspose_pdf.Document.save_as_svg` with their default settings.
    """

    PDF = "PDF"
    HTML = "HTML"
    MARKDOWN = "MARKDOWN"
    SVG = "SVG"
