"""Detect visually blank pages through the shared page rasterizer."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aspose_pdf.exceptions import PdfValidationException

from .text_selection import page_indexes

if TYPE_CHECKING:
    from aspose_pdf.document import Document
    from aspose_pdf.pages import Page


@dataclass(frozen=True, slots=True)
class _Options:
    dpi: float
    white_threshold: int
    max_ink_ratio: float
    include_annotations: bool


def _number(value: float, name: str) -> float:
    """Require a finite real number without coercing strings or booleans."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PdfValidationException(f"{name} must be a finite number")
    try:
        number = float(value)
    except OverflowError:
        raise PdfValidationException(f"{name} must be a finite number") from None
    if not math.isfinite(number):
        raise PdfValidationException(f"{name} must be a finite number")
    return number


def _options(
    dpi: float, white_threshold: int, max_ink_ratio: float, include_annotations: bool
) -> _Options:
    """Validate every option before rendering, even for an empty selection."""
    dpi = _number(dpi, "dpi")
    if dpi <= 0 or dpi / 72.0 == 0:
        raise PdfValidationException("dpi must be positive")
    if (
        isinstance(white_threshold, bool)
        or not isinstance(white_threshold, int)
        or not 1 <= white_threshold <= 255
    ):
        raise PdfValidationException("white_threshold must be an integer from 1 to 255")
    max_ink_ratio = _number(max_ink_ratio, "max_ink_ratio")
    if not 0 <= max_ink_ratio <= 1:
        raise PdfValidationException("max_ink_ratio must be between 0 and 1")
    if not isinstance(include_annotations, bool):
        raise PdfValidationException("include_annotations must be a boolean")
    return _Options(dpi, white_threshold, max_ink_ratio, include_annotations)


def _is_blank(page: Page, options: _Options) -> bool:
    """Count pixels with any channel below the white threshold, without copies."""
    raster = page.render(
        dpi=options.dpi,
        background=(255, 255, 255),
        antialias=False,
        draw_annotations=options.include_annotations,
    )
    total = raster.width * raster.height
    ink = 0
    pixels = memoryview(raster.pixels)
    for red, green, blue in zip(pixels[0::3], pixels[1::3], pixels[2::3]):
        if (
            red < options.white_threshold
            or green < options.white_threshold
            or blue < options.white_threshold
        ):
            ink += 1
            # Compare the actual ratio rather than rounding a pixel allowance;
            # a boundary such as 1/49 must include exactly one of 49 pixels.
            if ink / total > options.max_ink_ratio:
                return False
    return True


def is_blank(
    page: Page,
    *,
    dpi: float = 72.0,
    white_threshold: int = 250,
    max_ink_ratio: float = 0.0,
    include_annotations: bool = True,
) -> bool:
    """Inspect one page using validated blank-page detection options."""
    options = _options(dpi, white_threshold, max_ink_ratio, include_annotations)
    return _is_blank(page, options)


def find_blank_pages(
    document: Document,
    *,
    pages: Iterable[int] | None = None,
    dpi: float = 72.0,
    white_threshold: int = 250,
    max_ink_ratio: float = 0.0,
    include_annotations: bool = True,
) -> list[int]:
    """Return unique blank-page indices in selection order, rendering one at a time."""
    options = _options(dpi, white_threshold, max_ink_ratio, include_annotations)
    count = document.page_count
    selection = page_indexes(pages, count, limits=document.load_limits)
    if selection is None:
        selection = range(count)
    document._engine_pdf._load_budget.check(
        len(selection), "max_container_items", "blank-page detection selection"
    )
    seen: set[int] = set()
    found: list[int] = []
    for index in selection:
        if index not in seen:
            seen.add(index)
            if _is_blank(document.pages[index], options):
                found.append(index)
    return found
