"""Package rendered page views as bounded, deterministic image archives."""

from __future__ import annotations

import json
import math
import zipfile

from aspose_pdf.exceptions import PdfValidationException, UnsupportedFeatureException
from aspose_pdf.load_limits import _LoadBudget

from .attachment_zip import _BoundedBuffer, _write_entry
from .blank_pages import _number
from .contact_sheet import _integer, _rgb
from .rasterizer import _page_view, _raster_mode
from .text_selection import page_indexes


def _dimensions(document, index, dpi):
    """Match the renderer's effective CropBox, rotation and pixel rounding."""
    _, crop, rotation = _page_view(document._engine_pdf, index)
    width, height = max(1e-6, crop[2] - crop[0]), max(1e-6, crop[3] - crop[1])
    if rotation in (90, 270):
        width, height = height, width
    width, height = width * (dpi / 72.0), height * (dpi / 72.0)
    if not all(math.isfinite(value) and value > 0 for value in (width, height)):
        raise PdfValidationException(
            "page dimensions produce invalid page ZIP geometry"
        )
    return max(1, math.ceil(width)), max(1, math.ceil(height))


def _manifest(records, image_format, mode, dpi, budget, total):
    """Serialize small provenance records incrementally under the byte policy."""
    manifest = dict(
        schema_version=1, image_format=image_format, mode=mode, dpi=dpi, pages=records
    )
    with _BoundedBuffer(
        budget, "max_object_bytes", "page ZIP manifest bytes", decoded_prefix=total
    ) as output:
        for chunk in json.JSONEncoder(separators=(",", ":")).iterencode(manifest):
            output.write(chunk.encode("ascii"))
        return output.getvalue()


def export_zip(
    document,
    *,
    pages=None,
    image_format="png",
    dpi=72.0,
    background=(255, 255, 255),
    antialias=True,
    include_annotations=True,
    mode="rgb",
    threshold=128,
    dither=False,
    quality=85,
    lossless=False,
    method=4,
    compression="deflate",
    compresslevel=6,
    include_manifest=False,
):
    """Validate the whole selection, then render and encode one page at a time."""
    if not isinstance(image_format, str):
        raise PdfValidationException("image_format must be 'png', 'jpeg' or 'webp'")
    image_format = image_format.strip().lower().lstrip(".")
    image_format = {"jpg": "jpeg"}.get(image_format, image_format)
    if image_format not in {"png", "jpeg", "webp"}:
        raise PdfValidationException("image_format must be 'png', 'jpeg' or 'webp'")
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
    for name, value in (
        ("include_annotations", include_annotations),
        ("dither", dither),
        ("lossless", lossless),
        ("include_manifest", include_manifest),
    ):
        if not isinstance(value, bool):
            raise PdfValidationException(f"{name} must be a boolean")
    if not isinstance(mode, str):
        raise PdfValidationException("mode must be a raster color-mode string")
    mode = _raster_mode(mode)
    threshold = _integer(threshold, "threshold", 0)
    if threshold > 255:
        raise PdfValidationException("threshold must be an integer from 0 to 255")
    quality_value = _number(quality, "quality")
    if not 0 <= quality_value <= 100:
        raise PdfValidationException("quality must be between 0 and 100")
    if image_format == "jpeg":
        _integer(quality, "JPEG quality", 1)
        if mode == "bilevel":
            raise PdfValidationException(
                "JPEG has no bilevel mode; use gray or PNG/WebP"
            )
    method = _integer(method, "method", 0)
    if method > 6:
        raise PdfValidationException("method must be an integer from 0 to 6")
    if not isinstance(compression, str) or compression.strip().lower() not in {
        "deflate",
        "stored",
    }:
        raise PdfValidationException("compression must be 'deflate' or 'stored'")
    compresslevel = _integer(compresslevel, "compresslevel", 0)
    if compresslevel > 9:
        raise PdfValidationException("compresslevel must be an integer from 0 to 9")
    zip_method = (
        zipfile.ZIP_DEFLATED
        if compression.strip().lower() == "deflate"
        else zipfile.ZIP_STORED
    )
    budget = _LoadBudget(document.load_limits)
    selection = page_indexes(pages, document.page_count, limits=budget.limits)
    selection = range(document.page_count) if selection is None else selection
    budget.check_pages(len(selection))
    budget.check(
        len(selection) + int(include_manifest),
        "max_container_items",
        "page ZIP entries",
    )
    plan = []
    for index in selection:
        width, height = _dimensions(document, index, dpi)
        dimension_limit = {"png": 2**31 - 1, "jpeg": 65535, "webp": 16383}[image_format]
        if max(width, height) > dimension_limit:
            raise PdfValidationException(
                f"{image_format.upper()} dimensions must be from 1 to {dimension_limit}"
            )
        budget.check_raster_pixels(
            width * factor, height * factor, "page ZIP supersampled raster"
        )
        budget.check_image_pixels(width, height, "page ZIP image")
        pixels = width * height
        for limit in (
            "max_object_bytes",
            "max_decoded_stream_bytes",
            "max_total_decoded_bytes",
        ):
            budget.check(pixels * 3, limit, "page ZIP RGB raster bytes")
        # Include color conversion, encoder buffers and codec state; the
        # renderer separately bounds its supersampled working canvas.
        work = pixels * (35 if image_format == "webp" else 16) + 512 * 1024
        if dither and mode == "bilevel":
            work += width * 32
        budget.check(work, "max_codec_work_bytes", "page ZIP encoding buffers")
        plan.append((index, width, height))
    if plan and image_format == "webp":
        try:
            from PIL import features
        except ImportError:
            raise UnsupportedFeatureException(
                "WebP export requires Pillow; install the 'images' extra."
            ) from None
        if not features.check("webp"):
            raise UnsupportedFeatureException(
                "WebP export requires a Pillow build with WebP support."
            )
    extension = "jpg" if image_format == "jpeg" else image_format
    records = []
    total = 0
    with _BoundedBuffer(
        budget, "max_codec_work_bytes", "page ZIP output bytes"
    ) as output:
        with zipfile.ZipFile(
            output, "w", compression=zip_method, compresslevel=compresslevel
        ) as archive:
            for number, (index, width, height) in enumerate(plan, 1):
                raster = document.render_page(
                    index,
                    dpi=dpi,
                    background=background,
                    antialias=antialias,
                    draw_annotations=include_annotations,
                )
                if image_format == "png":
                    data = raster.to_png(mode=mode, threshold=threshold, dither=dither)
                elif image_format == "jpeg":
                    data = raster.to_jpeg(quality=quality, mode=mode)
                else:
                    data = raster.to_webp(
                        quality=quality_value,
                        lossless=lossless,
                        method=method,
                        mode=mode,
                        threshold=threshold,
                        dither=dither,
                    )
                for limit in ("max_object_bytes", "max_decoded_stream_bytes"):
                    budget.check(len(data), limit, "page ZIP encoded image bytes")
                total += len(data)
                budget.check(
                    total, "max_total_decoded_bytes", "page ZIP selected files"
                )
                name = f"page-{number:04d}.{extension}"
                _write_entry(archive, name, data, zip_method, compresslevel)
                if include_manifest:
                    records.append(
                        dict(
                            archive_name=name,
                            page_index=index,
                            width=width,
                            height=height,
                            size=len(data),
                        )
                    )
                del raster, data
            if include_manifest:
                data = _manifest(records, image_format, mode, dpi, budget, total)
                _write_entry(archive, "manifest.json", data, zip_method, compresslevel)
        return output.getvalue()
