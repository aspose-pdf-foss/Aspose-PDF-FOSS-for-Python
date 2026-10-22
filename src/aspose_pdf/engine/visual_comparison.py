"""Compare bounded page rasters without modifying PDFs or retaining all rasters."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from itertools import zip_longest
from typing import TYPE_CHECKING

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import PdfLoadLimits, _combined_limits, _LoadBudget
from aspose_pdf.visual_comparison import PageVisualComparison

from .blank_pages import _number, _options, _render
from .rasterizer import RasterizedPage, _page_view
from .text_selection import page_indexes

if TYPE_CHECKING:
    from aspose_pdf.document import Document


def _regions(
    regions: Iterable[Sequence[float]] | None, budget: _LoadBudget
) -> tuple[tuple[float, float, float, float], ...]:
    """Snapshot bounded rectangles before reading geometry or rendering pages."""
    if regions is None:
        return ()
    if isinstance(regions, (str, bytes, bytearray, Mapping)):
        raise PdfValidationException("ignore_regions must be an iterable of rectangles")
    try:
        iterator = iter(regions)
    except TypeError:
        raise PdfValidationException(
            "ignore_regions must be an iterable of rectangles"
        ) from None
    result = []
    for position, region in enumerate(iterator, 1):
        budget.check(
            position, "max_container_items", "visual comparison ignore regions"
        )
        if (
            not isinstance(region, Sequence)
            or isinstance(region, (str, bytes, bytearray))
            or len(region) != 4
        ):
            raise PdfValidationException(
                "Each ignore_regions rectangle must contain four coordinates"
            )
        box = tuple(_number(value, "ignore_regions coordinate") for value in region)
        if box[2] <= box[0] or box[3] <= box[1]:
            raise PdfValidationException(
                "ignore_regions rectangles must have ordered corners and positive area"
            )
        result.append(box)
    return tuple(result)


def _pixel_regions(regions, width: int, height: int, dpi: float):
    """Round outward in displayed points and clip before converting to integers."""
    scale = dpi / 72.0
    result = []
    for x0, y0, x1, y1 in regions:
        # Finite coordinates can overflow when scaled; clamp infinities before
        # floor/ceil so enormous off-canvas rectangles remain safe to ignore.
        left = math.floor(min(width, max(0, x0 * scale)))
        top = math.floor(min(height, max(0, y0 * scale)))
        right = math.ceil(min(width, max(0, x1 * scale)))
        bottom = math.ceil(min(height, max(0, y1 * scale)))
        if right > left and bottom > top:
            result.append((left, top, right, bottom))
    return tuple(result)


def _row_spans(regions, height: int):
    """Yield merged exclusion spans per row without allocating a pixel mask."""
    events = {}
    for number, (left, top, right, bottom) in enumerate(regions):
        events.setdefault(top, []).append((number, (left, right)))
        events.setdefault(bottom, []).append((number, None))
    active = {}
    spans = []
    for y in range(height):
        if y in events:
            for number, span in events[y]:
                if span is None:
                    active.pop(number)
                else:
                    active[number] = span
            spans = []
            for left, right in sorted(active.values()):
                if spans and left <= spans[-1][1]:
                    spans[-1] = (spans[-1][0], max(right, spans[-1][1]))
                else:
                    spans.append((left, right))
        yield spans


def _size(document: Document, index: int | None, dpi: float) -> tuple[int, int] | None:
    """Preflight the same visible geometry and pixel rounding as the rasterizer."""
    if index is None:
        return None
    _, crop, rotation = _page_view(document._engine_pdf, index)
    width = max(1e-6, crop[2] - crop[0])
    height = max(1e-6, crop[3] - crop[1])
    if rotation in (90, 270):
        width, height = height, width
    width, height = width * (dpi / 72.0), height * (dpi / 72.0)
    if (
        not math.isfinite(width)
        or not math.isfinite(height)
        or width <= 0
        or height <= 0
    ):
        raise PdfValidationException(
            "page dimensions produce invalid comparison geometry"
        )
    return max(1, math.ceil(width)), max(1, math.ceil(height))


def _compare(
    left: RasterizedPage | None,
    right: RasterizedPage | None,
    page_index: int | None,
    other_page_index: int | None,
    tolerance: int,
    max_diff_ratio: float,
    include_diff_images: bool,
    dpi: float,
    limits: PdfLoadLimits,
    *,
    ignore_regions: tuple[tuple[int, int, int, int], ...] = (),
) -> PageVisualComparison:
    """Count changed pixels and optionally draw their mask over pale page context."""
    lw, lh = (left.width, left.height) if left else (0, 0)
    rw, rh = (right.width, right.height) if right else (0, 0)
    width, height = max(lw, rw), max(lh, rh)
    total = lw * lh + rw * rh - min(lw, rw) * min(lh, rh)
    size = (lw, lh) if left else None
    other_size = (rw, rh) if right else None
    identical = (
        left is not None
        and right is not None
        and size == other_size
        and left.pixels == right.pixels
    )
    if identical and not include_diff_images:
        ignored = (
            sum(
                right - left
                for spans in _row_spans(ignore_regions, height)
                for left, right in spans
            )
            if ignore_regions
            else 0
        )
        return PageVisualComparison(
            page_index,
            other_page_index,
            size,
            other_size,
            0,
            total - ignored,
            None,
            True,
            ignored_pixels=ignored,
        )
    canvas = bytearray(b"\xff") * (width * height * 3) if include_diff_images else None
    left_pixels = memoryview(left.pixels) if left else None
    right_pixels = memoryview(right.pixels) if right else None
    changed = 0
    ignored = 0
    x0, y0, x1, y1 = width, height, 0, 0
    for y, spans in enumerate(_row_spans(ignore_regions, height)):
        # The bottom-right corner can be outside both differently sized pages.
        row_width = max(lw if y < lh else 0, rw if y < rh else 0)
        ignored += sum(max(0, min(right, row_width) - left) for left, right in spans)
        span_index = 0
        for x in range(row_width):
            while span_index < len(spans) and x >= spans[span_index][1]:
                span_index += 1
            if span_index < len(spans) and x >= spans[span_index][0]:
                if canvas is not None:
                    offset = (y * width + x) * 3
                    canvas[offset : offset + 3] = b"\xc0\xe0\xff"
                continue
            has_left, has_right = x < lw and y < lh, x < rw and y < rh
            a, b = (y * lw + x) * 3, (y * rw + x) * 3
            different = has_left != has_right
            if has_left and has_right and not identical:
                different = any(
                    abs(left_pixels[a + channel] - right_pixels[b + channel])
                    > tolerance
                    for channel in range(3)
                )
            if different:
                changed += 1
                x0, y0, x1, y1 = min(x0, x), min(y0, y), max(x1, x + 1), max(y1, y + 1)
            if canvas is not None:
                offset = (y * width + x) * 3
                if different:
                    canvas[offset : offset + 3] = b"\xff\x00\x00"
                else:
                    pixels, start = (right_pixels, b) if has_right else (left_pixels, a)
                    gray = (
                        pixels[start] * 77
                        + pixels[start + 1] * 150
                        + pixels[start + 2] * 29
                    ) >> 8
                    shade = 192 + (gray * 63 + 127) // 255
                    canvas[offset : offset + 3] = bytes((shade,)) * 3
    image = (
        RasterizedPage(width, height, bytes(canvas), dpi=dpi, _limits=limits)
        if canvas is not None
        else None
    )
    total -= ignored
    matches = (
        size is not None
        and size == other_size
        and (changed / total if total else 0.0) <= max_diff_ratio
    )
    return PageVisualComparison(
        page_index,
        other_page_index,
        size,
        other_size,
        changed,
        total,
        (x0, y0, x1, y1) if changed else None,
        matches,
        image,
        ignored_pixels=ignored,
    )


def compare_visual(
    document: Document,
    other: Document,
    *,
    pages: Iterable[int] | None = None,
    other_pages: Iterable[int] | None = None,
    dpi: float = 72.0,
    tolerance: int = 0,
    max_diff_ratio: float = 0.0,
    include_annotations: bool = True,
    include_diff_images: bool = False,
    ignore_regions: Iterable[Sequence[float]] | None = None,
) -> list[PageVisualComparison]:
    """Validate both selections and all raster budgets before comparing page pairs."""
    options = _options(dpi, 250, 0.0, include_annotations)
    if (
        isinstance(tolerance, bool)
        or not isinstance(tolerance, int)
        or not 0 <= tolerance <= 255
    ):
        raise PdfValidationException("tolerance must be an integer from 0 to 255")
    max_diff_ratio = _number(max_diff_ratio, "max_diff_ratio")
    if not 0 <= max_diff_ratio <= 1:
        raise PdfValidationException("max_diff_ratio must be between 0 and 1")
    if not isinstance(include_diff_images, bool):
        raise PdfValidationException("include_diff_images must be a boolean")
    limits = _combined_limits(document.load_limits, other.load_limits)
    budget = _LoadBudget(limits)
    regions = _regions(ignore_regions, budget)
    selection = page_indexes(pages, document.page_count, limits=limits)
    other_selection = page_indexes(other_pages, other.page_count, limits=limits)
    selection = range(document.page_count) if selection is None else selection
    other_selection = (
        range(other.page_count) if other_selection is None else other_selection
    )
    budget.check(
        max(len(selection), len(other_selection)),
        "max_container_items",
        "visual comparison page pairs",
    )
    plans = []
    retained_pixels = 0
    for index, other_index in zip_longest(selection, other_selection):
        size = _size(document, index, options.dpi)
        other_size = _size(other, other_index, options.dpi)
        width = max(size[0] if size else 0, other_size[0] if other_size else 0)
        height = max(size[1] if size else 0, other_size[1] if other_size else 0)
        budget.check_raster_pixels(width, height, "visual comparison canvas")
        if include_diff_images:
            retained_pixels += width * height
            budget.check_raster_pixels(
                retained_pixels, 1, "retained visual comparison images"
            )
        plans.append((index, other_index))
    results = []
    for index, other_index in plans:
        left = _render(document.pages[index], options) if index is not None else None
        right = (
            _render(other.pages[other_index], options)
            if other_index is not None
            else None
        )
        results.append(
            _compare(
                left,
                right,
                index,
                other_index,
                tolerance,
                max_diff_ratio,
                include_diff_images,
                options.dpi,
                limits,
                ignore_regions=_pixel_regions(
                    regions,
                    max(left.width if left else 0, right.width if right else 0),
                    max(left.height if left else 0, right.height if right else 0),
                    options.dpi,
                ),
            )
        )
        del left, right
    return results
