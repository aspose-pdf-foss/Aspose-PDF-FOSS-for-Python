"""Measure visible ink in page coordinates without changing any page boxes."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from aspose_pdf.load_limits import _LoadBudget

from .auto_crop import _ink_bounds, _page_box
from .blank_pages import _Options, _options, _render
from .rasterizer import _page_view
from .text_selection import page_indexes

if TYPE_CHECKING:
    from aspose_pdf.document import Document
    from aspose_pdf.pages import Page

Bounds = tuple[float, float, float, float]


def _measure(page: Page, options: _Options) -> Bounds | None:
    """Release each raster before returning a detached page-coordinate tuple."""
    raster = _render(page, options)
    bounds = _ink_bounds(raster, options.white_threshold)
    if bounds is None:
        return None
    # Read exactly the same normalized, inherited geometry as the renderer,
    # including its fallback for degenerate page boxes.
    _, crop, rotation = _page_view(page._document._engine_pdf, page.index)
    if bounds == (0, 0, raster.width, raster.height):
        return crop
    return _page_box(bounds, crop, rotation, options.dpi, 0.0)


def page_bounds(
    page: Page,
    *,
    dpi: float = 72.0,
    white_threshold: int = 250,
    include_annotations: bool = True,
) -> Bounds | None:
    """Validate inspection options before measuring the visible page."""
    options = _options(dpi, white_threshold, 0.0, include_annotations)
    return _measure(page, options)


def document_bounds(
    document: Document,
    *,
    pages: Iterable[int] | None = None,
    dpi: float = 72.0,
    white_threshold: int = 250,
    include_annotations: bool = True,
) -> dict[int, Bounds | None]:
    """Measure unique selected pages in order, retaining blank results as None."""
    options = _options(dpi, white_threshold, 0.0, include_annotations)
    budget = _LoadBudget(document.load_limits)
    selection = page_indexes(pages, document.page_count, limits=budget.limits)
    selection = range(document.page_count) if selection is None else selection
    budget.check(len(selection), "max_container_items", "content bounds page selection")
    budget.check_pages(len(selection))
    results = {}
    for index in selection:
        if index not in results:
            results[index] = _measure(document.pages[index], options)
    return results
