"""Compatibility exports of the PDF/A validation options and result.

The classes that lived here were inert look-alikes of the real ones -- options
that validated nothing and a result nothing filled in. Both import paths now
give the implementations in :mod:`aspose_pdf.pdfa`.
"""

from __future__ import annotations

from aspose_pdf.pdfa import PdfAValidateOptions, PdfAValidationResult

__all__ = ["PdfAValidateOptions", "PdfAValidationResult"]
