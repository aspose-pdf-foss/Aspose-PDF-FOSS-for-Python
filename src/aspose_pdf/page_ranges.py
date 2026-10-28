"""Parse page range expressions for the package's page selection APIs."""

from __future__ import annotations

import re

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import PdfLoadLimits, _LoadBudget

__all__ = ["parse_page_range"]

_RANGE = re.compile(r"([0-9]+)(?:\s*-\s*([0-9]*))?")


def parse_page_range(
    expression: str,
    page_count: int,
    *,
    one_based: bool = True,
    limits: PdfLoadLimits | None = None,
) -> tuple[int, ...]:
    """Return zero-based page indices from numbers and inclusive ranges.

    ``"1-3,7,10-"`` selects pages 1 through 3, page 7, and page 10 through
    the last page. Numbers in the expression are one-based by default;
    ``one_based=False`` interprets them as zero-based instead. The returned
    indices are always zero-based, ready for ``Document.extract_pages`` and
    APIs accepting a ``pages`` argument. Add one to each index for facades
    accepting one-based page numbers.

    Whitespace around items and hyphens is ignored. Input order and repeated
    pages are preserved. Empty items, descending ranges, unsupported syntax
    and pages outside ``page_count`` raise ``PdfValidationException``.
    Open-ended ranges require a valid starting page, including in empty PDFs.
    Expression bytes and the total expanded selection obey ``limits``.
    Parsing reads no document content.
    """
    if not isinstance(expression, str):
        raise TypeError("expression must be a string")
    if isinstance(page_count, bool) or not isinstance(page_count, int):
        raise TypeError("page_count must be an integer")
    if page_count < 0:
        raise ValueError("page_count must not be negative")
    if not isinstance(one_based, bool):
        raise TypeError("one_based must be a boolean")

    budget = _LoadBudget(limits)
    budget.check(len(expression), "max_object_bytes", "page range expression")
    try:
        size = len(expression.encode("utf-8"))
    except UnicodeEncodeError:
        raise PdfValidationException("Page range contains invalid Unicode text") from None
    budget.check(size, "max_object_bytes", "page range expression")
    budget.check(expression.count(",") + 1, "max_container_items", "page range items")
    offset = 1 if one_based else 0
    upper = page_count - 1 + offset
    indexes: list[int] = []

    def number(text: str) -> int:
        try:
            value = int(text.lstrip("0") or "0")
        except ValueError:
            raise PdfValidationException("Page number out of range") from None
        if not offset <= value <= upper:
            raise PdfValidationException("Page number out of range")
        return value

    for item in expression.split(","):
        match = _RANGE.fullmatch(item.strip())
        if match is None:
            raise PdfValidationException(
                "Page range must contain page numbers, inclusive ranges (1-3), "
                "or open-ended ranges (3-), separated by commas"
            )
        start = number(match[1])
        if match[2] is None:
            end = start
        elif match[2]:
            end = number(match[2])
        else:
            end = upper
        if end < start:
            raise PdfValidationException("Page ranges must be ascending")
        budget.check(
            len(indexes) + end - start + 1,
            "max_container_items",
            "expanded page range selection",
        )
        indexes.extend(range(start - offset, end - offset + 1))
    return tuple(indexes)
