"""Trim white margins by mapping rendered ink bounds back to PDF coordinates."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from aspose_pdf.exceptions import PdfValidationException

from .blank_pages import _number, _options, _render

if TYPE_CHECKING:
    from aspose_pdf.engine.rasterizer import RasterizedPage
    from aspose_pdf.pages import Page


def _ink_bounds(
    raster: RasterizedPage, threshold: int
) -> tuple[int, int, int, int] | None:
    """Return outer pixel edges around all ink, using strided views without copies."""
    left, top, right, bottom = raster.width, raster.height, 0, 0
    pixels = memoryview(raster.pixels)
    for index, (red, green, blue) in enumerate(
        zip(pixels[0::3], pixels[1::3], pixels[2::3])
    ):
        if red < threshold or green < threshold or blue < threshold:
            row, column = divmod(index, raster.width)
            left = min(left, column)
            right = max(right, column + 1)
            top = min(top, row)
            bottom = row + 1
    return None if right == 0 else (left, top, right, bottom)


def _page_box(
    bounds: tuple[int, int, int, int],
    crop: tuple[float, float, float, float],
    rotation: int,
    dpi: float,
    margin: float,
) -> tuple[float, float, float, float]:
    """Undo the rasterizer's rotation and scale at pixel edges, then clamp padding."""
    x0, y0, x1, y1 = crop
    # Match the renderer's minimum geometry for submicropoint page dimensions.
    width, height = max(1e-6, x1 - x0), max(1e-6, y1 - y0)
    scale = dpi / 72.0

    def to_page(x: int, y: int) -> tuple[float, float]:
        dx, dy = x / scale, y / scale
        if rotation == 90:
            return x0 + dy, y0 + dx
        if rotation == 180:
            return x0 + (width - dx), y0 + dy
        if rotation == 270:
            return x0 + (width - dy), y0 + (height - dx)
        return x0 + dx, y0 + (height - dy)

    left, top, right, bottom = bounds
    corners = [to_page(x, y) for x in (left, right) for y in (top, bottom)]
    xs, ys = zip(*corners)
    result = (
        max(x0, min(xs) - margin),
        max(y0, min(ys) - margin),
        min(x1, max(xs) + margin),
        min(y1, max(ys) + margin),
    )
    if (
        not all(math.isfinite(value) for value in result)
        or result[2] <= result[0]
        or result[3] <= result[1]
    ):
        raise PdfValidationException("Rendered ink bounds do not form a valid CropBox")
    return result


def auto_crop(
    page: Page,
    margin: float = 0.0,
    *,
    dpi: float = 72.0,
    white_threshold: int = 250,
    include_annotations: bool = True,
) -> None:
    """Set only the CropBox after rendering and validating the complete result."""
    margin = _number(margin, "margin")
    if margin < 0:
        raise PdfValidationException("margin must be nonnegative")
    options = _options(dpi, white_threshold, 0.0, include_annotations)
    raster = _render(page, options)
    bounds = _ink_bounds(raster, options.white_threshold)
    if bounds is None or bounds == (0, 0, raster.width, raster.height):
        return
    # The render has already validated the geometry. Order the public box in
    # the same way, including the renderer's fallback for a zero-area sheet.
    a, b, c, d = (float(value) for value in page.crop_box)
    crop = (min(a, c), min(b, d), max(a, c), max(b, d))
    if crop[0] == crop[2] or crop[1] == crop[3]:
        crop = (0.0, 0.0, 612.0, 792.0)
    result = _page_box(bounds, crop, page.rotation, options.dpi, margin)
    if result != crop:
        page.crop_box = result
