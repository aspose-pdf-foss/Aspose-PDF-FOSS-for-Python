"""Find visually blank separators before importing independent page ranges."""

from __future__ import annotations

from collections.abc import Iterator
from itertools import pairwise
from typing import TYPE_CHECKING

from aspose_pdf.exceptions import PdfValidationException

from .blank_pages import _is_blank, _options
from .bookmark_split import _range_outlines, _snapshot
from .text_split import _check_result, _refresh_targets

if TYPE_CHECKING:
    from aspose_pdf.document import Document

    from .simple_pdf import SimplePdf


def split_by_blank_pages(
    document: Document,
    *,
    keep_separators: bool = False,
    dpi: float = 72.0,
    white_threshold: int = 250,
    max_ink_ratio: float = 0.0,
    include_annotations: bool = True,
) -> Iterator[SimplePdf]:
    """Plan every range before importing, without changing source outline edits."""
    options = _options(dpi, white_threshold, max_ink_ratio, include_annotations)
    if not isinstance(keep_separators, bool):
        raise PdfValidationException("keep_separators must be a boolean")
    pdf = document._engine_pdf
    count = document.page_count
    pdf._load_budget.check_pages(count)
    pdf._load_budget.check(count, "max_container_items", "blank-page split pages")
    ranges = []
    start = None
    for index in range(count):
        if _is_blank(document.pages[index], options):
            if start is not None:
                ranges.append((start, index))
                start = None
        elif start is None:
            start = index
    if start is not None:
        ranges.append((start, count))
    if keep_separators and count:
        # Blank runs terminate the preceding part. A leading run stays with
        # the first content block, and an entirely blank input stays in one part.
        starts = [0, *(start for start, _ in ranges[1:]), count]
        ranges = list(pairwise(starts))
    if not ranges:
        return
    roots = (
        pdf._outlines_data if document._outlines is None else document._outlines._items
    )
    snapshot, _starts = _snapshot(pdf, roots, 1)
    for start, stop in ranges:
        original = pdf._outlines_data
        try:
            pdf._outlines_data = _range_outlines(snapshot, start, stop)
            result = pdf.extract_pages(range(start, stop))
        finally:
            pdf._outlines_data = original
        try:
            _check_result(result, context="blank-page split")
            _refresh_targets(result)
        except BaseException:
            result.dispose()
            raise
        yield result
