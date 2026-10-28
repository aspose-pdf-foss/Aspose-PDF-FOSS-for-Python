"""Import consecutive, fixed-size page groups without scanning page contents."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

from .bookmark_split import _range_outlines, _snapshot
from .text_split import _check_result, _refresh_targets

if TYPE_CHECKING:
    from .simple_pdf import SimplePdf


def validate_pages_per_part(pages_per_part: int) -> None:
    """Require a positive integer before reading or importing any pages."""
    if isinstance(pages_per_part, bool) or not isinstance(pages_per_part, int):
        raise TypeError("pages_per_part must be an integer")
    if pages_per_part < 1:
        raise ValueError("pages_per_part must be at least 1")


def split_every(
    pdf: SimplePdf, pages_per_part: int, *, outlines: list | None = None
) -> Iterator[SimplePdf]:
    """Yield independent parts, restoring the source model before every yield."""
    pdf._ensure_not_disposed()
    validate_pages_per_part(pages_per_part)
    count = len(pdf.pages)
    budget = pdf._load_budget
    budget.check_pages(count)
    budget.check(
        (count + pages_per_part - 1) // pages_per_part,
        "max_container_items",
        "page-count split parts",
    )
    budget.check(
        min(count, pages_per_part), "max_container_items", "page-count split selection"
    )
    if not count:
        return
    roots = pdf._outlines_data if outlines is None else outlines
    snapshot, _starts = _snapshot(pdf, roots, 1)
    for start in range(0, count, pages_per_part):
        pdf._ensure_not_disposed()
        stop = min(start + pages_per_part, count)
        original = pdf._outlines_data
        try:
            pdf._outlines_data = _range_outlines(snapshot, start, stop)
            result = pdf.extract_pages(range(start, stop))
        finally:
            pdf._outlines_data = original
        try:
            _check_result(result, context="page-count split")
            _refresh_targets(result)
        except BaseException:
            result.dispose()
            raise
        yield result
        del result
