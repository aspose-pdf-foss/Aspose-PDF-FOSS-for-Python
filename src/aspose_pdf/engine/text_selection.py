"""Validate text extraction options and snapshot page selections."""

from __future__ import annotations

from collections.abc import Iterable

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import PdfLoadLimits, _LoadBudget


def validate_skip_artifacts(value: bool) -> bool:
    """Require an explicit boolean for artifact filtering."""
    if not isinstance(value, bool):
        raise PdfValidationException("skip_artifacts must be a boolean")
    return value


def page_indexes(
    pages: Iterable[int] | None,
    page_count: int | None = None,
    *,
    one_based: bool = False,
    limits: PdfLoadLimits | None = None,
) -> tuple[int, ...] | None:
    """Return zero-based indices, preserving order/repeats; ``None`` means all.

    With no page count, only types and the lower bound can be checked. This
    lets plugin options snapshot a generator once before processing a batch;
    each input's upper bound is checked when the input is available.
    """
    if pages is None:
        return None
    if isinstance(pages, (str, bytes, bytearray)):
        raise PdfValidationException("Page selection must be an iterable of integers")
    try:
        iterator = iter(pages)
    except TypeError:
        raise PdfValidationException(
            "Page selection must be an iterable of integers"
        ) from None
    budget = _LoadBudget(limits)
    start = 1 if one_based else 0
    kind = "Page number" if one_based else "Page index"
    indexes = []
    for position, value in enumerate(iterator, 1):
        budget.check(position, "max_container_items", "text extraction page selection")
        if isinstance(value, bool) or not isinstance(value, int):
            raise PdfValidationException(f"{kind} must be an integer")
        index = value - start
        if index < 0 or (page_count is not None and index >= page_count):
            raise PdfValidationException(f"{kind} out of range: {value}")
        indexes.append(index)
    return tuple(indexes)
