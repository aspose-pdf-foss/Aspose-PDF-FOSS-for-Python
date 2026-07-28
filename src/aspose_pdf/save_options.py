"""Convenience re-exports for save-option objects."""

from __future__ import annotations

from enum import Enum

from aspose_pdf.html import HtmlSaveOptions
from aspose_pdf.markdown import MarkdownSaveOptions

__all__ = ["DocFormat", "HtmlSaveOptions", "MarkdownSaveOptions"]


class DocFormat(str, Enum):  # noqa: UP042
    """Target format for a save operation.

    Only :attr:`PDF` is implemented; the other members are API-compatibility
    placeholders that make :meth:`aspose_pdf.Document.save` raise
    :class:`~aspose_pdf.exceptions.UnsupportedFeatureException`.
    """

    PDF = "PDF"
    HTML = "HTML"
    MARKDOWN = "MARKDOWN"
    SVG = "SVG"
