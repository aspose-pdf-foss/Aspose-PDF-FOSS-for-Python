"""Save format enumeration for document export."""

from __future__ import annotations

from enum import Enum

__all__ = ["SaveFormat"]


class SaveFormat(Enum):
    """Format for saving PDF documents.

    :attr:`PDF` is implemented; :attr:`PPTX` is an API-compatibility
    placeholder: passing it to :meth:`aspose_pdf.Document.save` raises
    :class:`~aspose_pdf.exceptions.UnsupportedFeatureException` rather than
    writing a PDF under another extension. HTML, Markdown and SVG exports are
    selected with :class:`~aspose_pdf.save_options.DocFormat` or a save-options
    object.
    """

    PDF = "PDF"
    """Portable Document Format."""

    PPTX = "PPTX"
    """PowerPoint Open XML Presentation. Not implemented; saving raises."""
