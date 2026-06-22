"""Convenience re-exports for save-option objects."""

from __future__ import annotations

from enum import Enum

from aspose_pdf.html import HtmlSaveOptions
from aspose_pdf.markdown import MarkdownSaveOptions

__all__ = ["DocFormat", "HtmlSaveOptions", "MarkdownSaveOptions"]


class DocFormat(str, Enum):
    PDF = "PDF"
    HTML = "HTML"
    MARKDOWN = "MARKDOWN"
    SVG = "SVG"
