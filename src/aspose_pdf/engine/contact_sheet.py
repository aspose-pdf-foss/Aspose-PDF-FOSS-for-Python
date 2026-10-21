"""Compose selected PDF pages into a bounded, dependency-free RGB overview."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any

from aspose_pdf.exceptions import PdfValidationException

from .rasterizer import RasterizedPage, _page_view
from .text_selection import page_indexes

if TYPE_CHECKING:
    from aspose_pdf.document import Document


# Five-by-seven decimal digits, expanded to two pixels per dot for legible labels.
_DIGITS = (
    (14, 17, 19, 21, 25, 17, 14),
    (4, 12, 4, 4, 4, 4, 14),
    (14, 17, 1, 2, 4, 8, 31),
    (30, 1, 1, 14, 1, 1, 30),
    (2, 6, 10, 18, 31, 2, 2),
    (31, 16, 16, 30, 1, 1, 30),
    (14, 16, 16, 30, 17, 17, 14),
    (31, 1, 2, 4, 8, 8, 8),
    (14, 17, 17, 14, 17, 17, 14),
    (14, 17, 17, 15, 1, 1, 14),
)
_LABEL_HEIGHT = 22


def _integer(value: int, name: str, minimum: int) -> int:
    """Reject booleans and lossy integer coercion for pixel dimensions."""
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise PdfValidationException(f"{name} must be an integer >= {minimum}")
    return value


def _rgb(value: Sequence[int], name: str) -> tuple[int, int, int]:
    """Require exactly three byte-valued RGB channels."""
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or len(value) != 3
    ):
        raise PdfValidationException(f"{name} must contain three RGB integers (0-255)")
    for channel in value:
        if (
            isinstance(channel, bool)
            or not isinstance(channel, int)
            or not 0 <= channel <= 255
        ):
            raise PdfValidationException(
                f"{name} must contain three RGB integers (0-255)"
            )
    return tuple(value)


def _thumbnail_geometry(
    pdf: Any, index: int, width: int, height: int
) -> tuple[float, int, int]:
    """Fit the renderer's effective CropBox and rotation inside a pixel rectangle."""
    _, crop, rotation = _page_view(pdf, index)
    page_width = max(1e-6, crop[2] - crop[0])
    page_height = max(1e-6, crop[3] - crop[1])
    if rotation in (90, 270):
        page_width, page_height = page_height, page_width
    if not math.isfinite(page_width) or not math.isfinite(page_height):
        raise PdfValidationException(
            "page dimensions produce invalid thumbnail geometry"
        )
    try:
        scale = min(width / page_width, height / page_height)
    except OverflowError:
        raise PdfValidationException(
            "thumbnail_size produces invalid geometry"
        ) from None
    if not math.isfinite(scale) or scale <= 0:
        raise PdfValidationException(
            "page dimensions produce invalid thumbnail geometry"
        )
    # ceil() in the renderer can add a pixel when the fitted product rounds upward.
    if math.ceil(page_width * scale) > width or math.ceil(page_height * scale) > height:
        scale = math.nextafter(scale, 0.0)
    scaled_width, scaled_height = page_width * scale, page_height * scale
    if scale <= 0 or scaled_width <= 0 or scaled_height <= 0:
        raise PdfValidationException(
            "page dimensions produce invalid thumbnail geometry"
        )
    return scale, max(1, math.ceil(scaled_width)), max(1, math.ceil(scaled_height))


def _paste(
    canvas: bytearray, width: int, raster: RasterizedPage, x: int, y: int
) -> None:
    """Copy one RGB thumbnail row at a time without retaining earlier thumbnails."""
    row_bytes = raster.width * 3
    source = memoryview(raster.pixels)
    for row in range(raster.height):
        start = ((y + row) * width + x) * 3
        canvas[start : start + row_bytes] = source[
            row * row_bytes : (row + 1) * row_bytes
        ]


