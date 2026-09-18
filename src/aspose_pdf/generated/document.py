"""Compatibility export of :class:`aspose_pdf.document.Document`.

This module used to hold a second, older ``Document`` of its own, which had
fallen behind the real one: ``merge`` appended blank pages, ``save`` broke the
signatures of a signed file, a wrong password to ``decrypt`` passed silently,
and ``info``, ``version``, ``form``, ``outlines`` and ``permissions`` were always
``None``. Both import paths now give the one implementation.
"""

from __future__ import annotations

from aspose_pdf.document import Document

__all__ = ["Document"]
