"""Save format enumeration for document export."""

from __future__ import annotations

from enum import Enum

__all__ = ["SaveFormat"]


class SaveFormat(Enum):
    """Format for saving PDF documents.

    Only :attr:`PDF` is implemented. The remaining members are
    API-compatibility placeholders: passing one to
    :meth:`aspose_pdf.Document.save` raises
    :class:`~aspose_pdf.exceptions.UnsupportedFeatureException` rather than
    writing a PDF under another extension.
    """

    PDF = "PDF"
    """Portable Document Format."""

    PPTX = "PPTX"
    """PowerPoint Open XML Presentation. Not implemented; saving raises."""
