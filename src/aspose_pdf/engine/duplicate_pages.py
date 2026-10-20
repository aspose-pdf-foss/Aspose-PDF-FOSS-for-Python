"""Group identical rendered page views without retaining all page rasters."""

from __future__ import annotations

from collections.abc import Iterable
from hashlib import sha256
from typing import TYPE_CHECKING

from .blank_pages import _Options, _options, _render
from .text_selection import page_indexes

if TYPE_CHECKING:
    from aspose_pdf.document import Document

    from .rasterizer import RasterizedPage


def _matches(
    document: Document, index: int, raster: RasterizedPage, options: _Options
) -> bool:
    """Confirm a hash candidate against a fresh reference raster, without copies."""
    reference = _render(document.pages[index], options)
    return (
        reference.width == raster.width
        and reference.height == raster.height
        and reference.pixels == raster.pixels
    )


def find_duplicate_pages(
    document: Document,
    *,
    pages: Iterable[int] | None = None,
    dpi: float = 72.0,
    include_annotations: bool = True,
) -> list[list[int]]:
    """Return groups in first-occurrence order, checking pixels after hash matches."""
    options = _options(dpi, 250, 0.0, include_annotations)
    count = document.page_count
    selection = page_indexes(pages, count, limits=document.load_limits)
    if selection is None:
        selection = range(count)
    document._engine_pdf._load_budget.check(
        len(selection), "max_container_items", "duplicate-page detection selection"
    )
    indexes = range(count) if pages is None else tuple(dict.fromkeys(selection))
    if len(indexes) < 2:
        return []
    buckets: dict[tuple[int, int, bytes], list[list[int]]] = {}
    groups: list[list[int]] = []
    for index in indexes:
        raster = _render(document.pages[index], options)
        key = (raster.width, raster.height, sha256(raster.pixels).digest())
        candidates = buckets.setdefault(key, [])
        for group in candidates:
            # Hashes select candidates only. A collision must never merge
            # pages whose actual pixels differ, even within the same bucket.
            if _matches(document, group[0], raster, options):
                group.append(index)
                break
        else:
            group = [index]
            groups.append(group)
            candidates.append(group)
        # A subsequent render must not retain this page's pixel buffer.
        del raster
    return [group for group in groups if len(group) > 1]
