"""Parse page range expressions for the package's page selection APIs."""

from __future__ import annotations

import re

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import PdfLoadLimits, _LoadBudget

__all__ = ["parse_page_range"]

_RANGE = re.compile(r"([0-9]+|last)(?:\s*-\s*(last|[0-9]*))?")


def parse_page_range(
    expression: str,
    page_count: int,
    *,
    one_based: bool = True,
    limits: PdfLoadLimits | None = None,
) -> tuple[int, ...]:
    """Return zero-based indices from page numbers, keywords and ranges.

    ``"1-3,7,10-"`` selects pages 1 through 3, page 7, and page 10 through
    the last page. Numbers in the expression are one-based by default;
    ``one_based=False`` interprets them as zero-based instead. The returned
    indices are always zero-based, ready for ``Document.extract_pages`` and
    APIs accepting a ``pages`` argument. Add one to each index for facades
    accepting one-based page numbers.

    Case-insensitive ``odd`` and ``even`` select physical pages 1, 3, 5, ...
    and 2, 4, 6, ... regardless of ``one_based`` or page labels. ``last``
    selects the final page and can be either endpoint: ``"2-last"`` selects
    from page 2 onward; ``"last-1"`` selects every page in reverse order with
    the default numbering. Any range may descend, such as ``"4-2"``.

    Whitespace around items and hyphens is ignored. Input order and repeated
    pages are preserved, including overlaps between comma-separated items.
    Empty items, unsupported syntax and pages outside ``page_count`` raise
    ``PdfValidationException``. Numbers, ``last`` and open-ended ranges require
    an existing page. ``odd`` and ``even`` return an empty tuple when no pages
    match; document extraction and splitting still reject empty selections.
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
            value = upper if text == "last" else int(text.lstrip("0") or "0")
        except ValueError:
            raise PdfValidationException("Page number out of range") from None
        if not offset <= value <= upper:
            raise PdfValidationException("Page number out of range")
        return value

    for item in expression.split(","):
        item = item.strip().lower()
        if item in ("odd", "even"):
            start = 0 if item == "odd" else 1
            budget.check(
                len(indexes) + (page_count + 1 - start) // 2,
                "max_container_items",
                "expanded page range selection",
            )
            indexes.extend(range(start, page_count, 2))
            continue
        match = _RANGE.fullmatch(item)
        if match is None:
            raise PdfValidationException(
                "Page range must contain page numbers, odd, even, last, "
                "inclusive ranges (1-3 or 3-1), or open-ended ranges (3-), "
                "separated by commas"
            )
        start = number(match[1])
        if match[2] is None:
            end = start
        elif match[2]:
            end = number(match[2])
        else:
            end = upper
        step = 1 if end >= start else -1
        budget.check(
            len(indexes) + abs(end - start) + 1,
            "max_container_items",
            "expanded page range selection",
        )
        indexes.extend(range(start - offset, end - offset + step, step))
    return tuple(indexes)
