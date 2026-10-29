"""Trim white margins by mapping rendered ink bounds back to PDF coordinates."""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import TYPE_CHECKING

from aspose_pdf.exceptions import PdfValidationException

from .blank_pages import _number, _options, _render

if TYPE_CHECKING:
    from aspose_pdf.document import Document
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


def auto_crop_document(
    document: Document,
    margin: float = 0.0,
    *,
    pages: Iterable[int] | None = None,
    uniform: bool = False,
    dpi: float = 72.0,
    white_threshold: int = 250,
    include_annotations: bool = True,
    dry_run: bool = False,
) -> dict[int, tuple[float, float, float, float]]:
    """Plan every selected CropBox before applying individual or shared crops."""
    from .rasterizer import _page_view
    from .text_selection import page_indexes

    margin = _number(margin, "margin")
    if margin < 0:
        raise PdfValidationException("margin must be nonnegative")
    options = _options(dpi, white_threshold, 0.0, include_annotations)
    if not isinstance(uniform, bool) or not isinstance(dry_run, bool):
        raise PdfValidationException("uniform and dry_run must be booleans")
    pdf = document._engine_pdf
    selection = page_indexes(pages, document.page_count, limits=document.load_limits)
    selection = range(document.page_count) if selection is None else selection
    pdf._load_budget.check(len(selection), "max_container_items", "auto-crop selection")
    pdf._load_budget.check_pages(len(selection))
    views = {}
    shared_size = None
    for index in selection:
        if index in views:
            continue
        _, crop, rotation = _page_view(pdf, index)
        width, height = crop[2] - crop[0], crop[3] - crop[1]
        if not all(math.isfinite(v) and v > 0 for v in (width, height)):
            raise PdfValidationException("Invalid page dimensions for auto-crop")
        size = (height, width) if rotation in (90, 270) else (width, height)
        if uniform and shared_size is not None and size != shared_size:
            raise PdfValidationException(
                "uniform cropping requires equal displayed page dimensions"
            )
        shared_size = size
        views[index] = crop, rotation

    planned = {}
    shared_bounds = None
    canvas = None
    for index, (crop, rotation) in views.items():
        raster = _render(document.pages[index], options)
        bounds = _ink_bounds(raster, options.white_threshold)
        canvas = (0, 0, raster.width, raster.height)
        # Keep only small geometry tuples between renders, never page rasters.
        del raster
        if bounds is None:
            continue
        if uniform:
            if shared_bounds is None:
                shared_bounds = bounds
            else:
                shared_bounds = (
                    min(shared_bounds[0], bounds[0]),
                    min(shared_bounds[1], bounds[1]),
                    max(shared_bounds[2], bounds[2]),
                    max(shared_bounds[3], bounds[3]),
                )
        elif bounds != canvas:
            result = _page_box(bounds, crop, rotation, options.dpi, margin)
            if result != crop:
                planned[index] = result
    if uniform and shared_bounds is not None and shared_bounds != canvas:
        for index, (crop, rotation) in views.items():
            result = _page_box(shared_bounds, crop, rotation, options.dpi, margin)
            if result != crop:
                planned[index] = result
    if not dry_run:
        for index, crop in planned.items():
            document.pages[index].crop_box = crop
    return planned
