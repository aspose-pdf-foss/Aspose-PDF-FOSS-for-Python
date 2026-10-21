"""Detached page-pair results from visual PDF comparison."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aspose_pdf.engine.rasterizer import RasterizedPage

__all__ = ["PageVisualComparison"]


@dataclass(frozen=True, slots=True)
class PageVisualComparison:
    """One pair of rendered pages, using zero-based source page indices.

    A missing page has a ``None`` index and size. Sizes are ``(width, height)``
    in pixels. ``changed_pixels`` counts pixels outside the color tolerance,
    including all pixels covered by only one page. ``total_pixels`` is the
    union of the two page rectangles, aligned at the top-left without scaling.

    ``bbox`` encloses changed pixels as ``(x0, y0, x1, y1)``, with a top-left
    origin and exclusive upper bounds; ``None`` means no changed pixels.
    ``matches`` requires both pages, equal raster sizes, and a difference
    ratio at most the configured allowance. Missing or differently sized pages
    never match, even with a ratio allowance of 1.

    ``diff_image`` is present only when requested. Changed pixels are red,
    unchanged pixels show a pale grayscale page context, and areas outside
    both pages are white. Save it with ``RasterizedPage.save()``. The report
    and image remain usable after either document is closed.
    """

    page_index: int | None
    other_page_index: int | None
    size: tuple[int, int] | None
    other_size: tuple[int, int] | None
    changed_pixels: int
    total_pixels: int
    bbox: tuple[int, int, int, int] | None
    matches: bool
    diff_image: RasterizedPage | None = field(default=None, repr=False, compare=False)

    @property
    def difference_ratio(self) -> float:
        """Return changed pixels divided by pixels covered by either page."""
        return self.changed_pixels / self.total_pixels if self.total_pixels else 0.0
