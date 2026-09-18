"""Compatibility exports of the unsigned-content absorber and its result.

The versions that lived here returned whatever keyword arguments ``extract``
was given rather than reading a document. Both import paths now give the
implementations in :mod:`aspose_pdf.forms`.
"""

from __future__ import annotations

from aspose_pdf.forms import UnsignedContent, UnsignedContentAbsorber

__all__ = ["UnsignedContent", "UnsignedContentAbsorber"]