def _label(
    canvas: bytearray, width: int, text: str, x: int, y: int, color: bytes
) -> None:
    """Draw a centered source page number using the built-in decimal bitmap font."""
    for digit_index, digit in enumerate(text):
        for row, bits in enumerate(_DIGITS[int(digit)]):
            for column in range(5):
                if bits & (1 << (4 - column)):
                    for dy in range(2):
                        start = (
                            (y + row * 2 + dy) * width
                            + x
                            + digit_index * 12
                            + column * 2
                        ) * 3
                        canvas[start : start + 6] = color * 2


def render_contact_sheet(
    document: Document,
    *,
    pages: Iterable[int] | None = None,
    columns: int = 4,
    thumbnail_size: tuple[int, int] = (160, 220),
    margin: int = 12,
    spacing: int = 12,
    labels: bool = True,
    background: tuple[int, int, int] = (240, 240, 240),
    page_background: tuple[int, int, int] = (255, 255, 255),
    label_color: tuple[int, int, int] = (0, 0, 0),
    antialias: bool | int = True,
    include_annotations: bool = True,
) -> RasterizedPage:
    """Validate and preflight the whole sheet before allocating or rendering it."""
    columns = _integer(columns, "columns", 1)
    margin = _integer(margin, "margin", 0)
    spacing = _integer(spacing, "spacing", 0)
    if (
        not isinstance(thumbnail_size, Sequence)
        or isinstance(thumbnail_size, (str, bytes, bytearray))
        or len(thumbnail_size) != 2
    ):
        raise PdfValidationException(
            "thumbnail_size must contain two positive integers"
        )
    tw, th = (_integer(value, "thumbnail_size", 1) for value in thumbnail_size)
    for name, value in (
        ("labels", labels),
        ("include_annotations", include_annotations),
    ):
        if not isinstance(value, bool):
            raise PdfValidationException(f"{name} must be a boolean")
    if isinstance(antialias, bool):
        factor = 3 if antialias else 1
    else:
        factor = _integer(antialias, "antialias", 1)
        if factor > 8:
            raise PdfValidationException("antialias must be a factor between 1 and 8")
    background = _rgb(background, "background")
    page_background = _rgb(page_background, "page_background")
    label_color = _rgb(label_color, "label_color")
    pdf = document._engine_pdf
    selection = page_indexes(pages, document.page_count, limits=document.load_limits)
    if selection is None:
        selection = range(document.page_count)
    budget = pdf._load_budget
    budget.check(len(selection), "max_container_items", "contact sheet page selection")
    if not selection:
        raise PdfValidationException("A contact sheet requires at least one page")
    label_height = _LABEL_HEIGHT if labels else 0
    if labels and max(len(str(index + 1)) for index in selection) * 12 - 2 > tw:
        raise PdfValidationException("thumbnail_size is too narrow for page labels")
    columns = min(columns, len(selection))
    rows = (len(selection) + columns - 1) // columns
    width = columns * tw + (columns - 1) * spacing + 2 * margin
    height = rows * (th + label_height) + (rows - 1) * spacing + 2 * margin
    budget.check_raster_pixels(width, height, "contact sheet")
    plans = []
    for index in selection:
        scale, w, h = _thumbnail_geometry(pdf, index, tw, th)
        budget.check_raster_pixels(
            w * factor, h * factor, "contact sheet supersampled thumbnail"
        )
        plans.append((scale, w, h))
    canvas = bytearray(background) * (width * height)
    for position, (index, (scale, w, h)) in enumerate(zip(selection, plans)):
        x = margin + (position % columns) * (tw + spacing)
        y = margin + (position // columns) * (th + label_height + spacing)
        raster = document.render_page(
            index,
            scale=scale,
            background=page_background,
            antialias=antialias,
            draw_annotations=include_annotations,
        )
        _paste(canvas, width, raster, x + (tw - w) // 2, y + (th - h) // 2)
        del raster
        if labels:
            text = str(index + 1)
            _label(
                canvas,
                width,
                text,
                x + (tw - (len(text) * 12 - 2)) // 2,
                y + th + 4,
                bytes(label_color),
            )
    return RasterizedPage(width, height, bytes(canvas), _limits=document.load_limits)
