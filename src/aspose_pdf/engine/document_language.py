"""Bounded access to the catalog's default natural language declaration."""

from __future__ import annotations

import re

from aspose_pdf.exceptions import PdfParseException, PdfValidationException
from aspose_pdf.load_limits import _LoadBudget

from .cos import (
    PdfDictionary,
    PdfIndirectReference,
    PdfName,
    PdfNull,
    PdfStream,
    PdfString,
    decode_pdf_text_string,
)

_LANGUAGE_TAG = re.compile(r"(?:[A-Za-z]{1,8}(?:-[A-Za-z0-9]{1,8})*)?")
_LANG = PdfName("Lang")


def _resolve(pdf, value, budget):
    """Follow metadata references without accepting a missing object or a loop."""
    seen = set()
    while isinstance(value, PdfIndirectReference):
        budget.check(len(seen) + 1, "max_nesting_depth", "document language references")
        key = value.object_number, value.gen_number
        if key in seen:
            raise PdfParseException("Document language reference contains a cycle")
        seen.add(key)
        value = pdf._resolve(value)
        if value is None:
            raise PdfParseException("Document language reference was not found")
    return value


def _catalog(pdf, budget, *, create=False):
    if pdf._cos_doc is None:
        if not create:
            return None
        budget.check_objects(2)
        budget.check(3, "max_container_items", "document language catalog")
        pdf._ensure_cos()
    root = _resolve(pdf, pdf._cos_doc.trailer.mapping.get(PdfName("Root")), budget)
    if not isinstance(root, PdfDictionary) or isinstance(root, PdfStream):
        raise PdfParseException("Document language catalog must be a dictionary")
    budget.check(len(root.mapping), "max_container_items", "document language catalog")
    return root


def get_language(document):
    """Read the declared text string, preserving unknown or nonstandard spelling."""
    pdf = document._engine_pdf
    budget = _LoadBudget(document.load_limits)
    root = _catalog(pdf, budget)
    if root is None:
        return None
    value = _resolve(pdf, root.mapping.get(_LANG), budget)
    if value is None or isinstance(value, PdfNull):
        return None
    if not isinstance(value, PdfString):
        raise PdfParseException("Document language must be a text string")
    budget.check(len(value.value), "max_object_bytes", "document language string")
    return decode_pdf_text_string(value)


def set_language(document, value):
    """Replace just /Lang, without changing shared strings or page content."""
    budget = _LoadBudget(document.load_limits)
    if value is not None:
        if not isinstance(value, str):
            raise TypeError("language must be a string or None")
        budget.check(len(value), "max_object_bytes", "document language string")
        if _LANGUAGE_TAG.fullmatch(value) is None:
            raise PdfValidationException(
                "language must be empty or use 1-8 character ASCII subtags "
                "separated by hyphens, with an alphabetic first subtag"
            )
    root = _catalog(document._engine_pdf, budget, create=value is not None)
    if root is None:
        return
    if value is None:
        root.mapping.pop(_LANG, None)
    else:
        budget.check(
            len(root.mapping) + int(_LANG not in root.mapping),
            "max_container_items",
            "document language catalog",
        )
        root.mapping[_LANG] = PdfString(value)
