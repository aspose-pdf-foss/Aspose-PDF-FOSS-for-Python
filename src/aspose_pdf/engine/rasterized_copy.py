"""Build a fresh image-only PDF from rendered views of selected pages."""

from __future__ import annotations

import math
import zlib
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING

from aspose_pdf.exceptions import PdfValidationException
from aspose_pdf.load_limits import _LoadBudget

from .blank_pages import _number
from .contact_sheet import _integer, _rgb
from .content_authoring import AuthoredImage, build_image_stream
from .rasterizer import RasterizedPage, _page_view
from .text_selection import page_indexes

if TYPE_CHECKING:
    from aspose_pdf.document import Document

    from .simple_pdf import SimplePdf


def _geometry(document: Document, index: int, dpi: float):
    """Use the renderer's visible dimensions, minimum extents and pixel rounding."""
    _, crop, rotation = _page_view(document._engine_pdf, index)
    width, height = max(1e-6, crop[2] - crop[0]), max(1e-6, crop[3] - crop[1])
    if rotation in (90, 270):
        width, height = height, width
    scaled_width, scaled_height = width * (dpi / 72.0), height * (dpi / 72.0)
    if not all(
        math.isfinite(value) and value > 0
        for value in (width, height, scaled_width, scaled_height)
    ):
        raise PdfValidationException(
            "page dimensions produce invalid rasterized-copy geometry"
        )
    return width, height, math.ceil(scaled_width), math.ceil(scaled_height)


def _append_image(
    pdf: SimplePdf, raster: RasterizedPage, width: float, height: float, content: bytes
) -> None:
    """Register lossless samples directly, avoiding a PNG encode/decode round trip."""
    compressed = zlib.compress(raster.pixels)
    ratio = pdf._load_limits.max_compression_ratio
    use_flate = len(compressed) < len(raster.pixels) and (
        ratio is None or len(raster.pixels) <= len(compressed) * ratio
    )
    image = AuthoredImage(
        stream_data=compressed if use_flate else raster.pixels,
        decoded_data=raster.pixels,
        width=raster.width,
        height=raster.height,
        bits_per_component=8,
        color_space="DeviceRGB",
        components=3,
        filter_name="FlateDecode" if use_flate else None,
    )
    index = len(pdf.pages)
    pdf.add(((0.0, 0.0, width, height), content))
    _, key = pdf._register_page_image(index, image, "Im1")
    pdf._image_matrix_map[(index, key)] = (width, 0.0, 0.0, height, 0.0, 0.0)
    pdf._image_rect_map[(index, key)] = (0.0, 0.0, width, height)


def rasterized_copy(
    document: Document,
    *,
    pages: Iterable[int] | None = None,
    dpi: float = 150.0,
    background: Sequence[int] = (255, 255, 255),
    antialias: bool | int = True,
    include_annotations: bool = True,
) -> Document:
    """Preflight every selected output, then render and store one page at a time."""
    from aspose_pdf.document import Document

    dpi = _number(dpi, "dpi")
    if dpi <= 0 or dpi / 72.0 == 0:
        raise PdfValidationException("dpi must be positive")
    background = _rgb(background, "background")
    if isinstance(antialias, bool):
        factor = 3 if antialias else 1
    else:
        factor = _integer(antialias, "antialias", 1)
        if factor > 8:
            raise PdfValidationException("antialias must be a factor between 1 and 8")
    if not isinstance(include_annotations, bool):
        raise PdfValidationException("include_annotations must be a boolean")
    budget = _LoadBudget(document.load_limits)
    selection = page_indexes(pages, document.page_count, limits=budget.limits)
    selection = range(document.page_count) if selection is None else selection
    budget.check_pages(len(selection))
    budget.check(len(selection), "max_container_items", "rasterized-copy pages")
    # Catalog and Pages root, followed by Page, Contents and Image per result.
    objects = 2 + 3 * len(selection)
    budget.check_objects(objects)
    budget.check_object_id(objects)
    plans = []
    retained = 0
    for index in selection:
        width, height, pixels_w, pixels_h = _geometry(document, index, dpi)
        budget.check_raster_pixels(
            pixels_w * factor, pixels_h * factor, "rasterized-copy supersampled page"
        )
        budget.check_image_pixels(pixels_w, pixels_h, "rasterized-copy image")
        samples = pixels_w * pixels_h * 3
        budget.check(samples, "max_object_bytes", "rasterized-copy RGB samples")
        budget.check(samples, "max_decoded_stream_bytes", "rasterized-copy RGB samples")
        budget.check(
            samples * 2 + 512 * 1024,
            "max_codec_work_bytes",
            "rasterized-copy compression buffers",
        )
        content = build_image_stream("Im1", 0, 0, width, height)
        for limit in (
            "max_object_bytes",
            "max_content_stream_bytes",
            "max_decoded_stream_bytes",
        ):
            budget.check(len(content), limit, "rasterized-copy page content")
        retained += samples + len(content)
        budget.check(
            retained,
            "max_total_decoded_bytes",
            "rasterized-copy retained samples and content",
        )
        plans.append((index, width, height, content))
    result = Document(limits=document.load_limits)
    try:
        result._engine_pdf._ensure_cos()
        for index, width, height, content in plans:
            raster = document.render_page(
                index,
                dpi=dpi,
                background=background,
                antialias=antialias,
                draw_annotations=include_annotations,
            )
            _append_image(result._engine_pdf, raster, width, height, content)
            del raster
    except BaseException:
        result.dispose()
        raise
    return result
