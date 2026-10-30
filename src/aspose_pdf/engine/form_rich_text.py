"""Read and author the string-valued rich text of AcroForm text fields."""

from __future__ import annotations

import math
from typing import Any
from xml.parsers import expat

from aspose_pdf.exceptions import PdfValidationException, UnsupportedFeatureException

from .cos import (
    PdfName,
    PdfNull,
    PdfNumber,
    PdfStream,
    PdfString,
    decode_pdf_text_string,
)
from .form_field_edit import _check_text, _check_text_appearance_font
from .form_fields import field_attribute, widget_dictionaries

_RICH_TEXT = 1 << 25
_INCOMPATIBLE = (1 << 13) | (1 << 20) | (1 << 24)


def rich_text_value(pdf: Any, field: Any) -> str | None:
    """Read inherited markup without decoding page or appearance streams."""
    value = field_attribute(pdf, field, "RV")
    if isinstance(value, PdfString):
        return decode_pdf_text_string(value)
    if isinstance(value, PdfStream):
        raise UnsupportedFeatureException(
            "Stream-valued field rich text is not supported"
        )
    if value is not None and not isinstance(value, PdfNull):
        raise PdfValidationException("Field rich text must be a PDF text string")
    return None


def plain_text_changes(pdf: Any, field: Any) -> dict:
    """Mask stale inherited markup when a field receives a plain answer."""
    flags = field_attribute(pdf, field, "Ff")
    flags = int(flags.value) if isinstance(flags, PdfNumber) else 0
    if not flags & _RICH_TEXT and field_attribute(pdf, field, "RV") is None:
        return {}
    return {PdfName("RV"): PdfNull(), PdfName("Ff"): PdfNumber(flags & ~_RICH_TEXT)}


def _validate_markup(pdf: Any, markup: str) -> None:
    """Bound XML parsing and refuse declarations before extracting styled runs."""
    parser = expat.ParserCreate()
    depth = count = 0

    def start(tag: str, attributes: dict) -> None:
        nonlocal depth, count
        depth += 1
        count += 1
        pdf._load_budget.check(depth, "max_nesting_depth", "field rich-text depth")
        pdf._load_budget.check(count, "max_container_items", "field rich-text elements")
        pdf._load_budget.check(
            len(attributes), "max_container_items", "rich-text attributes"
        )
        if ":" in tag:
            raise PdfValidationException(
                "Rich-text elements must use unprefixed XHTML names"
            )
        if depth == 1 and tag != "body":
            raise PdfValidationException("rich_text must have a <body> root element")
        namespace = attributes.get("xmlns")
        if namespace not in (None, "", "http://www.w3.org/1999/xhtml"):
            raise PdfValidationException("rich_text must use the XHTML namespace")

    def end(tag: str) -> None:
        nonlocal depth
        depth -= 1

    def doctype(*args: Any) -> None:
        raise PdfValidationException(
            "Rich text with a DOCTYPE or entity declarations is not accepted"
        )

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.StartDoctypeDeclHandler = doctype
    try:
        for offset in range(0, len(markup), 64 * 1024):
            parser.Parse(markup[offset : offset + 64 * 1024], False)
        parser.Parse("", True)
    except expat.ExpatError as exc:
        raise PdfValidationException(
            f"rich_text must be well-formed XHTML: {exc}"
        ) from None


def set_rich_text(pdf: Any, field: Any, kind: str, markup: str | None) -> None:
    """Validate markup and field modes before updating rich and plain values."""
    if kind != "text":
        raise PdfValidationException("rich_text applies only to text fields")
    if markup is not None and not isinstance(markup, str):
        raise TypeError("rich_text must be a string or None")
    flags = field_attribute(pdf, field, "Ff")
    flags = int(flags.value) if isinstance(flags, PdfNumber) else 0
    if markup is None:
        changes = plain_text_changes(pdf, field)
        if not changes:
            return
    else:
        if flags & _INCOMPATIBLE:
            raise PdfValidationException(
                "Rich text cannot be combined with password, file selection or comb"
            )
        _check_text(pdf, markup)
        _validate_markup(pdf, markup)
        from .rich_text import RichStyle, parse_rich_text

        try:
            paragraphs = parse_rich_text(markup, RichStyle(), strict=True)
        except (ValueError, AssertionError):
            raise PdfValidationException(
                "rich_text contains an invalid style"
            ) from None
        if any(
            not math.isfinite(run.style.size)
            for paragraph in paragraphs
            for run in paragraph
        ):
            raise PdfValidationException("Rich-text font sizes must be finite")
        plain = "\n".join(
            "".join(run.text for run in paragraph) for paragraph in paragraphs
        )
        _check_text(pdf, plain)
        changes = {
            PdfName("RV"): PdfString(markup),
            PdfName("V"): PdfString(plain),
            PdfName("Ff"): PdfNumber(flags | _RICH_TEXT),
        }
    _check_text_appearance_font(pdf, field)
    for widget in widget_dictionaries(pdf, field):
        _check_text_appearance_font(pdf, widget)
    field.mapping.update(changes)
